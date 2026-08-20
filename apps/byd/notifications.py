"""Operator/manager notifications (Russian HTML) + the inline-button contracts.

Operators get their funnel "tasks" as Telegram messages from the operator bot
(via `TelegramOperatorNotifier`, the Bot HTTP API). The inline buttons below are
how the system learns things it otherwise can't see — whether a call connected
(no telephony!), whether payment landed — and how single-tap stage transitions
fire. Callback-data prefixes are the shared contract with the handlers registered
on the operator bot in `apps.byd.main`.
"""

from __future__ import annotations

import html
from datetime import date
from typing import Optional

from apps.byd.messages import arrival_label

# ----- callback-data prefixes (operator bot inline buttons) -----------------
CALL_CALLBACK_PREFIX = "bydcall:"      # bydcall:reached:<lead_id> / bydcall:noanswer:<lead_id>
PAID_CALLBACK_PREFIX = "bydpaid:"      # bydpaid:<lead_id>
TASK_DONE_CALLBACK_PREFIX = "bydtask:"  # bydtask:<task_id>
MATERIAL_CALLBACK_PREFIX = "bydmat:"   # bydmat:<kind>:<lead_id>

# Material kinds offered in the Stage-3 library button row.
MATERIAL_KINDS = ("photos", "tour", "testimonial", "price", "before_after")
_MATERIAL_LABELS = {
    "photos": "📸 Фото клиники",
    "tour": "🎥 Видео-обзор",
    "testimonial": "🎥 Отзывы пациентов",
    "price": "💰 Прайс-лист",
    "before_after": "📊 До / после",
}


def _e(value) -> str:
    return html.escape("" if value is None else str(value))


def fmt_amount(amount: Optional[int]) -> str:
    """Thousands-separated UZS sum, e.g. '1 500 000 сум'."""
    if amount is None:
        return "—"
    return f"{int(amount):,}".replace(",", " ") + " сум"


# ----- contact line (reused across notifications) ---------------------------

def contact_line(chat_id: Optional[int], username: Optional[str]) -> str:
    username = (username or "").strip()
    if not chat_id and not username:
        return ""
    bits: list[str] = []
    if username:
        u = _e(username)
        bits.append(f'<a href="https://t.me/{u}">@{u}</a>')
    if chat_id:
        i = int(chat_id)
        bits.append(f'<a href="tg://user?id={i}">открыть профиль</a>')
        bits.append(f"id: <code>{i}</code>")
    return "📞 Контакт клиента (Telegram):\n" + " · ".join(bits)


# ----- inline-keyboard builders ---------------------------------------------

def call_outcome_buttons(lead_id: int) -> dict:
    """Stage 1: did the operator reach the client? Drives 1→3 (reached) or
    1→2 (no answer) — the trigger the system can't observe (calls are off-platform)."""
    return {
        "inline_keyboard": [
            [
                {"text": "📞 Дозвонился", "callback_data": f"{CALL_CALLBACK_PREFIX}reached:{int(lead_id)}"},
                {"text": "📵 Не дозвонился", "callback_data": f"{CALL_CALLBACK_PREFIX}noanswer:{int(lead_id)}"},
            ]
        ]
    }


def paid_button(lead_id: int) -> dict:
    """Stage 5→6: operator confirms prepayment landed."""
    return {
        "inline_keyboard": [
            [{"text": "💰 Оплачено", "callback_data": f"{PAID_CALLBACK_PREFIX}{int(lead_id)}"}]
        ]
    }


def task_done_button(task_id: int) -> dict:
    return {
        "inline_keyboard": [
            [{"text": "✅ Выполнено", "callback_data": f"{TASK_DONE_CALLBACK_PREFIX}{int(task_id)}"}]
        ]
    }


def materials_keyboard(lead_id: int) -> dict:
    """Stage 3: one-tap library — each button sends that material to the client."""
    row1 = [
        {"text": _MATERIAL_LABELS[k], "callback_data": f"{MATERIAL_CALLBACK_PREFIX}{k}:{int(lead_id)}"}
        for k in ("photos", "tour", "testimonial")
    ]
    row2 = [
        {"text": _MATERIAL_LABELS[k], "callback_data": f"{MATERIAL_CALLBACK_PREFIX}{k}:{int(lead_id)}"}
        for k in ("price", "before_after")
    ]
    return {"inline_keyboard": [row1, row2]}


# ----- message bodies -------------------------------------------------------

def new_lead_message(
    *, name: str, request: str, city: str, chat_id: int, username: Optional[str]
) -> str:
    sections = [
        "🔔 <b>Новая заявка</b>",
        "👤 Имя: " + _e(name or "—"),
        "📋 Что беспокоит: " + _e(request or "—"),
        "🏙 Город: " + _e(city or "—"),
    ]
    contact = contact_line(chat_id, username)
    if contact:
        sections.append(contact)
    sections.append("⏱ Задача: позвонить в течение 10 минут.")
    return "\n\n".join(sections)


def call_reminder_message(*, name: str, chat_id: int, username: Optional[str]) -> str:
    sections = [
        "📞 <b>Прошло 10 минут — позвоните клиенту</b>",
        "👤 " + _e(name or "—"),
    ]
    contact = contact_line(chat_id, username)
    if contact:
        sections.append(contact)
    return "\n\n".join(sections)


def escalation_message(*, name: str, chat_id: int, username: Optional[str]) -> str:
    sections = [
        "⚠️ <b>Лид не обработан 3 часа</b>",
        "Никто не зафиксировал результат звонка по заявке:",
        "👤 " + _e(name or "—"),
    ]
    contact = contact_line(chat_id, username)
    if contact:
        sections.append(contact)
    return "\n\n".join(sections)


def operator_task_message(*, title: str, name: str, chat_id: int, username: Optional[str]) -> str:
    sections = ["📋 <b>Задача оператору</b>", _e(title)]
    sections.append("👤 Клиент: " + _e(name or "—"))
    contact = contact_line(chat_id, username)
    if contact:
        sections.append(contact)
    return "\n\n".join(sections)


def negotiation_message(*, name: str, request: str, chat_id: int, username: Optional[str]) -> str:
    """Stage 3: SPIN script + the materials library, shown to the operator who
    now owns the conversation."""
    spin = (
        "<b>СПИН-скрипт:</b>\n"
        "• С — Ситуация: как давно беспокоит? что уже пробовали?\n"
        "• П — Проблема: что беспокоит больше всего? как влияет на жизнь?\n"
        "• И — Последствия: что будет если не решить? есть важные события?\n"
        "• Н — Выгода: если бы всё прошло — как бы себя чувствовали?\n"
        "• Презентация: «вы сказали … — наша программа решает именно это»\n"
        "• Закрытие: когда удобно приехать? есть места на …"
    )
    sections = [
        "🗣 <b>Переговоры — клиент на связи</b>",
        "👤 " + _e(name or "—") + (f" · {_e(request)}" if request else ""),
    ]
    contact = contact_line(chat_id, username)
    if contact:
        sections.append(contact)
    sections.append(spin)
    sections.append("Ниже — кнопки, чтобы отправить клиенту материалы 👇")
    return "\n\n".join(sections)


def handoff_message(
    *,
    name: str,
    reason: str,
    chat_id: int,
    username: Optional[str],
    lead_id: Optional[int] = None,
    stage_title: Optional[str] = None,
) -> str:
    """The AI escalated — a human operator should take over this chat."""
    sections = ["🚨 <b>Нужен оператор — ИИ не справляется</b>", "👤 " + _e(name or "—")]
    if lead_id is not None:
        line = f"Сделка #{lead_id}"
        if stage_title:
            line += f" · {_e(stage_title)}"
        sections.append(line)
    contact = contact_line(chat_id, username)
    if contact:
        sections.append(contact)
    sections.append("📋 Причина:\n" + _e(reason or "—"))
    return "\n\n".join(sections)


def do_not_contact_message(
    *, name: str, chat_id: int, username: Optional[str], reason: str = ""
) -> str:
    """The client asked us to stop writing (or to delete their data). The deal is
    closed and every scheduled touch is cancelled — nobody writes first again."""
    sections = [
        "🚫 <b>Клиент просил больше не писать</b>",
        "Сделка закрыта, все запланированные сообщения отменены. "
        "Не пишем первыми; если клиент напишет сам — ИИ ответит.",
        "👤 " + _e(name or "—"),
    ]
    if reason:
        sections.append("📝 " + _e(reason))
    contact = contact_line(chat_id, username)
    if contact:
        sections.append(contact)
    return "\n\n".join(sections)


def payment_claim_message(
    *,
    name: str,
    note: str,
    chat_id: int,
    username: Optional[str],
    lead_id: Optional[int] = None,
    amount: Optional[int] = None,
) -> str:
    """The customer *says* they've paid (or sent a screenshot). NOT a confirmation
    — payment is verified by an operator against the payment system. Prompts them
    to check and, if it landed, tap «Оплачено»."""
    sections = [
        "💸 <b>Клиент сообщает об оплате</b>",
        "Проверьте поступление в платёжной системе. Если оплата пришла — нажмите «Оплачено».",
        "👤 " + _e(name or "—"),
    ]
    if lead_id is not None:
        line = f"Сделка #{lead_id}"
        if amount:
            line += f" · к оплате: {_e(fmt_amount(amount))}"
        sections.append(line)
    if note:
        sections.append("📝 " + _e(note))
    contact = contact_line(chat_id, username)
    if contact:
        sections.append(contact)
    return "\n\n".join(sections)


def payment_received_message(
    *, name: str, amount: Optional[int], arrival: date | None, chat_id: int, username: Optional[str]
) -> str:
    when = arrival_label(arrival) or "—"
    sections = [
        "💰 <b>Оплата получена!</b>",
        f"👤 Клиент: {_e(name or '—')}",
        f"💵 Сумма: {_e(fmt_amount(amount))}",
        f"📅 Дата заезда: {_e(when)}",
    ]
    contact = contact_line(chat_id, username)
    if contact:
        sections.append(contact)
    return "\n\n".join(sections)
