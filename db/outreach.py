"""When a funnel may write to a client first — the pacing rules behind the
panel's «Avto-xabarlar» page.

The funnel decides *what* to say and *roughly when*; this module is the last
gate before a scheduled touch actually goes out, and it exists because the
failure mode of an automated funnel is not saying the wrong thing — it is
sounding like a notification service: three messages in an evening, one at
06:40, another the morning after the client already answered.

Rules, all operator-editable from the panel and all read live:

* **enabled** — may the funnel write to a client *on its own*? This is off by
  default (the owner's decision, 2026-09-04): a bot that chases people with
  "we don't want to disturb you" messages reads as a spam service, and the
  messages that survived that filter said nothing the client needed. With it
  off, the bot answers when written to and stays quiet otherwise.
* **quiet hours** — nothing outside the working window; a touch due at night is
  moved to the window's opening hour. Applies to operator-scheduled messages
  too: a human asking for a follow-up did not ask for it at 03:00.
* **daily cap** and **minimum gap** — anti-flood limits on automatic touches.
  They do not apply to a message an operator scheduled by hand; that is one
  deliberate act, not a drip campaign.

Three outcomes, not two: "send now", "not now, try again at T", and — only when
the funnel is writing on its own initiative and that is switched off — **drop
it**. Dropping is right there and deferring would be wrong: the touch would sit
in the queue for as long as the switch stays off, which is forever by default.
An operator-scheduled message is never dropped.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time as dtime, timedelta, tzinfo
from typing import Optional

logger = logging.getLogger(__name__)

SETTING_KEY = "outreach"

# Marker in a scheduled task's payload saying a person queued it from the panel.
OPERATOR_SOURCE = "operator"

# The defaults are what the funnel did before this module existed, plus a
# civilised window: the panel starts from them and every field is editable.
DEFAULTS: dict = {
    # Off: the bot never writes first. See the module docstring.
    "enabled": False,
    "quiet_from": 9,        # first hour a touch may be sent (local time)
    "quiet_to": 21,         # first hour it may NOT be sent again
    "daily_cap": 2,         # automatic messages per chat per day
    "min_gap_minutes": 180,  # and never two closer together than this
}

# How far ahead a touch is pushed while outreach is switched off. Short enough
# that turning it back on feels immediate, long enough that a week-long pause
# does not rewrite the queue every few minutes.
PAUSED_RETRY = timedelta(hours=4)


@dataclass(frozen=True)
class OutreachPolicy:
    enabled: bool = False
    quiet_from: int = 9
    quiet_to: int = 21
    daily_cap: int = 2
    min_gap_minutes: int = 180

    @classmethod
    def from_dict(cls, raw: dict) -> "OutreachPolicy":
        def as_int(key: str, lo: int, hi: int) -> int:
            try:
                return max(lo, min(hi, int(raw.get(key, DEFAULTS[key]))))
            except (TypeError, ValueError):
                return int(DEFAULTS[key])

        return cls(
            enabled=bool(raw.get("enabled", False)),
            quiet_from=as_int("quiet_from", 0, 23),
            quiet_to=as_int("quiet_to", 1, 24),
            daily_cap=as_int("daily_cap", 1, 20),
            min_gap_minutes=as_int("min_gap_minutes", 0, 24 * 60),
        )

    def window_contains(self, when: datetime) -> bool:
        """Is `when` inside the sending window? Handles a window that wraps
        midnight (quiet_from 21, quiet_to 6 = evening through early morning)."""
        hour = when.hour
        if self.quiet_from == self.quiet_to:
            return True  # a zero-width window is read as "no restriction"
        if self.quiet_from < self.quiet_to:
            return self.quiet_from <= hour < self.quiet_to
        return hour >= self.quiet_from or hour < self.quiet_to

    def next_window_start(self, after: datetime) -> datetime:
        """The first instant at or after `after` that falls inside the window."""
        if self.window_contains(after):
            return after
        opening = after.replace(
            hour=self.quiet_from % 24, minute=0, second=0, microsecond=0
        )
        if opening <= after:
            opening += timedelta(days=1)
        return opening


@dataclass(frozen=True)
class Decision:
    send: bool
    retry_at: Optional[datetime] = None
    reason: str = ""
    # True = do not try again; the caller should cancel the task outright.
    drop: bool = False


async def load_policy(tenant_id: str) -> OutreachPolicy:
    from db.settings import get_setting

    return OutreachPolicy.from_dict(await get_setting(tenant_id, SETTING_KEY, DEFAULTS))


async def _outbound_history(
    tenant_id: str, chat_id: int, since: datetime
) -> tuple[int, Optional[datetime]]:
    """(how many automatic messages this chat got since `since`, the last one's time).

    Read off `conversation_events`, which already records every out-of-band send
    — so the cap counts what the client actually received, including touches
    fired before this module existed.
    """
    from sqlalchemy import func, select

    from db.engine import database_configured, get_sessionmaker
    from db.models import ConversationEvent

    if not database_configured():
        return 0, None
    stmt = select(
        func.count(ConversationEvent.id), func.max(ConversationEvent.created_at)
    ).where(
        ConversationEvent.tenant_id == tenant_id,
        ConversationEvent.chat_id == int(chat_id),
        ConversationEvent.role == "outbound",
        ConversationEvent.created_at >= since,
    )
    async with get_sessionmaker()() as session:
        count, last = (await session.execute(stmt)).one()
    return int(count or 0), last


def is_operator_task(payload: Optional[dict]) -> bool:
    """Did a person schedule this touch from the panel, or did the funnel?

    The distinction is the whole point of the master switch: "the bot must not
    chase people" is not "the operator may not schedule a follow-up".
    """
    return bool((payload or {}).get("source") == OPERATOR_SOURCE)


async def check_send(
    tenant_id: str,
    chat_id: int,
    tz: tzinfo,
    *,
    now: Optional[datetime] = None,
    operator_initiated: bool = False,
) -> Decision:
    """May we send this chat a message right now?

    Never raises: if the pacing data cannot be read the touch goes out, because
    a database hiccup silencing the funnel is a worse failure than one message
    arriving slightly out of its window.
    """
    now = now or datetime.now(tz)
    try:
        policy = await load_policy(tenant_id)
    except Exception:
        logger.debug("outreach policy unreadable for %s", tenant_id, exc_info=True)
        return Decision(True, None, "")

    if not policy.enabled and not operator_initiated:
        # Dropped, not deferred: the switch is off by default, so a deferral
        # would park this row in the queue indefinitely.
        return Decision(
            False, None, "bot o‘zi birinchi yozmaydi (sozlamada o‘chirilgan)", drop=True
        )

    if not policy.window_contains(now):
        return Decision(
            False,
            policy.next_window_start(now),
            f"jim vaqt ({policy.quiet_to}:00–{policy.quiet_from}:00)",
        )

    if operator_initiated:
        # A human asked for this one message. Anti-flood limits are for the
        # funnel's own drip, not for a deliberate act.
        return Decision(True, None, "")

    day_start = datetime.combine(now.date(), dtime(0, 0), tzinfo=tz)
    try:
        sent_today, last_at = await _outbound_history(tenant_id, chat_id, day_start)
    except Exception:
        logger.debug("outbound history unreadable for %s", chat_id, exc_info=True)
        return Decision(True, None, "")

    if last_at is not None:
        gap = timedelta(minutes=policy.min_gap_minutes)
        earliest = last_at.astimezone(tz) + gap
        if earliest > now:
            return Decision(
                False,
                policy.next_window_start(earliest),
                f"oxirgi xabardan {policy.min_gap_minutes} daqiqa o‘tmagan",
            )

    if sent_today >= policy.daily_cap:
        tomorrow = datetime.combine(
            now.date() + timedelta(days=1), dtime(policy.quiet_from % 24, 0), tzinfo=tz
        )
        return Decision(
            False, tomorrow, f"kunlik chek ({policy.daily_cap} ta) to‘ldi"
        )

    return Decision(True, None, "")


async def last_inbound_at(tenant_id: str, chat_id: int) -> Optional[datetime]:
    """When this chat last wrote to us — `None` if they never have.

    WhatsApp's 24-hour rule is measured from exactly this instant, and the
    corpus already records it. `None` is read as "outside the window": a chat we
    have never heard from cannot be inside one, and guessing the other way would
    make Meta refuse the send after we had already counted it as delivered.
    """
    try:
        from sqlalchemy import select

        from db.engine import database_configured, get_sessionmaker
        from db.models import ConversationEvent

        if not database_configured():
            return None
        stmt = (
            select(ConversationEvent.created_at)
            .where(
                ConversationEvent.tenant_id == tenant_id,
                ConversationEvent.chat_id == int(chat_id),
                ConversationEvent.role == "user",
            )
            .order_by(ConversationEvent.id.desc())
            .limit(1)
        )
        async with get_sessionmaker()() as session:
            return await session.scalar(stmt)
    except Exception:
        logger.debug("last_inbound_at failed for %s", chat_id, exc_info=True)
        return None


async def last_channel(tenant_id: str, chat_id: int) -> str:
    """Which transport did this chat last use — "customer", "whatsapp", …?

    A tenant can now be reached on more than one transport, and a chat id means
    different people on each: a Telegram id and a WhatsApp phone number are
    different address spaces. So a scheduled touch must be delivered on the
    channel the conversation actually happened on, and the corpus already
    records it on every event.

    Returns "" when nothing is known, which the caller reads as "use the
    default" — a new chat with no history yet cannot have a scheduled touch.
    """
    try:
        from sqlalchemy import select

        from db.engine import database_configured, get_sessionmaker
        from db.models import ConversationEvent

        if not database_configured():
            return ""
        stmt = (
            select(ConversationEvent.channel)
            .where(
                ConversationEvent.tenant_id == tenant_id,
                ConversationEvent.chat_id == int(chat_id),
                ConversationEvent.channel.is_not(None),
            )
            .order_by(ConversationEvent.id.desc())
            .limit(1)
        )
        async with get_sessionmaker()() as session:
            return (await session.scalar(stmt)) or ""
    except Exception:
        logger.debug("last_channel lookup failed for %s", chat_id, exc_info=True)
        return ""


async def log_sent_touch(
    *,
    tenant_id: str,
    chat_id: int,
    thread_id: str,
    text: str,
    action: str = "",
    channel: Optional[str] = None,
) -> None:
    """Record a touch that was sent from its **fallback template**.

    A composed touch is already logged by `Agent.compose`; one that fell back to
    the verbatim template was, until this, invisible — missing from the corpus,
    from the panel's "recently sent" list, and from the daily cap that counts
    what a client actually received. Best-effort like every other corpus write:
    a logging failure must never cost a client their message.
    """
    try:
        from db import training

        if not training.enabled() or not text:
            return
        await training.log_event(
            tenant_id=tenant_id,
            chat_id=int(chat_id),
            thread_id=thread_id,
            channel=channel,
            role="outbound",
            text=text,
            meta={"fallback": True, "action": action},
        )
    except Exception:
        logger.debug("logging a fallback touch failed for %s", chat_id, exc_info=True)
