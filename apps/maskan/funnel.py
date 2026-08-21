"""Funnel orchestration — the Maskan care funnel brain.

Two halves, mirroring `apps/byd/funnel.py`:

1. **Transitions** — called by the agent's tools (a grave was registered, a quote
   was given, an order was created) and by the order watcher (payment landed,
   the caretaker started, the work is done). Each moves the lead to its new
   stage, cancels the stale stage's scheduled touches, and enqueues the new
   stage's plan.

2. **Action executors** — called by the scheduler when a task's time comes: the
   follow-ups, payment reminders, SLA escalations, post-work review/referral
   asks, the repeat-care offer, and the seasonal memorial nudge. Registered in
   `ACTIONS`.

Runtime handles (the live customer channel, the operator notifier, the mute
store) aren't importable — they're set once at startup via `set_context()` and
read via `get_context()`, mirroring the services-singleton pattern.

**What this funnel deliberately does not do.** It never marks an order paid,
started or finished. Those facts belong to the Maskan backend — Payme's webhook
writes the payment, the caretaker's own Telegram workflow writes the work
status — and `order_watcher.py` observes them. Every transition from Stage 5
onward is therefore *reactive*: the funnel is told what already happened, and
its only job is to tell the client about it well.

**Mute semantics.** A muted chat means the conversational AI stays silent so a
human owns the dialogue. The scheduler's transactional touches (payment
reminders, "your order is done" with the photos) still send — they are
pre-scripted funnel steps, not the AI conversing, exactly like a CRM firing
reminders regardless of who is handling the chat. On a terminal close, all
pending tasks are purged so nothing fires for a dead lead.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Optional

from apps.maskan import messages as msg
from apps.maskan import notifications as notif
from apps.maskan.config import MASKAN_TZ, MaskanConfig, config
from apps.maskan.models import (
    DO_NOT_CONTACT_REASON,
    STAGE_DONE,
    STAGE_GRAVE,
    STAGE_NEW,
    STAGE_ORDERED,
    STAGE_PAYMENT,
    STAGE_PROGRESS,
    STAGE_QUOTED,
    STAGE_REPEAT,
    STAGE_TITLES_UZ,
    STATUS_ACTIVE,
    STATUS_CLOSED,
    STATUS_COLD,
    MaskanLead,
    MaskanScheduledTask,
)
from apps.maskan.repository import get_repository
from notifications import UNMUTE_CALLBACK_PREFIX

if TYPE_CHECKING:
    from core.channel import Channel
    from core.mute_store import MuteStore
    from notifications import TelegramOperatorNotifier

logger = logging.getLogger(__name__)


# ===== runtime context (set at startup) =====================================

@dataclass
class FunnelContext:
    config: MaskanConfig
    customer_channel: Optional["Channel"] = None
    notifier: Optional["TelegramOperatorNotifier"] = None
    mute_store: Optional["MuteStore"] = None


_context: Optional[FunnelContext] = None


def set_context(ctx: FunnelContext) -> None:
    global _context
    _context = ctx


def get_context() -> FunnelContext:
    if _context is None:
        # Tools/executors can be exercised before wiring in a REPL; fail loudly
        # rather than silently dropping a client message.
        raise RuntimeError("Maskan funnel context is not wired (call set_context first).")
    return _context


# ===== time helpers =========================================================

def _now() -> datetime:
    return datetime.now(MASKAN_TZ)


def _at_hour(day: date, hour: int) -> datetime:
    return datetime(day.year, day.month, day.day, hour, 0, tzinfo=MASKAN_TZ)


def _daytime(when: datetime) -> datetime:
    """Snap a day-scale touch to the configured send hour on its target day.

    Anything scheduled days ahead should land at a civilised hour rather than at
    whatever o'clock the triggering event happened to occur — a grave-care
    reminder arriving at 03:40 would be its own small insult.

    Snapping to the target *date* (not rounding forward from the target time) is
    what keeps a "+1 day" reminder roughly a day away: a quote given at 14:00
    reminds tomorrow at 10:00, not the day after. If that instant has already
    passed, it moves to the next day.
    """
    hour = get_context().config.daytime_send_hour
    slot = _at_hour(when.date(), hour)
    return slot if slot > _now() else _at_hour(when.date() + timedelta(days=1), hour)


def _dedup(lead_id: int, stage: int, action: str, seq: int = 0) -> str:
    return f"{lead_id}:{stage}:{action}:{seq}"


# Chat-silence follow-ups: one a few hours after the client's last message, one
# more days later — then nothing, the initiative is theirs.
CHAT_FOLLOWUP1_DELAY = timedelta(hours=4)
CHAT_FOLLOWUP2_DELAY = timedelta(days=3)

# Actions that must survive a stage transition. The memorial nudge is anchored to
# the calendar, not to the deal: a client who just paid still wants to hear from
# us before Hayit, and a client who went cold especially does.
_STAGE_INDEPENDENT = {"memorial"}


def _row(
    lead: MaskanLead,
    stage: int,
    action: str,
    when: datetime,
    *,
    seq: int = 0,
    payload: dict | None = None,
) -> dict:
    return {
        "lead_id": lead.id,
        "chat_id": lead.chat_id,
        "action_type": action,
        "stage": stage,
        "scheduled_for": when,
        "dedup_key": _dedup(lead.id, stage, action, seq),
        "payload": payload or {},
    }


def _chat_row(chat_id: int, action: str, when: datetime) -> dict:
    """A chat-addressed task row: always `lead_id=0` — the executor resolves the
    chat's *current* state at fire time. The dedup key is per-chat so a
    re-enqueue *resets* the timer instead of stacking rows."""
    return {
        "lead_id": 0,
        "chat_id": int(chat_id),
        "action_type": action,
        "stage": 0,
        "scheduled_for": when,
        "dedup_key": f"chat:{int(chat_id)}:{action}",
        "payload": {},
    }


# ===== client/operator send helpers =========================================

async def _compose_or_fallback(
    chat_id: int,
    *,
    convey: str,
    fallback: str,
    must_include: tuple[str, ...] = (),
) -> str:
    """Compose a scripted touch THROUGH the customer agent so it comes out in the
    client's own language and tone — Maskan's clients write Uzbek, Russian, or a
    mix, and a fixed-language template would break that.

    `convey` (internal, Uzbek) tells the agent what to say; `fallback` is used
    verbatim if composition is unavailable, so the client always gets something;
    `must_include` fragments (the payment link above all) are appended if the
    model dropped them, so critical data can never go missing.
    """
    ctx = get_context()
    text = ""
    if ctx.customer_channel is not None:
        text = await ctx.customer_channel.compose_outbound(int(chat_id), convey)
    if not text:
        text = fallback
    for frag in must_include:
        if frag and frag not in text:
            text = text.rstrip() + "\n" + frag
    return text


async def _send_client_directed(
    chat_id: int,
    *,
    convey: str,
    fallback: str,
    must_include: tuple[str, ...] = (),
) -> None:
    """Compose (via the customer agent) and send a scripted funnel touch.

    Bypasses the mute gate by design (compose + send_text don't go through
    dispatch) — these are pre-scripted touches, not the AI holding a conversation.
    """
    ctx = get_context()
    if ctx.customer_channel is None:
        raise RuntimeError("customer channel not wired")
    text = await _compose_or_fallback(
        chat_id, convey=convey, fallback=fallback, must_include=must_include
    )
    await ctx.customer_channel.send_text(int(chat_id), text)
    await _record_outbound(chat_id, text)


async def _record_outbound(chat_id: int, note: str) -> None:
    """Mirror an out-of-band send into the agent's conversation thread so the AI
    has the full history on handback. Best-effort — never blocks a send."""
    ctx = get_context()
    if ctx.customer_channel is not None and note:
        try:
            await ctx.customer_channel.record_outbound(int(chat_id), note)
        except Exception:
            logger.debug("record_outbound failed for %s", chat_id, exc_info=True)


async def _send_client_photos(chat_id: int, urls: list[str]) -> None:
    urls = [u for u in urls if u]
    if not urls:
        return
    ctx = get_context()
    if ctx.customer_channel is None:
        raise RuntimeError("customer channel not wired")
    await ctx.customer_channel.send_photos(int(chat_id), urls)


async def _notify(text: str, *, reply_markup: Optional[dict] = None) -> int:
    """Broadcast to the operators; returns how many admins actually got it.

    The count matters for handoffs: muting a chat that nobody was told about
    would leave the client talking to a wall (see `escalate_to_human`)."""
    ctx = get_context()
    if ctx.notifier is None:
        logger.warning("Maskan notifier not wired; dropping operator message")
        return 0
    try:
        return await ctx.notifier.notify_text(text, reply_markup=reply_markup)
    except Exception:
        logger.exception("Maskan: operator notification failed")
        return 0


def _unmute_row(chat_id: int) -> list[dict]:
    return [{
        "text": "🤖 Sun'iy intellektni yoqish",
        "callback_data": f"{UNMUTE_CALLBACK_PREFIX}{int(chat_id)}",
    }]


# ===== memorial calendar ====================================================

def _upcoming_memorials(cfg: MaskanConfig, *, limit: int = 3) -> list[date]:
    """The next few configured memorial days that are still far enough away to
    schedule a reminder before (a date whose lead-time has already passed is
    skipped rather than fired immediately)."""
    today = _now().date()
    cutoff = today + timedelta(days=cfg.memorial_lead_days)
    return [d for d in cfg.memorial_dates if d >= cutoff][:limit]


async def _schedule_memorials(lead: MaskanLead) -> None:
    """Arm the seasonal nudge for each upcoming memorial day.

    Idempotent: the dedup key carries the date's ordinal, so re-arming on every
    transition just refreshes the same rows instead of stacking them.
    """
    cfg = get_context().config
    days = _upcoming_memorials(cfg)
    if not days:
        return
    rows = []
    for day in days:
        when = _at_hour(day - timedelta(days=cfg.memorial_lead_days), cfg.daytime_send_hour)
        rows.append(
            _row(
                lead,
                0,
                "memorial",
                when,
                seq=day.toordinal(),
                payload={"date": day.isoformat()},
            )
        )
    await get_repository().enqueue_tasks(rows)


# ===== transitions ==========================================================

async def ensure_lead(
    *,
    chat_id: int,
    name: str = "",
    phone: str = "",
    request: str = "",
    username: str = "",
) -> MaskanLead:
    """Get the chat's open lead, or open one. Idempotent per chat.

    Called from every tool that touches funnel state, so the agent never has to
    think about lead lifecycle — it just does its job and the lead follows.
    Fields already set are not overwritten with blanks.
    """
    repo = get_repository()
    lead = await repo.get_active_lead_by_chat(chat_id)
    if lead is not None:
        updates = {}
        if name and not lead.name:
            updates["name"] = name
        if phone and not lead.phone:
            updates["phone"] = phone
        if request and not lead.request:
            updates["request"] = request
        if username and username != lead.tg_username:
            updates["tg_username"] = username
        if updates:
            lead = await repo.update_lead(lead.id, **updates) or lead
        return lead

    lead = await repo.create_lead(
        chat_id=chat_id,
        name=name,
        phone=phone,
        request=request,
        tg_username=username,
        stage=STAGE_NEW,
    )
    now = _now()
    await repo.enqueue_tasks(
        [
            _row(lead, STAGE_NEW, "s1_followup", now + timedelta(hours=6)),
            _row(lead, STAGE_NEW, "s1_last", _daytime(now + timedelta(days=2))),
        ]
    )
    await _schedule_memorials(lead)
    logger.info("Maskan lead %s opened for chat %s", lead.id, chat_id)
    return lead


async def note_grave(
    *,
    chat_id: int,
    grave_id: int,
    grave_label: str,
    cemetery_label: str,
    django_user_id: Optional[int] = None,
    name: str = "",
    username: str = "",
) -> MaskanLead:
    """Stage 1→2. We now know whose grave and where — the first real commitment
    point, and the moment staff should know a live case exists."""
    repo = get_repository()
    lead = await ensure_lead(chat_id=chat_id, name=name, username=username)
    first_time = lead.current_stage < STAGE_GRAVE

    lead = await repo.advance_stage(
        lead.id,
        STAGE_GRAVE,
        django_grave_id=int(grave_id),
        grave_label=grave_label or lead.grave_label,
        cemetery_label=cemetery_label or lead.cemetery_label,
        **({"django_user_id": int(django_user_id)} if django_user_id else {}),
    ) or lead

    await repo.cancel_pending_for_lead(lead.id, exclude_actions=_STAGE_INDEPENDENT)
    now = _now()
    await repo.enqueue_tasks(
        [
            _row(lead, STAGE_GRAVE, "s2_offer", now + timedelta(minutes=45)),
            _row(lead, STAGE_GRAVE, "s2_followup", _daytime(now + timedelta(days=1))),
            _row(lead, STAGE_GRAVE, "s2_last", _daytime(now + timedelta(days=3))),
        ]
    )
    if first_time:
        await _notify(
            notif.new_lead_message(
                name=lead.name,
                request=lead.request,
                chat_id=chat_id,
                username=lead.tg_username,
                phone=lead.phone,
            )
        )
    logger.info("Maskan lead %s reached grave stage (grave %s)", lead.id, grave_id)
    return lead


async def note_quote(
    *,
    chat_id: int,
    service_codes: list[str],
    total: Optional[int],
    grave_id: Optional[int] = None,
) -> Optional[MaskanLead]:
    """Stage 2→3. The client has been told a concrete price for concrete
    services — from here the reminders talk about money, not about options."""
    repo = get_repository()
    lead = await repo.get_active_lead_by_chat(chat_id)
    if lead is None:
        return None
    lead = await repo.advance_stage(
        lead.id,
        STAGE_QUOTED,
        service_codes=list(service_codes or []),
        order_total=total,
        # A client who picked an existing grave never went through note_grave,
        # so this is where the handle gets attached.
        **({"django_grave_id": int(grave_id)} if grave_id else {}),
    ) or lead
    await repo.cancel_pending_for_lead(lead.id, exclude_actions=_STAGE_INDEPENDENT)
    now = _now()
    await repo.enqueue_tasks(
        [
            _row(lead, STAGE_QUOTED, "s3_reminder1", now + timedelta(hours=3)),
            _row(lead, STAGE_QUOTED, "s3_reminder2", _daytime(now + timedelta(days=1))),
            _row(lead, STAGE_QUOTED, "s3_last", _daytime(now + timedelta(days=3))),
        ]
    )
    return lead


async def note_order(
    *,
    chat_id: int,
    order_id: int,
    total: Optional[int],
    frequency: str,
    checkout_url: str,
    service_codes: Optional[list[str]] = None,
) -> Optional[MaskanLead]:
    """Stage 3→4. An awaiting-payment order exists in the backend and the client
    has the Payme link. Now we wait for the money — reminders here, and an
    operator check if it never arrives.

    The checkout URL is stored on the task payload (not just sent once) so every
    reminder can repeat the exact same link.
    """
    repo = get_repository()
    lead = await repo.get_active_lead_by_chat(chat_id)
    if lead is None:
        return None
    lead = await repo.advance_stage(
        lead.id,
        STAGE_PAYMENT,
        django_order_id=int(order_id),
        order_total=total,
        order_frequency=frequency or "once",
        last_order_status="pending",
        last_payment_status="awaiting",
        **({"service_codes": list(service_codes)} if service_codes else {}),
    ) or lead

    await repo.cancel_pending_for_lead(lead.id, exclude_actions=_STAGE_INDEPENDENT)
    now = _now()
    link_payload = {"url": checkout_url}
    await repo.enqueue_tasks(
        [
            _row(lead, STAGE_PAYMENT, "s4_reminder1", now + timedelta(hours=2),
                 seq=1, payload=link_payload),
            _row(lead, STAGE_PAYMENT, "s4_reminder2", _daytime(now + timedelta(days=1)),
                 seq=2, payload=link_payload),
            _row(lead, STAGE_PAYMENT, "s4_reminder3", _daytime(now + timedelta(days=3)),
                 seq=3, payload=link_payload),
            _row(lead, STAGE_PAYMENT, "s4_operator_check", now + timedelta(hours=6)),
            _row(lead, STAGE_PAYMENT, "s4_expire", _daytime(now + timedelta(days=7))),
        ]
    )
    await _notify(
        notif.order_created_message(
            name=lead.name,
            order_id=order_id,
            total=total,
            grave=lead.grave_label,
            cemetery=lead.cemetery_label,
            frequency_label=msg.FREQ_LABELS_UZ.get(frequency or "once", "—"),
            chat_id=chat_id,
            username=lead.tg_username,
        )
    )
    logger.info("Maskan lead %s awaiting payment for order %s", lead.id, order_id)
    return lead


# ----- watcher-driven transitions (the backend already made these true) -----

async def on_payment_received(lead: MaskanLead, order: dict) -> None:
    """Stage 4→5. Payme's webhook marked the order paid; the backend has routed
    it to the cemetery's caretaker group. Tell the client, alert staff, and start
    the delivery SLA."""
    repo = get_repository()
    total = order.get("total") or lead.order_total
    lead = await repo.advance_stage(
        lead.id,
        STAGE_ORDERED,
        order_total=total,
        last_payment_status="paid",
        # Deliberately NOT stamping the backend's work status here. Payment and
        # acceptance can both land between two polls, and recording "accepted"
        # as part of the payment step would make the next tick see no change —
        # the client would never hear that the work had started. Leaving it at
        # "pending" lets the work transition fire on the following tick.
        paid_at=_now(),
    ) or lead

    await repo.cancel_pending_for_lead(lead.id, exclude_actions=_STAGE_INDEPENDENT)
    await _send_client_directed(
        lead.chat_id,
        convey=(
            f"To'lov qabul qilinganini ayt, rahmat bildir. Buyurtma raqami "
            f"№{lead.django_order_id}, summa {msg.fmt_sum(total)}. Buyurtma qabriston "
            "xodimiga uzatilganini, ish tugagach oldin/keyin rasmlari yuborilishini ayt."
        ),
        fallback=msg.s5_paid(lead.name, int(lead.django_order_id or 0), total),
    )
    await _notify(
        notif.payment_received_message(
            name=lead.name,
            order_id=int(lead.django_order_id or 0),
            total=total,
            grave=lead.grave_label,
            cemetery=lead.cemetery_label,
        )
    )
    # If nobody picks the job up within a day, staff need to know.
    await repo.enqueue_tasks(
        [_row(lead, STAGE_ORDERED, "s5_sla", _now() + timedelta(hours=24))]
    )


async def on_work_started(lead: MaskanLead, order: dict) -> None:
    """Stage 5→6. A caretaker accepted the job in the cemetery group."""
    repo = get_repository()
    caretaker = str(order.get("caretaker") or "")
    lead = await repo.advance_stage(
        lead.id,
        STAGE_PROGRESS,
        last_order_status=str(order.get("status") or "accepted"),
    ) or lead
    await repo.cancel_pending_for_lead(lead.id, exclude_actions=_STAGE_INDEPENDENT)
    await _send_client_directed(
        lead.chat_id,
        convey=(
            "Buyurtma qabriston xodimi tomonidan qabul qilinganini va ish "
            "boshlanganini ayt."
            + (f" Xodim ismi: {caretaker}." if caretaker else "")
            + " Ish tugagach rasmlari yuborilishini ayt."
        ),
        fallback=msg.s6_accepted(lead.name, caretaker),
    )
    # Photos should arrive within a few days; otherwise staff chase it.
    await repo.enqueue_tasks(
        [_row(lead, STAGE_PROGRESS, "s6_sla", _now() + timedelta(hours=72))]
    )


async def on_work_completed(lead: MaskanLead, order: dict) -> None:
    """Stage 6→7. The admin confirmed the caretaker's before/after photos.

    The photos are the product here — send them first, then say what happened,
    then let the post-work chain (review, referral, repeat) take over.
    """
    repo = get_repository()
    lead = await repo.advance_stage(
        lead.id,
        STAGE_DONE,
        last_order_status="completed",
        completed_at=_now(),
    ) or lead
    await repo.cancel_pending_for_lead(lead.id, exclude_actions=_STAGE_INDEPENDENT)

    photos = [u for u in (order.get("photos") or []) if u]
    if photos:
        try:
            await _send_client_photos(lead.chat_id, photos)
        except Exception:
            # A failed album must not cost the client the message itself.
            logger.exception("Maskan lead %s: sending result photos failed", lead.id)

    await _send_client_directed(
        lead.chat_id,
        convey=(
            f"{lead.grave_label or 'Qabr'} parvarish qilinganini ayt. "
            + ("Rasmlar yuborildi — " if photos else "")
            + "ishdan ko'ngli to'lganini so'ra. Iliq, qisqa yoz."
        ),
        fallback=msg.s7_completed(lead.name, lead.grave_label),
    )

    now = _now()
    rows = [
        _row(lead, STAGE_DONE, "s7_review", _daytime(now + timedelta(days=2))),
        _row(lead, STAGE_DONE, "s7_referral", _daytime(now + timedelta(days=5))),
    ]
    # Recurring order → offer the next cycle; one-off → an annual nudge.
    repeat_days = {"monthly": 30, "quarterly": 90, "annual": 365}.get(lead.order_frequency)
    if repeat_days:
        rows.append(
            _row(lead, STAGE_REPEAT, "s8_recurring", _daytime(now + timedelta(days=repeat_days)))
        )
    else:
        rows.append(
            _row(lead, STAGE_REPEAT, "s8_annual", _daytime(now + timedelta(days=365)))
        )
    await repo.enqueue_tasks(rows)
    await _schedule_memorials(lead)
    logger.info("Maskan lead %s completed (order %s)", lead.id, lead.django_order_id)


async def on_order_rejected(lead: MaskanLead, order: dict) -> None:
    """The admin rejected the caretaker's work. The client gets a calm heads-up;
    staff get the reason and own it from there."""
    repo = get_repository()
    reason = str(order.get("reject_reason") or "")
    await repo.update_lead(lead.id, last_order_status="rejected")
    await repo.cancel_pending_for_lead(lead.id, exclude_actions=_STAGE_INDEPENDENT)
    await _send_client_directed(
        lead.chat_id,
        convey=(
            "Buyurtma bo'yicha aniqlashtirish kerakligini, mas'ul xodim tez orada "
            "bog'lanishini ayt. Sababni aytma, uzr so'ra."
        ),
        fallback=msg.s7_rejected(lead.name),
    )
    await _notify(
        notif.sla_message(
            title="Buyurtma rad etildi" + (f": {reason}" if reason else ""),
            name=lead.name,
            order_id=lead.django_order_id,
            grave=lead.grave_label,
            cemetery=lead.cemetery_label,
            chat_id=lead.chat_id,
            username=lead.tg_username,
        ),
        reply_markup=notif.task_done_button(lead.id, "rejected"),
    )


# ----- escalation / termination ---------------------------------------------

async def escalate_to_human(
    *, chat_id: int, reason: str, name: str = "", username: str = ""
) -> None:
    """The AI can't handle this client — hand off to a human.

    Notifies staff with the reason + contact AND mutes the chat, so the AI goes
    silent and the operator owns the conversation. The «Sun'iy intellektni
    yoqish» button hands control back.
    """
    repo = get_repository()
    lead = await repo.get_active_lead_by_chat(chat_id)
    ctx = get_context()
    muted = False
    if ctx.mute_store is not None:
        try:
            await ctx.mute_store.mute(int(chat_id))
            muted = True
        except Exception:
            logger.exception("Maskan: muting chat %s failed", chat_id)

    # Two ways out of a handoff: hand the chat back to the AI, or judge the case
    # dead and stop every scheduled touch for it.
    rows = [_unmute_row(chat_id)]
    if lead is not None:
        rows.append(notif.close_button(lead.id)["inline_keyboard"][0])
    markup = {"inline_keyboard": rows}
    delivered = await _notify(
        notif.handoff_message(
            name=name or (lead.name if lead else ""),
            reason=reason,
            chat_id=chat_id,
            username=username or (lead.tg_username if lead else ""),
            lead_id=lead.id if lead else None,
            stage_title=STAGE_TITLES_UZ.get(lead.current_stage, "") if lead else None,
            phone=lead.phone if lead else "",
        ),
        reply_markup=markup,
    )
    if muted and not delivered:
        # Nobody was reachable, so nobody can press «включить ИИ» — a mute here
        # would silence the chat forever. Better a mediocre AI answer than a
        # client left with no one at all.
        try:
            await ctx.mute_store.unmute(int(chat_id))
            muted = False
        except Exception:
            logger.exception("Maskan: rolling back mute for chat %s failed", chat_id)
        logger.error(
            "Maskan chat %s: handoff notification reached no operator — mute rolled back",
            chat_id,
        )
    logger.info(
        "Maskan chat %s handed to a human (%s operator(s) notified, muted=%s): %s",
        chat_id, delivered, muted, reason,
    )


async def stop_contact(
    *, chat_id: int, reason: str = "", name: str = "", username: str = ""
) -> None:
    """The client asked us not to write again.

    Terminal for outreach: every pending touch addressed to the chat is
    cancelled and the lead — created on the spot if none exists, so the flag has
    somewhere to live — is closed with the do-not-contact marker. The chat is
    deliberately NOT muted: we never write first again, but the AI still answers
    normally if the client writes to us.
    """
    repo = get_repository()
    lead = await repo.get_active_lead_by_chat(chat_id)
    if lead is None:
        lead = await repo.create_lead(
            chat_id=chat_id, name=name, tg_username=username, stage=STAGE_NEW
        )
    await repo.cancel_pending_for_chat(chat_id)
    await repo.set_status(lead.id, STATUS_CLOSED, close_reason=DO_NOT_CONTACT_REASON)
    await _notify(
        notif.do_not_contact_message(
            name=name or lead.name,
            chat_id=chat_id,
            username=username or lead.tg_username,
            reason=reason,
        )
    )
    logger.info("Maskan chat %s flagged do-not-contact (lead %s)", chat_id, lead.id)


async def close_lead(lead_id: int, reason: str = "") -> tuple[bool, str]:
    """Operator-driven terminal close (inline button or manager tool)."""
    repo = get_repository()
    lead = await repo.get_lead(lead_id)
    if lead is None:
        return False, "Murojaat topilmadi."
    await repo.cancel_pending_for_lead(lead.id)
    await repo.set_status(lead.id, STATUS_CLOSED, close_reason=reason or "Operator yopdi")
    return True, f"Murojaat #{lead.id} yopildi, kuzatuv to'xtatildi."


# ----- inbound-message hooks -------------------------------------------------

async def note_customer_activity(chat_id: int) -> None:
    """Every inbound message (re)arms the chat-silence follow-ups: one a few
    hours after the client's last message, a second days later — then nothing.
    The reset-on-conflict enqueue is what pushes both timers back on each message.

    Not armed when someone else owns the pacing: a human operator (mute), the
    funnel itself (any stage past the opening capture, which has its own
    cadence), or a do-not-contact chat.
    """
    ctx = get_context()
    if ctx.mute_store is not None:
        try:
            if await ctx.mute_store.is_muted(int(chat_id)):
                return
        except Exception:
            logger.debug("mute check failed for chat %s", chat_id, exc_info=True)

    repo = get_repository()
    lead = await repo.get_latest_lead_by_chat(chat_id)
    if lead is not None:
        if lead.status == STATUS_CLOSED and lead.close_reason == DO_NOT_CONTACT_REASON:
            return
        if lead.status != STATUS_CLOSED and lead.current_stage != STAGE_NEW:
            return
    now = _now()
    await repo.enqueue_tasks(
        [
            _chat_row(chat_id, "chat_followup1", now + CHAT_FOLLOWUP1_DELAY),
            _chat_row(chat_id, "chat_followup2", _daytime(now + CHAT_FOLLOWUP2_DELAY)),
        ]
    )


async def on_customer_reply(chat_id: int) -> bool:
    """A cold lead wrote back — bring it home. Returns True if it acted."""
    repo = get_repository()
    lead = await repo.get_active_lead_by_chat(chat_id)
    if lead is None or lead.status != STATUS_COLD:
        return False
    await repo.set_status(lead.id, STATUS_ACTIVE)
    logger.info("Maskan lead %s reactivated by an inbound message", lead.id)
    return True


async def _go_cold(lead: MaskanLead) -> None:
    """Follow-ups exhausted: stop the chase, keep the seasonal touches."""
    repo = get_repository()
    await repo.cancel_pending_for_lead(lead.id, exclude_actions=_STAGE_INDEPENDENT)
    await repo.set_status(lead.id, STATUS_COLD)


# ===== action executors =====================================================
#
# Each takes (lead, task). `lead` is None only for chat-addressed rows
# (lead_id=0), which resolve the chat's state themselves at fire time.

async def _exec_chat_followup1(lead, task: MaskanScheduledTask) -> None:
    fresh = await get_repository().get_active_lead_by_chat(task.chat_id)
    if fresh is not None and fresh.current_stage != STAGE_NEW:
        return
    await _send_client_directed(
        task.chat_id,
        convey=(
            "Mijoz javob bermay qoldi. Qisqa, bosiq eslatma yoz: qaysi qabristonda "
            "va kimning qabri ekanini bilsak, xizmat va narxni aniq aytishimizni ayt."
        ),
        fallback=msg.s1_followup(fresh.name if fresh else ""),
    )


async def _exec_chat_followup2(lead, task: MaskanScheduledTask) -> None:
    cfg = get_context().config
    fresh = await get_repository().get_active_lead_by_chat(task.chat_id)
    if fresh is not None and fresh.current_stage != STAGE_NEW:
        return
    await _send_client_directed(
        task.chat_id,
        convey=(
            "Oxirgi marta yoz: bezovta qilmayotganingni, kerak bo'lganda istalgan "
            f"payt yozishi mumkinligini ayt. Telefon raqamini ber: {cfg.support_phone}."
        ),
        fallback=msg.s1_last_call(fresh.name if fresh else "", cfg),
        must_include=(cfg.support_phone,),
    )
    if fresh is not None:
        await _go_cold(fresh)


async def _exec_s1_followup(lead: MaskanLead, task: MaskanScheduledTask) -> None:
    await _send_client_directed(
        lead.chat_id,
        convey=(
            "Qaysi qabristonda va kimning qabri ekanini so'ra — shundan keyin "
            "xizmatlar va narxlarni aniq ayta olishingni tushuntir."
        ),
        fallback=msg.s1_followup(lead.name),
    )


async def _exec_s1_last(lead: MaskanLead, task: MaskanScheduledTask) -> None:
    cfg = get_context().config
    await _send_client_directed(
        lead.chat_id,
        convey=(
            "Bosiq yakuniy xabar: bezovta qilmaysan, kerak bo'lsa yozsin. "
            f"Telefon: {cfg.support_phone}."
        ),
        fallback=msg.s1_last_call(lead.name, cfg),
        must_include=(cfg.support_phone,),
    )
    await _go_cold(lead)


async def _exec_s2_offer(lead: MaskanLead, task: MaskanScheduledTask) -> None:
    # Build the subject phrase from whatever we actually know — an empty label
    # would otherwise leave a dangling "dagi" in the directive.
    where = f"{lead.cemetery_label}dagi " if lead.cemetery_label else ""
    whose = f"{lead.grave_label} qabri" if lead.grave_label else "qabr"
    await _send_client_directed(
        lead.chat_id,
        convey=(
            f"{where}{whose} uchun xizmat taklif qil. "
            "Eng ko'p tanlanadiganlari: o't tozalash, umumiy tozalash, marmar jilo; "
            "to'liq parvarish hammasini qamrab oladi. Qaysi biri kerakligini so'ra. "
            "Narxni o'zingdan o'ylab topma — kerak bo'lsa xizmatlar ro'yxati asbobidan foydalan."
        ),
        fallback=msg.s2_offer(lead.name, lead.grave_label, lead.cemetery_label),
    )


async def _exec_s2_followup(lead: MaskanLead, task: MaskanScheduledTask) -> None:
    await _send_client_directed(
        lead.chat_id,
        convey="Qisqa eslatma: xizmatni tanlashga yordam berishingni taklif qil.",
        fallback=msg.s2_followup(lead.name),
    )


async def _exec_s2_last(lead: MaskanLead, task: MaskanScheduledTask) -> None:
    await _send_client_directed(
        lead.chat_id,
        convey=(
            "Yakuniy xabar: bezovta qilmaysan; kerak bo'lganda yozsin, qabr "
            "ma'lumotlari saqlanib qolishini ayt."
        ),
        fallback=msg.s2_last_call(lead.name),
    )
    await _go_cold(lead)


async def _exec_s3_reminder1(lead: MaskanLead, task: MaskanScheduledTask) -> None:
    await _send_client_directed(
        lead.chat_id,
        convey=(
            f"Aytilgan narxni eslat — jami {msg.fmt_sum(lead.order_total)}. "
            "To'lovni rasmiylashtirishga yordam kerakmi, deb so'ra."
        ),
        fallback=msg.s3_reminder(lead.name, lead.order_total),
    )


async def _exec_s3_reminder2(lead: MaskanLead, task: MaskanScheduledTask) -> None:
    await _send_client_directed(
        lead.chat_id,
        convey="Yumshoq eslatma: savol qolgan bo'lsa javob berishingni ayt.",
        fallback=msg.s3_reminder(lead.name, lead.order_total),
    )


async def _exec_s3_last(lead: MaskanLead, task: MaskanScheduledTask) -> None:
    await _send_client_directed(
        lead.chat_id,
        convey="Yakuniy, bosiq xabar: kerak bo'lganda yozsin.",
        fallback=msg.s2_last_call(lead.name),
    )
    await _go_cold(lead)


async def _payment_reminder(lead: MaskanLead, task: MaskanScheduledTask) -> None:
    """Shared body for the three payment reminders.

    Skips itself if the money already arrived — the watcher runs on its own
    clock, so a reminder can be claimed a minute after payment landed.
    """
    fresh = await get_repository().get_lead(lead.id)
    if fresh is None or fresh.last_payment_status == "paid":
        return
    link = str((task.payload or {}).get("url") or "")
    await _send_client_directed(
        lead.chat_id,
        convey=(
            f"To'lov havolasi hali ochiqligini eslat, summa {msg.fmt_sum(fresh.order_total)}. "
            "To'lovdan keyin buyurtma darhol go'rkovga uzatilishini ayt. "
            f"Havolani o'zgartirmasdan qo'sh: {link}"
        ),
        fallback=msg.s4_payment_reminder(fresh.name, fresh.order_total, link),
        must_include=(link,),
    )


async def _exec_s4_reminder1(lead: MaskanLead, task: MaskanScheduledTask) -> None:
    await _payment_reminder(lead, task)


async def _exec_s4_reminder2(lead: MaskanLead, task: MaskanScheduledTask) -> None:
    await _payment_reminder(lead, task)


async def _exec_s4_reminder3(lead: MaskanLead, task: MaskanScheduledTask) -> None:
    await _payment_reminder(lead, task)


async def _exec_s4_operator_check(lead: MaskanLead, task: MaskanScheduledTask) -> None:
    fresh = await get_repository().get_lead(lead.id)
    if fresh is None or fresh.last_payment_status == "paid":
        return
    await _notify(
        notif.payment_check_message(
            name=fresh.name,
            order_id=fresh.django_order_id,
            total=fresh.order_total,
            chat_id=fresh.chat_id,
            username=fresh.tg_username,
        ),
        reply_markup=notif.task_done_button(fresh.id, "payment"),
    )


async def _exec_s4_expire(lead: MaskanLead, task: MaskanScheduledTask) -> None:
    """The payment window closed with no money. Stop chasing, and stop watching.

    Stamping `expired` is what takes the order out of the watcher's poll set —
    otherwise every never-paid order would be polled forever and eventually
    crowd out live ones.
    """
    repo = get_repository()
    fresh = await repo.get_lead(lead.id)
    if fresh is None or fresh.last_payment_status == "paid":
        return
    await _send_client_directed(
        lead.chat_id,
        convey=(
            "To'lov amalga oshmaganini, buyurtmani kutish rejimidan chiqarganingni "
            "ayt. Kerak bo'lsa bir og'iz yozsa, qaytadan tez rasmiylashtirishingni ayt."
        ),
        fallback=msg.s4_expire(fresh.name),
    )
    await repo.update_lead(fresh.id, last_order_status="expired")
    await _go_cold(fresh)


async def _exec_s5_sla(lead: MaskanLead, task: MaskanScheduledTask) -> None:
    """Paid a day ago and still nobody accepted it in the cemetery group."""
    fresh = await get_repository().get_lead(lead.id)
    if fresh is None or fresh.current_stage != STAGE_ORDERED:
        return
    await _notify(
        notif.sla_message(
            title="To'langan buyurtmani sutka davomida hech kim qabul qilmadi.",
            name=fresh.name,
            order_id=fresh.django_order_id,
            grave=fresh.grave_label,
            cemetery=fresh.cemetery_label,
            chat_id=fresh.chat_id,
            username=fresh.tg_username,
        ),
        reply_markup=notif.task_done_button(fresh.id, "accept_sla"),
    )


async def _exec_s6_sla(lead: MaskanLead, task: MaskanScheduledTask) -> None:
    """Accepted three days ago and no before/after photos have arrived."""
    fresh = await get_repository().get_lead(lead.id)
    if fresh is None or fresh.current_stage != STAGE_PROGRESS:
        return
    await _notify(
        notif.sla_message(
            title="Ish qabul qilinganiga 3 kun bo'ldi, rasmlar hali yo'q.",
            name=fresh.name,
            order_id=fresh.django_order_id,
            grave=fresh.grave_label,
            cemetery=fresh.cemetery_label,
            chat_id=fresh.chat_id,
            username=fresh.tg_username,
        ),
        reply_markup=notif.task_done_button(fresh.id, "photo_sla"),
    )


async def _exec_s7_review(lead: MaskanLead, task: MaskanScheduledTask) -> None:
    cfg = get_context().config
    await _send_client_directed(
        lead.chat_id,
        convey=(
            "Parvarish sifati haqida fikrini so'ra — qisqa va samimiy. "
            f"Ilovada ham baho qoldirishi mumkinligini ayt: {cfg.app_android_url}"
        ),
        fallback=msg.s7_review(lead.name, cfg),
        must_include=(cfg.app_android_url,),
    )


async def _exec_s7_referral(lead: MaskanLead, task: MaskanScheduledTask) -> None:
    await _send_client_directed(
        lead.chat_id,
        convey=(
            "Tanish-bilishlariga ham qabr parvarishi kerak bo'lsa, bemalol shu yerga "
            "yo'naltirishini ayt. Bosiq, majburlamasdan."
        ),
        fallback=msg.s7_referral(lead.name),
    )


async def _exec_s8_recurring(lead: MaskanLead, task: MaskanScheduledTask) -> None:
    """The subscription cycle came round — offer the same care again."""
    await _send_client_directed(
        lead.chat_id,
        convey=(
            f"{lead.grave_label or 'Qabr'} uchun "
            f"{msg.FREQ_LABELS_UZ.get(lead.order_frequency, 'keyingi')} parvarish vaqti "
            "kelganini ayt va avvalgi xizmatlar bilan davom ettirishni taklif qil."
        ),
        fallback=msg.s8_recurring(lead.name, lead.grave_label, lead.order_frequency),
    )
    # Keep the cycle going for as long as the client keeps saying yes.
    repeat_days = {"monthly": 30, "quarterly": 90, "annual": 365}.get(lead.order_frequency)
    if repeat_days:
        nxt = _daytime(_now() + timedelta(days=repeat_days))
        await get_repository().enqueue_tasks(
            [_row(lead, STAGE_REPEAT, "s8_recurring", nxt, seq=nxt.toordinal())]
        )


async def _exec_s8_annual(lead: MaskanLead, task: MaskanScheduledTask) -> None:
    await _send_client_directed(
        lead.chat_id,
        convey=(
            f"O'tgan yili {lead.grave_label or 'qabr'} parvarish qilganingizni eslat "
            "va bu yil ham tartibga keltirishni taklif qil."
        ),
        fallback=msg.s8_annual(lead.name, lead.grave_label),
    )


async def _exec_memorial(lead: MaskanLead, task: MaskanScheduledTask) -> None:
    """The seasonal driver: Hayit / Arafa is coming and people visit graves.

    Fires regardless of stage (people order precisely because the date is near),
    but never for a do-not-contact chat — `execute_task` guards closed leads and
    `cancel_pending_for_chat` clears these rows on a stop-contact request.
    """
    raw = str((task.payload or {}).get("date") or "")
    when = None
    if raw:
        try:
            when = date.fromisoformat(raw)
        except ValueError:
            when = None
    await _send_client_directed(
        lead.chat_id,
        convey=(
            "Ziyorat kunlari yaqinlashayotganini ayt"
            + (f" ({msg.date_label(when)})" if when else "")
            + f" va {lead.grave_label or 'qabr'}ni shu kungacha tartibga keltirishni "
            "taklif qil. Oldindan buyurtma bersa ulgurishimizni ayt. Bosiq, hurmat bilan."
        ),
        fallback=msg.s8_memorial(lead.name, lead.grave_label, when),
    )


ACTIONS = {
    "chat_followup1": _exec_chat_followup1,
    "chat_followup2": _exec_chat_followup2,
    "s1_followup": _exec_s1_followup,
    "s1_last": _exec_s1_last,
    "s2_offer": _exec_s2_offer,
    "s2_followup": _exec_s2_followup,
    "s2_last": _exec_s2_last,
    "s3_reminder1": _exec_s3_reminder1,
    "s3_reminder2": _exec_s3_reminder2,
    "s3_last": _exec_s3_last,
    "s4_reminder1": _exec_s4_reminder1,
    "s4_reminder2": _exec_s4_reminder2,
    "s4_reminder3": _exec_s4_reminder3,
    "s4_operator_check": _exec_s4_operator_check,
    "s4_expire": _exec_s4_expire,
    "s5_sla": _exec_s5_sla,
    "s6_sla": _exec_s6_sla,
    "s7_review": _exec_s7_review,
    "s7_referral": _exec_s7_referral,
    "s8_recurring": _exec_s8_recurring,
    "s8_annual": _exec_s8_annual,
    "memorial": _exec_memorial,
}


async def execute_task(task: MaskanScheduledTask) -> None:
    """Run one claimed task. Raises on failure so the scheduler can retry/park it.

    Skips leads that went terminal since the task was scheduled — a defensive
    backstop on top of cancel-on-close.
    """
    action = ACTIONS.get(task.action_type)
    if action is None:
        logger.error("Unknown Maskan action_type %r (task %s)", task.action_type, task.id)
        return
    if not task.lead_id:
        # Chat-addressed task — the executor does its own fire-time checks.
        await action(None, task)
        return
    lead = await get_repository().get_lead(task.lead_id)
    if lead is None:
        logger.warning("Maskan task %s references missing lead %s", task.id, task.lead_id)
        return
    if lead.status == STATUS_CLOSED:
        logger.info("Maskan task %s skipped — lead %s is closed", task.id, lead.id)
        return
    await action(lead, task)
