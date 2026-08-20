"""Russian staff notification (HTML) for a follow-up request.

When the catalog agent calls `request_operator`, the clinic staff in
`ANFA_MODERATOR_CHAT_IDS` get this message with a deep-link to the client so a
person can follow up (e.g. call them back). The bot is NOT muted — it keeps
answering the client — so there's no "Подключить ИИ" button.
"""

from __future__ import annotations

import html
from typing import Optional


def _e(value) -> str:
    return html.escape("" if value is None else str(value))


def _format_telegram_contact(patient: Optional[dict]) -> str:
    """Build a Telegram-contact line. Both link forms are included because each
    can fail in different ways: t.me/<username> needs a username; tg://user
    deep-links can be blocked by the moderator's client privacy settings.
    Plain id is always there for manual copy."""
    if not patient:
        return ""
    tg_id = patient.get("tg_id")
    username = (patient.get("tg_username") or "").strip()
    if not tg_id and not username:
        return ""
    bits: list[str] = []
    if username:
        u = _e(username)
        bits.append(f'<a href="https://t.me/{u}">@{u}</a>')
    if tg_id:
        i = int(tg_id)
        bits.append(f'<a href="tg://user?id={i}">открыть профиль</a>')
        bits.append(f"id: <code>{i}</code>")
    return "📞 Контакт клиента (Telegram):\n" + " · ".join(bits)


def build_handoff_message(
    *,
    reason: str,
    summary: str = "",
    patient: Optional[dict] = None,
) -> str:
    sections = ["🙋 Клиент просит оператора (бот продолжает отвечать)"]
    name = (patient or {}).get("tg_first_name") if patient else ""
    if name:
        sections.append("👤 Клиент:\n" + _e(name))
    contact = _format_telegram_contact(patient)
    if contact:
        sections.append(contact)
    sections.append("📌 Причина:\n" + _e(reason or "—"))
    if summary:
        sections.append("📝 Интересует:\n" + _e(summary))
    return "\n\n".join(sections)


def build_results_handoff_message(
    *,
    summary: str = "",
    patient: Optional[dict] = None,
    client_name: str = "",
    client_birthdate: str = "",
) -> str:
    """Staff notification for the results/analysis handoff.

    Unlike `build_handoff_message`, the bot is PAUSED for this chat: a person
    must send the client their results and then press "Подключить ИИ" to hand
    the chat back to the bot. Carries the same contact deep-link, plus the
    client-stated full name and date of birth so staff can locate their results.
    """
    sections = [
        "📋 Клиент просит результаты анализов / документ — БОТ ПРИОСТАНОВЛЕН.\n"
        "Ответьте клиенту сами (отправьте результаты), затем нажмите "
        "«Подключить ИИ», чтобы вернуть бота."
    ]
    name = (patient or {}).get("tg_first_name") if patient else ""
    if name:
        sections.append("👤 Клиент:\n" + _e(name))
    contact = _format_telegram_contact(patient)
    if contact:
        sections.append(contact)
    # Client-stated identity (full name + date of birth) for locating their
    # results in the clinic's local CRM — the Telegram profile has neither
    # surname nor date of birth.
    identity_bits: list[str] = []
    if client_name:
        identity_bits.append("ФИО: " + _e(client_name))
    if client_birthdate:
        identity_bits.append("Дата рождения: " + _e(client_birthdate))
    if identity_bits:
        sections.append("🪪 Данные для поиска результатов:\n" + "\n".join(identity_bits))
    if summary:
        sections.append("📝 Просит:\n" + _e(summary))
    return "\n\n".join(sections)
