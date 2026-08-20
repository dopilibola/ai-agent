"""Operator/manager notifications (Uzbek HTML) + the inline-button contracts.

Maskan's staff work in Uzbek — the backend, the admin panel and the caretaker
bot are all Uzbek — so these read in Uzbek, unlike byd's Russian ones.

The inline buttons cover the two things the system genuinely cannot observe on
its own: whether a staff member has actually dealt with an escalation, and
whether the AI should be handed the chat back after a human took over. Order
status and payment are *not* buttons here — those come from the Django backend
(Payme's webhook, the caretaker's own bot), and the watcher reads them.

Callback-data prefixes are the shared contract with the handlers registered on
the operator bot in `apps.maskan.main`.
"""

from __future__ import annotations

import html
from typing import Optional

# ----- callback-data prefixes (operator bot inline buttons) -----------------
TASK_DONE_CALLBACK_PREFIX = "msktask:"   # msktask:<lead_id>:<kind>
CLOSE_CALLBACK_PREFIX = "mskclose:"      # mskclose:<lead_id>


def _e(value) -> str:
    return html.escape("" if value is None else str(value))


def fmt_sum(amount: Optional[int]) -> str:
    if amount is None:
        return "—"
    return f"{int(amount):,}".replace(",", " ") + " so'm"


# ----- contact line (reused across notifications) ---------------------------

def contact_line(chat_id: Optional[int], username: Optional[str], phone: str = "") -> str:
    username = (username or "").strip()
    bits: list[str] = []
    if username:
        u = _e(username)
        bits.append(f'<a href="https://t.me/{u}">@{u}</a>')
    if chat_id:
        i = int(chat_id)
        bits.append(f'<a href="tg://user?id={i}">profilni ochish</a>')
        bits.append(f"id: <code>{i}</code>")
    line = ""
    if bits:
        line = "📞 Mijoz (Telegram):\n" + " · ".join(bits)
    if phone:
        line += ("\n" if line else "") + "☎️ Telefon: " + _e(phone)
    return line


def _grave_line(grave: str, cemetery: str) -> str:
    if not grave and not cemetery:
        return ""
    parts = [p for p in (grave, cemetery) if p]
    return "🪦 " + _e(" · ".join(parts))


# ----- inline-keyboard builders ---------------------------------------------

def task_done_button(lead_id: int, kind: str) -> dict:
    """"Hal qilindi" — closes an operator to-do so the SLA chain stops nagging."""
    return {
        "inline_keyboard": [
            [{
                "text": "✅ Hal qilindi",
                "callback_data": f"{TASK_DONE_CALLBACK_PREFIX}{int(lead_id)}:{kind}",
            }]
        ]
    }


def close_button(lead_id: int) -> dict:
    """Terminal close for a lead an operator judges dead — stops every touch."""
    return {
        "inline_keyboard": [
            [{
                "text": "🚫 Yopish (kuzatuvni to'xtatish)",
                "callback_data": f"{CLOSE_CALLBACK_PREFIX}{int(lead_id)}",
            }]
        ]
    }


# ----- message bodies -------------------------------------------------------

def new_lead_message(
    *, name: str, request: str, chat_id: int, username: Optional[str], phone: str = ""
) -> str:
    """A new conversation reached grave-level detail — worth staff awareness, but
    NOT a call task: Maskan is self-service, the AI carries it from here."""
    sections = [
        "🔔 <b>Yangi murojaat</b>",
        "👤 " + _e(name or "—"),
    ]
    if request:
        sections.append("📝 So'rov: " + _e(request))
    contact = contact_line(chat_id, username, phone)
    if contact:
        sections.append(contact)
    return "\n\n".join(sections)


def order_created_message(
    *,
    name: str,
    order_id: int,
    total: Optional[int],
    grave: str,
    cemetery: str,
    frequency_label: str,
    chat_id: int,
    username: Optional[str],
) -> str:
    """The AI created an awaiting-payment order. Informational — the caretaker
    group only hears about it once Payme confirms the money."""
    sections = [
        "🧾 <b>Buyurtma rasmiylashtirildi (to'lov kutilmoqda)</b>",
        f"№{int(order_id)} · {_e(fmt_sum(total))} · {_e(frequency_label)}",
        "👤 " + _e(name or "—"),
    ]
    grave_line = _grave_line(grave, cemetery)
    if grave_line:
        sections.append(grave_line)
    contact = contact_line(chat_id, username)
    if contact:
        sections.append(contact)
    return "\n\n".join(sections)


def payment_received_message(
    *, name: str, order_id: int, total: Optional[int], grave: str, cemetery: str
) -> str:
    sections = [
        "💰 <b>To'lov tushdi</b>",
        f"№{int(order_id)} · {_e(fmt_sum(total))}",
        "👤 " + _e(name or "—"),
    ]
    grave_line = _grave_line(grave, cemetery)
    if grave_line:
        sections.append(grave_line)
    sections.append("Buyurtma qabriston guruhiga uzatildi.")
    return "\n\n".join(sections)


def sla_message(
    *,
    title: str,
    name: str,
    order_id: Optional[int],
    grave: str,
    cemetery: str,
    chat_id: int,
    username: Optional[str],
) -> str:
    """A deadline the backend workflow missed — nobody accepted the job, or no
    before/after photos arrived. Staff have to go poke someone."""
    sections = ["⏰ <b>Muddat o'tdi</b>", _e(title)]
    if order_id:
        sections.append(f"Buyurtma №{int(order_id)}")
    sections.append("👤 " + _e(name or "—"))
    grave_line = _grave_line(grave, cemetery)
    if grave_line:
        sections.append(grave_line)
    contact = contact_line(chat_id, username)
    if contact:
        sections.append(contact)
    return "\n\n".join(sections)


def payment_check_message(
    *, name: str, order_id: Optional[int], total: Optional[int], chat_id: int,
    username: Optional[str]
) -> str:
    sections = [
        "💳 <b>To'lov tekshiruvi</b>",
        "Havola yuborilgandan keyin to'lov ko'rinmadi — mijoz bilan bog'laning.",
        "👤 " + _e(name or "—"),
    ]
    if order_id:
        sections.append(f"Buyurtma №{int(order_id)} · {_e(fmt_sum(total))}")
    contact = contact_line(chat_id, username)
    if contact:
        sections.append(contact)
    return "\n\n".join(sections)


def handoff_message(
    *,
    name: str,
    reason: str,
    chat_id: int,
    username: Optional[str],
    lead_id: Optional[int] = None,
    stage_title: Optional[str] = None,
    phone: str = "",
) -> str:
    """The AI escalated — a human should take this chat over."""
    sections = ["🚨 <b>Operator kerak — sun'iy intellekt uddalay olmadi</b>",
                "👤 " + _e(name or "—")]
    if lead_id is not None:
        line = f"Murojaat #{lead_id}"
        if stage_title:
            line += f" · {_e(stage_title)}"
        sections.append(line)
    contact = contact_line(chat_id, username, phone)
    if contact:
        sections.append(contact)
    sections.append("📋 Sabab:\n" + _e(reason or "—"))
    return "\n\n".join(sections)


def do_not_contact_message(
    *, name: str, chat_id: int, username: Optional[str], reason: str = ""
) -> str:
    """The client asked us to stop writing. Every scheduled touch is cancelled;
    we never write first again, though the AI still answers if they write."""
    sections = [
        "🚫 <b>Mijoz yozmaslikni so'radi</b>",
        "Murojaat yopildi, barcha rejalashtirilgan xabarlar bekor qilindi. "
        "O'zi yozsa — sun'iy intellekt javob beradi.",
        "👤 " + _e(name or "—"),
    ]
    if reason:
        sections.append("📝 " + _e(reason))
    contact = contact_line(chat_id, username)
    if contact:
        sections.append(contact)
    return "\n\n".join(sections)
