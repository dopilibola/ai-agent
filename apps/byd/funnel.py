"""Funnel orchestration — the BYD sales funnel brain.

Two halves:

1. **Transitions** (called by agent tools + operator inline-button callbacks):
   create a lead, record a call outcome, schedule the consultation, request
   prepayment, mark paid, mark completed, react to a customer reply. Each moves
   the lead between Bitrix stages, cancels the stale stage's scheduled tasks, and
   enqueues the new stage's plan.

2. **Action executors** (called by the scheduler when a task's time comes): the
   ~23 time-delayed touches — drip messages, reminders, the voucher, post-sale
   chain, birthday. Registered in `ACTIONS`.

Runtime handles (the live customer channel, the operator notifier, the mute
store) aren't importable — they're set once at startup via `set_context()` and
read via `get_context()`, mirroring the services-singleton pattern.

**Mute semantics.** A muted chat means the *conversational AI* stays silent so a
human operator owns the dialogue (set on Stage-3 handoff). The scheduler's
*transactional* touches (reminders, payment link, voucher) still send — they're
pre-scripted funnel steps, not the AI conversing — exactly like a CRM firing
reminders regardless of who's handling the chat. Operator/manager notifications
always go out. On a terminal close, all pending tasks are purged so nothing
fires for a dead lead.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Optional

from apps.byd.config import CLINIC_TZ, BydConfig, config
from apps.byd import messages as msg
from apps.byd import notifications as notif
from apps.byd.models import (
    DO_NOT_CONTACT_REASON,
    STAGE_BOOKED,
    STAGE_CONFIRMED,
    STAGE_CONSULT,
    STAGE_DONE,
    STAGE_NEGOTIATION,
    STAGE_NEW,
    STAGE_NO_ANSWER,
    STAGE_PREPAYMENT,
    STAGE_TITLES_RU,
    STATUS_ACTIVE,
    STATUS_CLOSED,
    STATUS_COLD,
    BydLead,
    BydScheduledTask,
)
from apps.byd.repository import get_repository
from notifications import UNMUTE_CALLBACK_PREFIX

if TYPE_CHECKING:
    from core.channel import Channel
    from core.mute_store import MuteStore
    from notifications import TelegramOperatorNotifier

logger = logging.getLogger(__name__)


# ===== runtime context (set at startup) =====================================

@dataclass
class FunnelContext:
    config: BydConfig
    customer_channel: Optional["Channel"] = None
    notifier: Optional["TelegramOperatorNotifier"] = None
    mute_store: Optional["MuteStore"] = None


_context: Optional[FunnelContext] = None


def set_context(ctx: FunnelContext) -> None:
    global _context
    _context = ctx


def get_context() -> FunnelContext:
    if _context is None:
        # Fall back to a config-only context so import-time/test paths don't NPE;
        # sends will fail loudly (channel is None) and the task retries.
        return FunnelContext(config=config)
    return _context


# ===== time helpers =========================================================

def _now() -> datetime:
    return datetime.now(CLINIC_TZ)


def _clinic_dt(d: date, hour: int) -> datetime:
    return datetime(d.year, d.month, d.day, hour, 0, tzinfo=CLINIC_TZ)


def _dedup(lead_id: int, stage: int, action: str, seq: int = 0) -> str:
    return f"{lead_id}:{stage}:{action}:{seq}"


# Chat-silence follow-ups (script §8.3–8.5): first a few hours after the client's
# last message, one more days later — then nothing, the initiative is theirs.
CHAT_FOLLOWUP1_DELAY = timedelta(hours=4)
CHAT_FOLLOWUP2_DELAY = timedelta(days=3)


def _row(
    lead: BydLead, stage: int, action: str, when: datetime, *, seq: int = 0, payload: dict | None = None
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
    chat's *current* state at fire time, so tagging a lead id here would only go
    stale (the reset-on-conflict enqueue doesn't update lead_id). The dedup key
    is per-chat so a re-enqueue *reset*s the timer instead of stacking rows."""
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

async def _send_client_text(chat_id: int, text: str) -> None:
    ctx = get_context()
    if ctx.customer_channel is None:
        raise RuntimeError("customer channel not wired")
    await ctx.customer_channel.send_text(int(chat_id), text)
    await _record_outbound(chat_id, text)


async def _compose_or_fallback(
    chat_id: int,
    *,
    convey: str,
    fallback: str,
    must_include: tuple[str, ...] = (),
) -> str:
    """Compose a scripted funnel touch THROUGH the customer agent so it comes out
    in the customer's language and tone — not a fixed-language template. Returns
    the text to send (does not send).

    `convey` (internal, RU) tells the agent what to say; `fallback` is the verbatim
    template used if composition is unavailable/fails (so the customer always gets
    *something*); `must_include` fragments (payment link, address, social URLs) are
    appended if the model dropped them, so critical data can never go missing.
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
    dispatch) — these are pre-scripted touches, exactly like the old templates,
    just now in the customer's language.
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
    """Mirror an out-of-band client send into the agent's conversation thread so
    the AI has the full history on handback — and into the Bitrix deal timeline
    so the operator sees it in the CRM card. Best-effort — never blocks a send."""
    ctx = get_context()
    if ctx.customer_channel is not None and note:
        try:
            await ctx.customer_channel.record_outbound(int(chat_id), note)
        except Exception:
            logger.debug("record_outbound failed for %s", chat_id, exc_info=True)
    try:
        lead = await get_repository().get_active_lead_by_chat(int(chat_id))
        if lead is not None:
            await bitrix_comment(lead, f"🤖 Клиенту: {note}")
    except Exception:
        logger.debug("bitrix outbound mirror failed for %s", chat_id, exc_info=True)


async def _send_client_photos(chat_id: int, urls: list[str]) -> None:
    urls = [u for u in urls if u]
    if not urls:
        return
    ctx = get_context()
    if ctx.customer_channel is None:
        raise RuntimeError("customer channel not wired")
    await ctx.customer_channel.send_photos(int(chat_id), urls)


async def _send_client_file_url(chat_id: int, url: str) -> None:
    if not url:
        return
    ctx = get_context()
    if ctx.customer_channel is None:
        raise RuntimeError("customer channel not wired")
    await ctx.customer_channel.send_file_url(int(chat_id), url)


async def _notify(text: str, *, reply_markup: Optional[dict] = None) -> None:
    ctx = get_context()
    if ctx.notifier is None:
        logger.warning("BYD notifier not wired; dropping operator message")
        return
    await ctx.notifier.notify_text(text, reply_markup=reply_markup)


def _unmute_row(chat_id: int) -> list[dict]:
    return [{"text": "🤖 Подключить ИИ", "callback_data": f"{UNMUTE_CALLBACK_PREFIX}{int(chat_id)}"}]


# ===== Bitrix24 mirror (durable, via the scheduled-task queue) ==============
#
# Every push to Bitrix goes through `byd_scheduled_tasks` rows (action_type
# `bitrix_*`) rather than a direct API call: the scheduler's retry/backoff makes
# delivery survive Bitrix downtime, and a transition never blocks on (or fails
# because of) the CRM. `bitrix_*` rows are exempt from cancel_pending_* — the
# mirror must record closes and never lose a queued comment. Rows are only
# enqueued when the integration is configured, so a webhook-less deploy keeps a
# clean queue.

def _bitrix_on() -> bool:
    return get_context().config.bitrix_enabled


def _bitrix_seq() -> int:
    """Millisecond sequence for unique, roughly ordered mirror dedup keys.

    Deliberately NOT coalescing per lead: a `running` row skips the
    reset-on-conflict, so reusing one key could drop the state written between a
    push's read and its completion. A row per event is always safe."""
    return int(_now().timestamp() * 1000)


async def bitrix_mark_dirty(lead_id: int, chat_id: int) -> None:
    """Enqueue a full-state push of this lead (contact + deal fields + stage)."""
    if not _bitrix_on():
        return
    await get_repository().enqueue_tasks(
        [
            {
                "lead_id": int(lead_id),
                "chat_id": int(chat_id),
                "action_type": "bitrix_sync",
                "stage": 0,
                "scheduled_for": _now(),
                "dedup_key": f"bitrix:sync:{int(lead_id)}:{_bitrix_seq()}",
                "payload": {},
            }
        ]
    )


async def bitrix_comment(
    lead: BydLead, text: str, *, event: bool = False
) -> None:
    """Queue a deal-timeline comment. `event=True` marks funnel events (payment,
    escalation, close) that post even when dialogue mirroring is switched off."""
    cfg = get_context().config
    if not _bitrix_on() or not text:
        return
    if not event and not cfg.bitrix_mirror_messages:
        return
    await get_repository().enqueue_tasks(
        [
            {
                "lead_id": lead.id,
                "chat_id": lead.chat_id,
                "action_type": "bitrix_comment",
                "stage": 0,
                "scheduled_for": _now(),
                "dedup_key": f"bitrix:comment:{lead.id}:{_bitrix_seq()}",
                "payload": {"text": text[:5000]},
            }
        ]
    )


async def _bitrix_task_action(action: str, op_task_id: int, lead: BydLead) -> None:
    if not _bitrix_on():
        return
    await get_repository().enqueue_tasks(
        [
            {
                "lead_id": lead.id,
                "chat_id": lead.chat_id,
                "action_type": action,
                "stage": 0,
                "scheduled_for": _now(),
                "dedup_key": f"bitrix:{action}:{int(op_task_id)}",
                "payload": {"op_task_id": int(op_task_id)},
            }
        ]
    )


async def bitrix_mirror_inbound(chat_id: int, text: str) -> None:
    """Mirror a customer's inbound message into the deal timeline (wired into
    the channel message_guard). No deal yet (pre-capture) → nothing to write."""
    cfg = get_context().config
    if not _bitrix_on() or not cfg.bitrix_mirror_messages or not text:
        return
    lead = await get_repository().get_active_lead_by_chat(chat_id)
    if lead is None:
        return
    await bitrix_comment(lead, f"💬 Клиент: {text}")


async def bitrix_mirror_reply(chat_id: int, text: str) -> None:
    """Mirror the AI agent's conversational reply into the deal timeline (wired
    as the customer channel's outbound_observer)."""
    cfg = get_context().config
    if not _bitrix_on() or not cfg.bitrix_mirror_messages or not text:
        return
    lead = await get_repository().get_active_lead_by_chat(chat_id)
    if lead is None:
        return
    await bitrix_comment(lead, f"🤖 Нигина: {text}")


# ===== transitions ==========================================================

async def create_lead(
    *, chat_id: int, name: str, request: str, city: str, username: str = ""
) -> BydLead:
    """Stage 1. Idempotent per chat: if a lead is already open, just enrich it;
    otherwise create it, notify operators with the call-outcome buttons, open the
    'call now' task, and schedule the 10-min reminder + 3h escalation."""
    repo = get_repository()
    existing = await repo.get_active_lead_by_chat(chat_id)
    # Only merge into an existing lead while it's still in Stage-1 capture (the
    # same person being qualified across a few messages — don't duplicate/re-notify).
    # If the chat already has a lead that ADVANCED past Stage 1, a fresh
    # register_lead is a new person — a referral ("запишите ещё брата") — so fall
    # through and create a SEPARATE lead + notify, instead of overwriting the
    # in-flight/booked deal.
    if existing is not None and existing.current_stage == STAGE_NEW:
        await repo.update_lead(
            existing.id,
            name=name or existing.name,
            request=request or existing.request,
            city=city or existing.city,
            tg_username=username or existing.tg_username,
        )
        await bitrix_mark_dirty(existing.id, chat_id)
        return existing

    lead = await repo.create_lead(
        chat_id=chat_id,
        name=name,
        request=request,
        city=city,
        tg_username=username,
        stage=STAGE_NEW,
    )

    op_task = await repo.create_operator_task(
        lead_id=lead.id,
        chat_id=chat_id,
        kind="call_now",
        title="Позвонить клиенту сейчас (срок — 10 минут).",
        due_at=_now() + timedelta(minutes=10),
    )
    await _notify(
        notif.new_lead_message(
            name=name, request=request, city=city, chat_id=chat_id, username=username
        ),
        reply_markup=notif.call_outcome_buttons(lead.id),
    )

    now = _now()
    await repo.enqueue_tasks(
        [
            _row(lead, STAGE_NEW, "s1_call_reminder", now + timedelta(minutes=10)),
            _row(lead, STAGE_NEW, "s1_escalation", now + timedelta(hours=3)),
        ]
    )
    await bitrix_mark_dirty(lead.id, chat_id)
    await _bitrix_task_action("bitrix_task", op_task.id, lead)
    logger.info("BYD lead %s created for chat %s", lead.id, chat_id)
    return lead


async def record_call_outcome(lead_id: int, *, reached: bool) -> Optional[BydLead]:
    """Operator clicked Дозвонился / Не дозвонился. Reached → Stage 3
    (negotiation handoff, mute). No answer → Stage 2 (5-touch drip)."""
    repo = get_repository()
    lead = await repo.get_lead(lead_id)
    if lead is None:
        return None

    await repo.mark_operator_acted(lead_id, "reached" if reached else "noanswer")
    await _close_open_task(lead_id, "call_now")
    # Stage-1 SLA tasks no longer relevant.
    await repo.cancel_pending_for_lead(lead_id)

    if reached:
        return await _enter_negotiation(lead)
    return await _enter_drip(lead)


async def _enter_negotiation(lead: BydLead) -> BydLead:
    repo = get_repository()
    updated = await repo.advance_stage(lead.id, STAGE_NEGOTIATION) or lead
    # The AI stays active through the funnel (so it can field questions and catch a
    # later payment claim); it's muted only once payment is confirmed (`mark_paid`)
    # or on an explicit escalation. The operator drives negotiation by phone and can
    # silently take over the chat any time by typing as the account (auto-mute).
    markup = notif.materials_keyboard(lead.id)
    await _notify(
        notif.negotiation_message(
            name=updated.name,
            request=updated.request,
            chat_id=updated.chat_id,
            username=updated.tg_username,
        ),
        reply_markup=markup,
    )
    await bitrix_mark_dirty(updated.id, updated.chat_id)
    return updated


async def _enter_drip(lead: BydLead) -> BydLead:
    repo = get_repository()
    updated = await repo.advance_stage(lead.id, STAGE_NO_ANSWER) or lead
    now = _now()
    rows = [
        _row(updated, STAGE_NO_ANSWER, "s2_touch1", now),
        _row(updated, STAGE_NO_ANSWER, "s2_touch2", now + timedelta(hours=2)),
        _row(updated, STAGE_NO_ANSWER, "s2_touch3", now + timedelta(hours=24)),
        _row(updated, STAGE_NO_ANSWER, "s2_touch4", now + timedelta(days=3)),
        _row(updated, STAGE_NO_ANSWER, "s2_touch5", now + timedelta(days=5)),
    ]
    await repo.enqueue_tasks(rows)
    await bitrix_mark_dirty(updated.id, updated.chat_id)
    logger.info("BYD lead %s entered 5-touch drip", updated.id)
    return updated


async def on_customer_reply(chat_id: int) -> bool:
    """Customer wrote back while in the no-answer drip. Cancel remaining touches
    (incl. a pending reactivation), bring a cold lead back to active, and ping the
    operator so they call and record the outcome. Returns True if it acted.

    Wired as the channel's message_guard so it runs on every inbound message
    without touching core — it returns None there (no reply), only side effects.
    """
    repo = get_repository()
    lead = await repo.get_active_lead_by_chat(chat_id)
    if lead is None or lead.current_stage != STAGE_NO_ANSWER:
        return False
    cancelled = await repo.cancel_pending_for_lead(lead.id)
    if lead.status == STATUS_COLD:
        await repo.set_status(lead.id, STATUS_ACTIVE)
    await _notify(
        "💬 <b>Клиент ответил во время дозвона</b>\n\n"
        + notif._e(lead.name or "—")
        + (f" · {notif._e(lead.request)}" if lead.request else "")
        + "\n\n"
        + notif.contact_line(lead.chat_id, lead.tg_username),
        reply_markup=notif.call_outcome_buttons(lead.id),
    )
    logger.info("BYD lead %s replied during drip (%d touches cancelled)", lead.id, cancelled)
    return True


async def note_customer_activity(chat_id: int) -> None:
    """Every inbound message (re)arms the chat-silence follow-ups (script
    §8.3/8.4): one a few hours after the client's last message, a second days
    later — then nothing (§8.5: after two attempts the initiative is the
    client's). The reset-on-conflict enqueue is what makes each new message push
    both timers back (and re-arm them for a fresh silence episode).

    Not armed when someone else owns the pacing: a human operator (mute), the
    funnel itself (any stage past Stage-1 capture — the drip/reminders have
    their own cadence), or a do-not-contact chat.
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
            _chat_row(chat_id, "chat_followup2", now + CHAT_FOLLOWUP2_DELAY),
        ]
    )


async def stop_contact(
    *, chat_id: int, reason: str = "", name: str = "", username: str = ""
) -> None:
    """The client asked us not to write again («больше не пишите», «удалите мой
    номер», a data-deletion request) — script §2.1/§10.2/§10.3/§10.6.

    Terminal for outreach: every pending scheduled touch addressed to the chat is
    cancelled and the lead — created on the spot if none exists, so the flag has
    somewhere to live — is closed with the do-not-contact marker. The chat is
    deliberately NOT muted: per §10.4 we never write first again, but the AI
    still answers normally if the client writes to us.
    """
    repo = get_repository()
    lead = await repo.get_active_lead_by_chat(chat_id)
    if lead is None:
        lead = await repo.create_lead(
            chat_id=chat_id, name=name, tg_username=username, stage=STAGE_NEW
        )
    await repo.cancel_pending_for_chat(chat_id)
    await repo.set_status(lead.id, STATUS_CLOSED, close_reason=DO_NOT_CONTACT_REASON)
    await bitrix_comment(
        lead,
        "🚫 Клиент просил больше не писать" + (f": {reason}" if reason else "")
        + ". Сделка закрыта, все рассылки отменены.",
        event=True,
    )
    await bitrix_mark_dirty(lead.id, chat_id)
    await _notify(
        notif.do_not_contact_message(
            name=name or lead.name,
            chat_id=chat_id,
            username=username or lead.tg_username,
            reason=reason,
        )
    )
    logger.info("BYD chat %s flagged do-not-contact (lead %s)", chat_id, lead.id)


async def schedule_consultation(
    *,
    lead_id: int,
    program_code: str,
    arrival: date,
    date_of_birth: Optional[date] = None,
) -> tuple[bool, str]:
    """Stage 3→4. Set program + arrival + DOB + amounts, send the client the
    immediate confirmation, and schedule the -3d reminder + -2d operator task."""
    repo = get_repository()
    lead = await repo.get_lead(lead_id)
    if lead is None:
        return False, "Сделка не найдена."
    program = await repo.get_program(program_code)
    if program is None or not program.active:
        return False, f"Программа '{program_code}' не найдена. Доступны: 7 / 14 / 21."

    total = int(program.price)
    prepayment = total * get_context().config.prepayment_percent // 100

    await repo.cancel_pending_for_lead(lead_id)
    updated = await repo.advance_stage(
        lead_id,
        STAGE_CONSULT,
        program_code=program.code,
        arrival_date=arrival,
        date_of_birth=date_of_birth,
        total_amount=total,
        prepayment_amount=prepayment,
    ) or lead

    cfg = get_context().config
    now = _now()
    rows = [_row(updated, STAGE_CONSULT, "s4_confirm", now)]
    reminder_at = _clinic_dt(arrival, 10) - timedelta(days=3)
    if reminder_at > now:
        rows.append(_row(updated, STAGE_CONSULT, "s4_reminder", reminder_at))
    operator_at = _clinic_dt(arrival, 10) - timedelta(days=2)
    if operator_at > now:
        rows.append(_row(updated, STAGE_CONSULT, "s4_operator_confirm", operator_at))
    await repo.enqueue_tasks(rows)
    await bitrix_mark_dirty(updated.id, updated.chat_id)
    return True, (
        f"Консультация назначена: программа {program.code} дней, заезд "
        f"{msg.arrival_label(arrival)}. Клиенту отправлено подтверждение."
    )


async def request_prepayment(lead_id: int) -> tuple[bool, str]:
    """Stage 4→5. Send the payment link + schedule 3 daily reminders + the
    +2h operator 'check payment' task."""
    repo = get_repository()
    lead = await repo.get_lead(lead_id)
    if lead is None:
        return False, "Сделка не найдена."

    await repo.cancel_pending_for_lead(lead_id)
    updated = await repo.advance_stage(lead_id, STAGE_PREPAYMENT) or lead
    now = _now()
    rows = [
        _row(updated, STAGE_PREPAYMENT, "s5_payment_link", now),
        _row(updated, STAGE_PREPAYMENT, "s5_reminder", now + timedelta(days=1), seq=1),
        _row(updated, STAGE_PREPAYMENT, "s5_reminder", now + timedelta(days=2), seq=2),
        _row(updated, STAGE_PREPAYMENT, "s5_reminder", now + timedelta(days=3), seq=3),
        _row(updated, STAGE_PREPAYMENT, "s5_operator_check", now + timedelta(hours=2)),
    ]
    await repo.enqueue_tasks(rows)
    await bitrix_mark_dirty(updated.id, updated.chat_id)

    # Give the operator the «Оплачено» button now (not only on the +2h check) so
    # they can confirm payment the moment it lands.
    await _notify(
        "💳 <b>Ожидаем предоплату</b>\n\n"
        f"👤 {notif._e(updated.name or '—')}\n"
        f"💵 К оплате ({get_context().config.prepayment_percent}%): "
        f"{notif.fmt_amount(updated.prepayment_amount)}\n"
        "Клиенту отправлена ссылка. Когда оплата придёт — нажмите «Оплачено».\n\n"
        + notif.contact_line(updated.chat_id, updated.tg_username),
        reply_markup=notif.paid_button(updated.id),
    )
    return True, "Клиенту отправлена ссылка на предоплату. Напоминания запланированы."


async def mark_paid(lead_id: int) -> tuple[bool, str]:
    """Stage 5→6. Stamp payment, allocate the voucher number, send the PDF
    voucher to the client + notify ops with the amount, then schedule Stage-7
    confirmation reminders off the arrival date."""
    repo = get_repository()
    lead = await repo.get_lead(lead_id)
    if lead is None:
        return False, "Сделка не найдена."
    if lead.prepayment_received_at is not None:
        return False, "Оплата уже была отмечена ранее."

    await repo.cancel_pending_for_lead(lead_id)
    voucher_number = await repo.assign_voucher_number(lead_id)
    updated = await repo.advance_stage(
        lead_id, STAGE_BOOKED, prepayment_received_at=_now()
    ) or lead

    await _send_voucher(updated, voucher_number)

    # Payment confirmed → the operator owns fulfillment/arrival from here, so mute
    # the AI (mirrors oygul's mute-on-paid). The «Подключить ИИ» button on the
    # notification below brings it back if the operator wants it. Scheduled arrival
    # reminders still fire — they bypass the mute by design.
    ctx = get_context()
    if ctx.mute_store is not None:
        try:
            await ctx.mute_store.mute(int(updated.chat_id))
        except Exception:
            logger.exception("Failed to mute chat %s after payment", updated.chat_id)

    await _notify(
        notif.payment_received_message(
            name=updated.name,
            amount=updated.prepayment_amount,
            arrival=updated.arrival_date,
            chat_id=updated.chat_id,
            username=updated.tg_username,
        ),
        reply_markup={"inline_keyboard": [_unmute_row(updated.chat_id)]},
    )

    await _enqueue_confirmation(updated)
    await bitrix_comment(
        updated,
        f"💰 Предоплата получена: {notif.fmt_amount(updated.prepayment_amount)}. "
        f"Ваучер №{voucher_number} отправлен клиенту.",
        event=True,
    )
    await bitrix_mark_dirty(updated.id, updated.chat_id)
    return True, f"Оплата отмечена. Ваучер №{voucher_number} отправлен клиенту."


async def _send_voucher(lead: BydLead, voucher_number: int) -> None:
    """Build + send the PDF voucher and the booking-confirmed caption."""
    cfg = get_context().config
    repo = get_repository()
    program = await repo.get_program(lead.program_code) if lead.program_code else None
    program_title = program.title if program else (
        f"{lead.program_code} дней" if lead.program_code else "—"
    )
    try:
        from apps.byd.voucher import build_voucher_pdf

        pdf = build_voucher_pdf(
            voucher_number=voucher_number,
            patient_name=lead.name,
            program_title=program_title,
            arrival=lead.arrival_date,
            prepayment_amount=lead.prepayment_amount,
            remaining_amount=lead.remaining_amount(),
            clinic_address=cfg.clinic_address,
        )
    except Exception:
        logger.exception("Failed to build voucher PDF for lead %s", lead.id)
        pdf = None

    arrival = msg.arrival_label(lead.arrival_date)
    caption = await _compose_or_fallback(
        lead.chat_id,
        convey=(
            "Поздравь клиента: его место в клинике BYD Medical официально "
            "забронировано! Скажи, что во вложении ваучер с деталями заезда"
            + (f", и что ждём его {arrival}" if arrival else "")
            + ". Тёпло, коротко, можно с эмодзи."
        ),
        fallback=msg.s6_voucher_caption(lead.name, lead.arrival_date),
    )
    ctx = get_context()
    if pdf and ctx.customer_channel is not None:
        try:
            await ctx.customer_channel.send_document(
                lead.chat_id, pdf, filename=f"voucher_{voucher_number}.pdf", caption=caption
            )
            await _record_outbound(
                lead.chat_id, f"[Клиенту отправлен PDF-ваучер №{voucher_number}] {caption}"
            )
            return
        except Exception:
            logger.exception("Failed to send voucher document to %s", lead.chat_id)
    # Fallback: at least send the confirmation text if the PDF/send failed.
    await _send_client_text(lead.chat_id, caption)


async def _enqueue_confirmation(lead: BydLead) -> None:
    """Stage 7 reminders, anchored to the arrival date (skip any already past)."""
    if lead.arrival_date is None:
        return
    repo = get_repository()
    cfg = get_context().config
    now = _now()
    rows: list[dict] = []
    prepare_at = _clinic_dt(lead.arrival_date, 10) - timedelta(days=5)
    if prepare_at > now:
        rows.append(_row(lead, STAGE_CONFIRMED, "s7_operator_prepare", prepare_at))
    reminder_at = _clinic_dt(lead.arrival_date, 10) - timedelta(days=3)
    if reminder_at > now:
        rows.append(_row(lead, STAGE_CONFIRMED, "s7_reminder", reminder_at))
    morning_at = _clinic_dt(lead.arrival_date, cfg.morning_message_hour)
    if morning_at > now:
        rows.append(_row(lead, STAGE_CONFIRMED, "s7_morning", morning_at))
    await repo.enqueue_tasks(rows)


async def mark_completed(lead_id: int) -> tuple[bool, str]:
    """Stage 7→8. Patient finished the course — start the post-sale chain
    (review, referral, +60d reactivation, birthday)."""
    repo = get_repository()
    lead = await repo.get_lead(lead_id)
    if lead is None:
        return False, "Сделка не найдена."

    await repo.cancel_pending_for_lead(lead_id)
    updated = await repo.advance_stage(lead_id, STAGE_DONE) or lead
    now = _now()
    rows = [
        _row(updated, STAGE_DONE, "s8_review", now + timedelta(days=2)),
        _row(updated, STAGE_DONE, "s8_referral", now + timedelta(days=2), seq=1),
        _row(updated, STAGE_DONE, "s8_reactivation", now + timedelta(days=60)),
    ]
    bday = _next_birthday_at(updated)
    if bday is not None:
        rows.append(
            _row(updated, STAGE_DONE, "s8_birthday", bday, seq=bday.year, payload={"year": bday.year})
        )
    await repo.enqueue_tasks(rows)
    await bitrix_mark_dirty(updated.id, updated.chat_id)
    return True, "Сделка переведена в «Успешно реализовано». Запущены пост-сейл сообщения."


def _next_birthday_at(lead: BydLead) -> Optional[datetime]:
    if lead.date_of_birth is None:
        return None
    cfg = get_context().config
    dob = lead.date_of_birth
    now = _now()
    year = now.year
    try:
        candidate = _clinic_dt(date(year, dob.month, dob.day), cfg.birthday_message_hour)
    except ValueError:  # Feb 29 → use Mar 1
        candidate = _clinic_dt(date(year, dob.month, 28), cfg.birthday_message_hour) + timedelta(days=1)
    if candidate <= now:
        try:
            candidate = _clinic_dt(date(year + 1, dob.month, dob.day), cfg.birthday_message_hour)
        except ValueError:
            candidate = _clinic_dt(date(year + 1, dob.month, 28), cfg.birthday_message_hour) + timedelta(days=1)
    return candidate


async def escalate_to_human(
    *, chat_id: int, reason: str, name: str = "", username: str = ""
) -> None:
    """The AI can't handle this customer — hand off to a human operator.

    Mirrors oygul's call_human (notify operators with the reason + contact) and,
    because BYD operators work through the operator bot rather than by typing as
    the userbot, also mutes the chat so the AI goes silent and the operator owns
    it. The «Подключить ИИ» button hands control back. Works with or without a
    lead (the AI may escalate before Stage-1 capture completes).
    """
    repo = get_repository()
    lead = await repo.get_active_lead_by_chat(chat_id)
    msg_name = name or (lead.name if lead else "")
    uname = username or (lead.tg_username if lead else "")
    await _notify(
        notif.handoff_message(
            name=msg_name,
            reason=reason,
            chat_id=chat_id,
            username=uname,
            lead_id=lead.id if lead else None,
            stage_title=STAGE_TITLES_RU.get(lead.current_stage) if lead else None,
        ),
        reply_markup={"inline_keyboard": [_unmute_row(chat_id)]},
    )
    ctx = get_context()
    if ctx.mute_store is not None:
        try:
            await ctx.mute_store.mute(int(chat_id))
        except Exception:
            logger.exception("Failed to mute chat %s on human escalation", chat_id)
    if lead is not None:
        await bitrix_comment(
            lead, f"🚨 ИИ передал диалог оператору. Причина: {reason or '—'}", event=True
        )
    logger.info("BYD escalated chat %s to a human operator", chat_id)


async def report_payment_claim(
    *, chat_id: int, note: str = "", name: str = "", username: str = ""
) -> None:
    """The customer says they've paid the prepayment (or sent a screenshot).

    We do **not** trust this as confirmation — in this clinic a payment is verified
    by an operator against the payment system, not by the customer's word. So this
    only alerts an operator to check and confirm with «Оплачено». It never advances
    the stage, allocates a voucher, or marks the lead paid (that's `mark_paid`,
    fired by the operator's button / manager tool). Works with or without a lead.
    """
    repo = get_repository()
    lead = await repo.get_active_lead_by_chat(chat_id)
    msg_name = name or (lead.name if lead else "")
    uname = username or (lead.tg_username if lead else "")
    await _notify(
        notif.payment_claim_message(
            name=msg_name,
            note=note,
            chat_id=chat_id,
            username=uname,
            lead_id=lead.id if lead else None,
            amount=lead.prepayment_amount if lead else None,
        ),
        # The «Оплачено» button needs a lead to act on; omit it if we have none.
        reply_markup=notif.paid_button(lead.id) if lead is not None else None,
    )
    if lead is not None:
        await bitrix_comment(
            lead,
            "💸 Клиент сообщает об оплате" + (f": {note}" if note else "")
            + ". Требуется проверка поступления.",
            event=True,
        )
    logger.info(
        "BYD payment claim reported for chat %s (lead %s)",
        chat_id, lead.id if lead else None,
    )


async def send_material(lead_id: int, kind: str) -> tuple[bool, str]:
    """Stage 3 materials library — send one asset to the client."""
    repo = get_repository()
    lead = await repo.get_lead(lead_id)
    if lead is None:
        return False, "Сделка не найдена."
    cfg = get_context().config
    try:
        if kind == "photos":
            if not cfg.clinic_photo_urls:
                return False, "Фото клиники не настроены (BYD_CLINIC_PHOTO_URLS)."
            await _send_client_photos(lead.chat_id, list(cfg.clinic_photo_urls))
        elif kind == "tour":
            if not cfg.tour_video_url:
                return False, "Видео-обзор не настроен (BYD_TOUR_VIDEO_URL)."
            await _send_client_file_url(lead.chat_id, cfg.tour_video_url)
        elif kind == "testimonial":
            if not cfg.testimonial_video_url:
                return False, "Видео-отзыв не настроен (BYD_TESTIMONIAL_VIDEO_URL)."
            await _send_client_file_url(lead.chat_id, cfg.testimonial_video_url)
        elif kind == "price":
            if not cfg.price_image_url:
                return False, "Прайс-лист не настроен (BYD_PRICE_IMAGE_URL)."
            await _send_client_photos(lead.chat_id, [cfg.price_image_url])
        elif kind == "before_after":
            if not cfg.before_after_url:
                return False, "Фото до/после не настроены (BYD_BEFORE_AFTER_URL)."
            await _send_client_photos(lead.chat_id, [cfg.before_after_url])
        else:
            return False, f"Неизвестный материал: {kind}"
    except Exception as exc:
        logger.exception("Failed to send material %s for lead %s", kind, lead_id)
        return False, f"Не удалось отправить материал: {exc}"
    _labels = {
        "photos": "фото клиники", "tour": "видео-обзор", "testimonial": "видео-отзыв",
        "price": "прайс-лист", "before_after": "фото до/после",
    }
    await _record_outbound(lead.chat_id, f"[Клиенту отправлен материал: {_labels.get(kind, kind)}]")
    return True, "Материал отправлен клиенту."


async def _close_open_task(lead_id: int, kind: str) -> None:
    """Best-effort: close the most recent open operator task of a kind."""
    try:
        from sqlalchemy import select

        from apps.byd.models import BydOperatorTask
        from db.engine import get_sessionmaker

        async with get_sessionmaker()() as session:
            task_id = await session.scalar(
                select(BydOperatorTask.id).where(
                    BydOperatorTask.lead_id == int(lead_id),
                    BydOperatorTask.kind == kind,
                    BydOperatorTask.status == "open",
                ).order_by(BydOperatorTask.created_at.desc()).limit(1)
            )
        if task_id is not None:
            await close_operator_task(task_id)
    except Exception:
        logger.debug("close_open_task(%s, %s) failed", lead_id, kind, exc_info=True)


async def close_operator_task(task_id: int) -> bool:
    """Close a local operator task and queue completion of its Bitrix mirror.
    The single entry point for closing tasks (inline «Выполнено» button included)
    so the CRM copy can never be forgotten."""
    repo = get_repository()
    closed = await repo.close_operator_task(task_id)
    if closed:
        task = await repo.get_operator_task(task_id)
        lead = await repo.get_lead(task.lead_id) if task else None
        if task is not None and lead is not None:
            await _bitrix_task_action("bitrix_task_complete", task.id, lead)
    return closed


async def _operator_task_notify(
    lead: BydLead, *, kind: str, title: str, markup: Optional[dict] = None
) -> None:
    """Create an operator task row + fan it out. Defaults to a 'done' button;
    pass `markup` for a task whose action is something else (e.g. the
    check-payment task carries the «Оплачено» button instead)."""
    repo = get_repository()
    task = await repo.create_operator_task(
        lead_id=lead.id, chat_id=lead.chat_id, kind=kind, title=title
    )
    await _notify(
        notif.operator_task_message(
            title=title, name=lead.name, chat_id=lead.chat_id, username=lead.tg_username
        ),
        reply_markup=markup or notif.task_done_button(task.id),
    )
    await _bitrix_task_action("bitrix_task", task.id, lead)


# ===== pull-side transitions (operator acted in Bitrix, not Telegram) =======

async def notify_operators(text: str, *, reply_markup: Optional[dict] = None) -> None:
    """Public wrapper for out-of-funnel callers (the Bitrix pull job)."""
    await _notify(text, reply_markup=reply_markup)


async def apply_remote_details(
    lead_id: int,
    *,
    program_code: Optional[str] = None,
    arrival: Optional[date] = None,
    dob: Optional[date] = None,
) -> None:
    """Deal-card field edits made by an operator in Bitrix, pulled onto the
    lead. Only ever *sets* values (an emptied Bitrix field is passed as None and
    ignored). The program recomputes amounts while unpaid; an arrival move
    re-anchors the date-driven reminders; a DOB landing after Stage 8 still
    arms the birthday greeting."""
    repo = get_repository()
    lead = await repo.get_lead(lead_id)
    if lead is None:
        return
    fields: dict = {}
    if program_code and program_code != (lead.program_code or ""):
        program = await repo.get_program(program_code)
        if program is not None and program.active and lead.prepayment_received_at is None:
            total = int(program.price)
            fields["program_code"] = program.code
            fields["total_amount"] = total
            fields["prepayment_amount"] = (
                total * get_context().config.prepayment_percent // 100
            )
    if dob is not None and dob != lead.date_of_birth:
        fields["date_of_birth"] = dob
    arrival_changed = arrival is not None and arrival != lead.arrival_date
    if arrival_changed:
        fields["arrival_date"] = arrival
    if not fields:
        return
    updated = await repo.update_lead(lead_id, **fields)
    if updated is None:
        return
    if arrival_changed:
        await _reanchor_arrival(updated)
    if "date_of_birth" in fields and updated.current_stage == STAGE_DONE:
        bday = _next_birthday_at(updated)
        if bday is not None:
            await repo.enqueue_tasks(
                [_row(updated, STAGE_DONE, "s8_birthday", bday, seq=bday.year,
                      payload={"year": bday.year})]
            )
    logger.info("BYD lead %s updated from Bitrix: %s", lead_id, sorted(fields))
    await bitrix_mark_dirty(updated.id, updated.chat_id)


async def _reanchor_arrival(lead: BydLead) -> None:
    """The arrival date moved — cancel and re-enqueue the reminders anchored to
    it for the lead's current stage (never re-sends the one-off confirmations)."""
    if lead.arrival_date is None:
        return
    repo = get_repository()
    now = _now()
    if lead.current_stage in (STAGE_CONSULT, STAGE_PREPAYMENT):
        await repo.cancel_pending_actions(lead.id, {"s4_reminder", "s4_operator_confirm"})
        rows = []
        reminder_at = _clinic_dt(lead.arrival_date, 10) - timedelta(days=3)
        if reminder_at > now:
            rows.append(_row(lead, STAGE_CONSULT, "s4_reminder", reminder_at))
        operator_at = _clinic_dt(lead.arrival_date, 10) - timedelta(days=2)
        if operator_at > now:
            rows.append(_row(lead, STAGE_CONSULT, "s4_operator_confirm", operator_at))
        await repo.enqueue_tasks(rows)
    elif lead.current_stage in (STAGE_BOOKED, STAGE_CONFIRMED):
        await repo.cancel_pending_actions(
            lead.id, {"s7_operator_prepare", "s7_reminder", "s7_morning"}
        )
        await _enqueue_confirmation(lead)


async def advance_to_confirmation(lead_id: int) -> None:
    """Bitrix stage 7 («Подтверждение брони»). Ensures payment side effects ran
    (an operator may drag 5→7 in one go), then re-labels and (re)arms the
    arrival reminders."""
    repo = get_repository()
    lead = await repo.get_lead(lead_id)
    if lead is None:
        return
    if lead.prepayment_received_at is None:
        await mark_paid(lead_id)
        lead = await repo.get_lead(lead_id) or lead
    updated = await repo.advance_stage(lead_id, STAGE_CONFIRMED) or lead
    await _enqueue_confirmation(updated)
    await bitrix_mark_dirty(updated.id, updated.chat_id)


async def close_from_bitrix(lead_id: int, reason: str) -> None:
    """An operator moved the deal to a failure stage in Bitrix — close the lead
    and stop every scheduled client touch."""
    repo = get_repository()
    lead = await repo.get_lead(lead_id)
    if lead is None or lead.status == STATUS_CLOSED:
        return
    await repo.cancel_pending_for_lead(lead_id)
    await repo.set_status(lead_id, STATUS_CLOSED, close_reason=(reason or "Закрыто в Bitrix")[:255])
    await _notify(
        "📕 <b>Сделка закрыта в Bitrix</b>\n\n"
        f"👤 {notif._e(lead.name or '—')}\n"
        f"📝 {notif._e(reason or '—')}\n\n"
        + notif.contact_line(lead.chat_id, lead.tg_username)
    )
    logger.info("BYD lead %s closed from Bitrix: %s", lead_id, reason)


# ===== payment link =========================================================

def payment_link(lead: BydLead) -> str:
    """Click.uz pay URL for the lead's prepayment amount (mirrors oygul). Falls
    back to the website if Click isn't configured."""
    from urllib.parse import urlencode

    cfg = get_context().config
    amount = lead.prepayment_amount or 0
    if not cfg.click_service_id or not cfg.click_merchant_id:
        return cfg.website_url
    params = {
        "service_id": cfg.click_service_id,
        "merchant_id": cfg.click_merchant_id,
        "amount": f"{amount:.2f}",
        "transaction_param": cfg.click_transaction_param or str(lead.id),
        "return_url": cfg.click_return_url,
    }
    return f"https://my.click.uz/services/pay/?{urlencode(params)}"


# ===== action executors (the scheduler fires these) =========================

async def _exec_s1_call_reminder(lead: BydLead, task: BydScheduledTask) -> None:
    if lead.current_stage != STAGE_NEW or lead.operator_first_action_at is not None:
        return  # operator already acted → nothing to nudge
    await _notify(
        notif.call_reminder_message(
            name=lead.name, chat_id=lead.chat_id, username=lead.tg_username
        ),
        reply_markup=notif.call_outcome_buttons(lead.id),
    )


async def _exec_s1_escalation(lead: BydLead, task: BydScheduledTask) -> None:
    if lead.current_stage != STAGE_NEW or lead.operator_first_action_at is not None:
        return
    await _notify(
        notif.escalation_message(
            name=lead.name, chat_id=lead.chat_id, username=lead.tg_username
        ),
        reply_markup=notif.call_outcome_buttons(lead.id),
    )


async def _exec_s2_touch1(lead: BydLead, task: BydScheduledTask) -> None:
    await _send_client_directed(
        lead.chat_id,
        convey=(
            "Мы пытались дозвониться до клиента, но не дозвонились. Напиши короткое "
            "тёплое сообщение: пытались до него дозвониться, и спроси, когда ему "
            "удобно поговорить."
        ),
        fallback=msg.s2_touch1(lead.name),
    )


async def _exec_s2_touch2(lead: BydLead, task: BydScheduledTask) -> None:
    cfg = get_context().config
    await _send_client_directed(
        lead.chat_id,
        convey=(
            "Пока ждём ответа — напиши короткое тёплое подводящее сообщение о том, "
            "что отправляешь клиенту информацию о наших программах (сразу после "
            "сообщения ему придут материалы)."
        ),
        fallback=msg.s2_touch2_caption(lead.name),
    )
    extras: list[str] = []
    if cfg.price_image_url:
        await _send_client_photos(lead.chat_id, [cfg.price_image_url])
        extras.append("прайс-лист")
    if cfg.testimonial_video_url:
        await _send_client_file_url(lead.chat_id, cfg.testimonial_video_url)
        extras.append("видео-отзыв")
    if extras:
        await _record_outbound(lead.chat_id, "[Клиенту отправлены: " + ", ".join(extras) + "]")


async def _exec_s2_touch3(lead: BydLead, task: BydScheduledTask) -> None:
    await _send_client_directed(
        lead.chat_id,
        convey=(
            "Напиши тёплое, эмпатичное сообщение: многие привыкают к усталости и "
            "тяжести в теле и считают это нормой, но это не норма — организм просит "
            "о помощи. Многие наши пациенты тоже откладывали («подожду, само "
            "пройдёт»), а потом жалели, что не пришли раньше. Мягко поддержи и "
            "предложи просто написать/поговорить."
        ),
        fallback=msg.s2_touch3(lead.name),
    )


async def _exec_s2_touch4(lead: BydLead, task: BydScheduledTask) -> None:
    await _send_client_directed(
        lead.chat_id,
        convey=(
            f"Сообщи, что на {msg.current_month_nom()} осталось несколько свободных "
            "мест, и предложи клиенту забронировать место, пока оно есть. Коротко и тёпло."
        ),
        fallback=msg.s2_touch4(lead.name),
    )


async def _exec_s2_touch5(lead: BydLead, task: BydScheduledTask) -> None:
    await _send_client_directed(
        lead.chat_id,
        convey=(
            "Сообщи, что сейчас идёт акция на программы очищения: если клиент "
            "забронирует в этом месяце — специальные условия. Попроси рассказать, "
            "что его беспокоит, чтобы подобрать программу именно под него."
        ),
        fallback=msg.s2_touch5(lead.name),
    )
    # Final touch done with no reply → cold archive + one +30d reactivation.
    repo = get_repository()
    await repo.set_status(lead.id, STATUS_COLD)
    await repo.enqueue_tasks(
        [_row(lead, STAGE_NO_ANSWER, "s2_reactivation", _now() + timedelta(days=30))]
    )
    await bitrix_comment(
        lead,
        "❄️ 5 касаний без ответа — холодный архив. Через 30 дней уйдёт "
        "финальная попытка реактивации.",
        event=True,
    )


async def _exec_s2_reactivation(lead: BydLead, task: BydScheduledTask) -> None:
    await _send_client_directed(
        lead.chat_id,
        convey=(
            "Напомни о себе: клиент интересовался программами очищения в BYD Medical. "
            "Если для него это всё ещё актуально — будем рады подобрать удобное время, "
            "пусть напишет. Тёпло и ненавязчиво."
        ),
        fallback=msg.s2_reactivation(lead.name),
    )
    # The single final attempt — close the lead as lost afterwards.
    repo = get_repository()
    await repo.set_status(lead.id, STATUS_CLOSED, close_reason="Холодный архив: нет ответа")
    await bitrix_mark_dirty(lead.id, lead.chat_id)


async def _chat_followup_allowed(task: BydScheduledTask) -> bool:
    """Fire-time recheck for a chat-silence follow-up: the situation may have
    changed since it was armed (operator took over, funnel advanced, client
    opted out) — those all silently swallow the touch."""
    ctx = get_context()
    if ctx.mute_store is not None and await ctx.mute_store.is_muted(int(task.chat_id)):
        return False
    lead = await get_repository().get_latest_lead_by_chat(task.chat_id)
    if lead is None:
        return True
    if lead.status == STATUS_CLOSED:
        return lead.close_reason != DO_NOT_CONTACT_REASON
    return lead.current_stage == STAGE_NEW


async def _exec_chat_followup1(lead: Optional[BydLead], task: BydScheduledTask) -> None:
    if not await _chat_followup_allowed(task):
        return
    await _send_client_directed(
        task.chat_id,
        convey=(
            "Клиент давно не отвечает в переписке. Мягко напомни о себе: если "
            "остались вопросы по программам — ты на связи, с радостью подскажешь. "
            "Одно короткое тёплое сообщение, без давления."
        ),
        fallback=msg.chat_followup1(),
    )


async def _exec_chat_followup2(lead: Optional[BydLead], task: BydScheduledTask) -> None:
    if not await _chat_followup_allowed(task):
        return
    await _send_client_directed(
        task.chat_id,
        convey=(
            "Клиент так и не ответил после напоминания. Напиши последнее "
            "ненавязчивое сообщение: не торопишь, просто на связи — если вопрос "
            "ещё актуален, пусть обращается в любое время. Без давления; после "
            "этого мы больше не пишем первыми."
        ),
        fallback=msg.chat_followup2(),
    )


async def _exec_s4_confirm(lead: BydLead, task: BydScheduledTask) -> None:
    arrival = msg.arrival_label(lead.arrival_date)
    await _send_client_directed(
        lead.chat_id,
        convey=(
            "Подтверди клиенту, что всё отлично и врач будет его ждать"
            + (f" {arrival}" if arrival else "")
            + ". При заезде врач проведёт личную консультацию и подберёт программу "
            "именно под него. Тёпло и коротко."
        ),
        fallback=msg.s4_confirm(lead.name, lead.arrival_date),
    )


async def _exec_s4_reminder(lead: BydLead, task: BydScheduledTask) -> None:
    cfg = get_context().config
    await _send_client_directed(
        lead.chat_id,
        convey=(
            "Напомни, что заезд в клинику BYD Medical через 3 дня. "
            f"Адрес: {cfg.clinic_address}. Время заезда: с {cfg.arrival_time}. "
            "Если есть вопросы — пусть пишут."
        ),
        fallback=msg.s4_reminder(lead.name, cfg),
        must_include=(cfg.clinic_address,),
    )


async def _exec_s4_operator_confirm(lead: BydLead, task: BydScheduledTask) -> None:
    await _operator_task_notify(
        lead,
        kind="confirm",
        title=(
            f"Клиент {lead.name or '—'} должен приехать послезавтра "
            f"({msg.arrival_label(lead.arrival_date)}) — уточни подтверждение "
            "и убедись что всё готово."
        ),
    )


async def _exec_s5_payment_link(lead: BydLead, task: BydScheduledTask) -> None:
    cfg = get_context().config
    url = payment_link(lead)
    await _send_client_directed(
        lead.chat_id,
        convey=(
            "Сообщи, что для подтверждения места в клинике нужна предоплата "
            f"{cfg.prepayment_percent}% от стоимости программы, и дай ссылку для "
            "оплаты. После оплаты место будет официально забронировано. "
            f"Вставь ссылку точно как есть, отдельной строкой: {url}"
        ),
        fallback=msg.s5_payment_request(lead.name, url, cfg),
        must_include=(url,),
    )


async def _exec_s5_reminder(lead: BydLead, task: BydScheduledTask) -> None:
    fresh = await get_repository().get_lead(lead.id)
    if fresh and fresh.prepayment_received_at is not None:
        return  # already paid → stop reminding
    url = payment_link(fresh or lead)
    await _send_client_directed(
        lead.chat_id,
        convey=(
            "Мягко напомни, что место клиента ещё свободно, но мест мало — не хотим, "
            "чтобы он его потерял. Дай ссылку для оплаты. "
            f"Вставь ссылку точно как есть, отдельной строкой: {url}"
        ),
        fallback=msg.s5_payment_reminder(lead.name, url),
        must_include=(url,),
    )


async def _exec_s5_operator_check(lead: BydLead, task: BydScheduledTask) -> None:
    fresh = await get_repository().get_lead(lead.id)
    if fresh and fresh.prepayment_received_at is not None:
        return
    await _operator_task_notify(
        lead,
        kind="check_payment",
        title=f"Проверь оплату от клиента {lead.name or '—'} — если не оплатил, позвони и уточни.",
        markup=notif.paid_button(lead.id),
    )


async def _exec_s7_operator_prepare(lead: BydLead, task: BydScheduledTask) -> None:
    await _operator_task_notify(
        lead,
        kind="prepare",
        title=(
            f"Пациент {lead.name or '—'} приезжает {msg.arrival_label(lead.arrival_date)} "
            "— уточни подтверждение и подготовь всё необходимое."
        ),
    )


async def _exec_s7_reminder(lead: BydLead, task: BydScheduledTask) -> None:
    cfg = get_context().config
    await _send_client_directed(
        lead.chat_id,
        convey=(
            "Напомни, что заезд в клинику BYD Medical через 3 дня. "
            f"Адрес: {cfg.clinic_address}. Время заезда: с {cfg.arrival_time}. "
            "Если есть вопросы — пусть пишут."
        ),
        fallback=msg.s7_reminder(lead.name, cfg),
        must_include=(cfg.clinic_address,),
    )


async def _exec_s7_morning(lead: BydLead, task: BydScheduledTask) -> None:
    cfg = get_context().config
    await _send_client_directed(
        lead.chat_id,
        convey=(
            "Доброе утро! Напиши, что сегодня ждём клиента в клинике BYD Medical, "
            f"заезд с {cfg.arrival_time}. Если нужна помощь — пусть звонят."
        ),
        fallback=msg.s7_morning(lead.name, cfg),
    )


async def _exec_s8_review(lead: BydLead, task: BydScheduledTask) -> None:
    cfg = get_context().config
    await _send_client_directed(
        lead.chat_id,
        convey=(
            "Спроси, как клиент себя чувствует после программы, и попроси оставить "
            "отзыв (это важно для нас, займёт 1 минуту). Дай ссылки, каждую вставь "
            f"как есть: Instagram — {cfg.instagram_url}; наша группа — "
            f"{cfg.community_url}; сайт — {cfg.website_url}."
        ),
        fallback=msg.s8_review(lead.name, cfg),
        must_include=(cfg.instagram_url, cfg.community_url, cfg.website_url),
    )


async def _exec_s8_referral(lead: BydLead, task: BydScheduledTask) -> None:
    await _send_client_directed(
        lead.chat_id,
        convey=(
            "Предложи: если близким клиента тоже нужна помощь — пусть порекомендует "
            "нас. За каждого приведённого друга — скидка 10% на его следующий курс."
        ),
        fallback=msg.s8_referral(lead.name),
    )


async def _exec_s8_reactivation(lead: BydLead, task: BydScheduledTask) -> None:
    await _send_client_directed(
        lead.chat_id,
        convey=(
            "Спроси о самочувствии клиента и предложи бесплатную онлайн-консультацию "
            "с нашими врачами — спроси, когда ему удобно."
        ),
        fallback=msg.s8_reactivation(lead.name),
    )


async def _exec_s8_birthday(lead: BydLead, task: BydScheduledTask) -> None:
    await _send_client_directed(
        lead.chat_id,
        convey=(
            "Поздравь клиента с днём рождения! В честь праздника — скидка 10% на "
            "следующий курс в BYD Medical. Пожелай здоровья и сил."
        ),
        fallback=msg.s8_birthday(lead.name),
    )
    # Re-enqueue next year's greeting (idempotent: dedup_key carries the year).
    nxt = _next_birthday_at(lead)
    if nxt is not None:
        await get_repository().enqueue_tasks(
            [_row(lead, STAGE_DONE, "s8_birthday", nxt, seq=nxt.year, payload={"year": nxt.year})]
        )


# ----- Bitrix mirror executors (lazy import: bitrix.py never imports funnel) --

async def _exec_bitrix_sync(lead: BydLead, task: BydScheduledTask) -> None:
    from apps.byd import bitrix

    await bitrix.sync_lead(lead)


async def _exec_bitrix_comment(lead: BydLead, task: BydScheduledTask) -> None:
    from apps.byd import bitrix

    await bitrix.post_lead_comment(lead, (task.payload or {}).get("text") or "")


async def _exec_bitrix_task(lead: BydLead, task: BydScheduledTask) -> None:
    from apps.byd import bitrix

    await bitrix.push_operator_task(int((task.payload or {})["op_task_id"]), lead)


async def _exec_bitrix_task_complete(lead: BydLead, task: BydScheduledTask) -> None:
    from apps.byd import bitrix

    await bitrix.complete_operator_task(int((task.payload or {})["op_task_id"]))


ACTIONS = {
    "bitrix_sync": _exec_bitrix_sync,
    "bitrix_comment": _exec_bitrix_comment,
    "bitrix_task": _exec_bitrix_task,
    "bitrix_task_complete": _exec_bitrix_task_complete,
    "chat_followup1": _exec_chat_followup1,
    "chat_followup2": _exec_chat_followup2,
    "s1_call_reminder": _exec_s1_call_reminder,
    "s1_escalation": _exec_s1_escalation,
    "s2_touch1": _exec_s2_touch1,
    "s2_touch2": _exec_s2_touch2,
    "s2_touch3": _exec_s2_touch3,
    "s2_touch4": _exec_s2_touch4,
    "s2_touch5": _exec_s2_touch5,
    "s2_reactivation": _exec_s2_reactivation,
    "s4_confirm": _exec_s4_confirm,
    "s4_reminder": _exec_s4_reminder,
    "s4_operator_confirm": _exec_s4_operator_confirm,
    "s5_payment_link": _exec_s5_payment_link,
    "s5_reminder": _exec_s5_reminder,
    "s5_operator_check": _exec_s5_operator_check,
    "s7_operator_prepare": _exec_s7_operator_prepare,
    "s7_reminder": _exec_s7_reminder,
    "s7_morning": _exec_s7_morning,
    "s8_review": _exec_s8_review,
    "s8_referral": _exec_s8_referral,
    "s8_reactivation": _exec_s8_reactivation,
    "s8_birthday": _exec_s8_birthday,
}


async def execute_task(task: BydScheduledTask) -> None:
    """Run one claimed task. Raises on failure so the scheduler can retry/park it.

    Skips leads that have gone terminal (closed) since the task was scheduled —
    a defensive backstop on top of cancel-on-close.
    """
    action = ACTIONS.get(task.action_type)
    if action is None:
        logger.error("Unknown BYD action_type %r (task %s)", task.action_type, task.id)
        return
    if not task.lead_id:
        # Chat-addressed task (lead_id=0) — armed before any lead existed. The
        # executor does its own fire-time checks against the chat's latest state.
        await action(None, task)
        return
    lead = await get_repository().get_lead(task.lead_id)
    if lead is None:
        logger.warning("BYD task %s references missing lead %s", task.id, task.lead_id)
        return
    # `bitrix_*` actions run even for a closed lead — the close itself must
    # reach the CRM. The closed-skip guards only client-facing touches.
    if lead.status == STATUS_CLOSED and not task.action_type.startswith("bitrix_"):
        logger.info("BYD task %s skipped — lead %s is closed", task.id, lead.id)
        return
    await action(lead, task)
