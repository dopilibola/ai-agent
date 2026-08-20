"""Deterministic emergency triage for the anfa patient channels.

Runs in code as a `Channel.message_guard` *before* the LLM ever sees the
message, so the safety interstitial doesn't depend on the model obeying the
"medical safety" prompt block. When the patient's message contains
emergency-grade symptoms (the CEO's list: severe pain, bleeding, fainting/loss
of consciousness, chest pain, breathing trouble, very high fever) the guard
returns an urgent-care message and short-circuits the booking flow for that
turn. Otherwise it returns None and normal booking proceeds.

Conservative on purpose — it matches acute/severe phrasing, not bare "pain", so
a routine "tooth hurts, want to book" is not derailed.
"""

from __future__ import annotations

from typing import Any, Optional

# Uzbek apostrophe variants (o'/g') — normalize them all away so "og'riq",
# "og`riq" and "ogriq" match the same pattern.
_APOSTROPHES = "'`ʻʼ’‘"

# Two-layer match on the normalized (lowercased, apostrophe-stripped) text.
#
# Layer 1 — unambiguous phrases (adjacency-safe substrings). A bare match here is
# enough. Bleeding/fainting kept as adjacent phrases so "qon" near "tahlil"
# (blood TEST) or "ketdim" (I left) can't false-trigger.
_EMERGENCY_TERMS = [
    # --- Russian ---
    "боль в груди", "болит грудь", "давит грудь", "давит в груди",
    "сердечный приступ", "инфаркт", "инсульт",
    "кровотечение", "истекаю кровью", "кровь не останавлива", "кровь идет", "кровь идёт",
    "обморок", "теряю сознание", "потерял сознание", "потеряла сознание", "без сознания",
    "не могу дышать", "трудно дышать", "тяжело дышать", "задыхаюсь", "одышка",
    "температура 39", "температура 40", "температура 41", "высокая температура",
    "судорог", "сильное отравление", "отравил",
    # --- Uzbek (apostrophes already stripped) ---
    "yurak xuruji", "infarkt", "insult",
    "qon ketyap", "qon ketmoq", "qon ketdi", "qon ketishi", "qon oqyap", "qon oqmoq",
    "qon toxtamay", "qon keldi",
    "hushdan ket", "hushidan ket", "hushini yoqot", "hushim ket", "hushsiz", "behush",
    "talvasa", "kuchli zaharlan", "zaharlanib",
    # --- English (voice transcripts / mixed) ---
    "chest pain", "cant breathe", "can not breathe", "cannot breathe",
    "trouble breathing", "short of breath", "heart attack", "stroke",
    "heavy bleeding", "bleeding heavily", "losing consciousness", "passed out",
    "unconscious", "severe pain",
]

# Layer 2 — stem co-occurrence rules: a tuple of groups; it fires when at least
# one stem from EVERY group is present anywhere in the message. Stems (not whole
# words) absorb morphology — "og'riyapti" vs "og'riq", "ko'kragim" vs "ko'krak",
# "nafas olishim qiyin" vs "nafas ololmayapman". The qualifier group (severity or
# body part) keeps generic discomfort from triggering: bare "tishim ogriyapti"
# (tooth hurts) has no qualifier and stays silent.
_EMERGENCY_COMBOS = [
    # chest pain
    (["kokrak", "kokrag"], ["ogri", "ogir"]),
    (["груд"], ["бол", "дав"]),
    # severe pain (CEO list explicitly counts "severe pain")
    (["qattiq", "kuchli", "chidab"], ["ogri", "ogir"]),
    (["сильн", "остр", "невыносим"], ["бол"]),
    # breathing distress
    (["nafas"], ["qiyin", "olmay", "ololm", "qis", "yetm", "bogil"]),
    (["дыша", "дыхан", "задыха"], ["не могу", "трудно", "тяжело", "не получ"]),
    # very high fever
    (["harorat", "isitma"], ["39", "40", "41", "yuqori", "baland"]),
    (["температур", "жар"], ["39", "40", "41", "высок"]),
]


def _is_emergency(norm: str) -> bool:
    if any(term in norm for term in _EMERGENCY_TERMS):
        return True
    return any(
        all(any(stem in norm for stem in group) for group in groups)
        for groups in _EMERGENCY_COMBOS
    )

_MSG_RU = (
    "⚠️ Если это экстренная ситуация (сильная боль, кровотечение, потеря "
    "сознания, боль в груди, затруднённое дыхание или очень высокая "
    "температура) — пожалуйста, немедленно вызовите скорую помощь по номеру "
    "103.\n\nКак только будете в безопасности, я помогу записать вас к врачу "
    "в клинику Анфа."
)

_MSG_UZ = (
    "⚠️ Agar bu shoshilinch holat bo‘lsa (kuchli og‘riq, qon ketishi, hushdan "
    "ketish, ko‘krakdagi og‘riq, nafas qisilishi yoki juda yuqori harorat) — "
    "iltimos, darhol 103 raqamiga qo‘ng‘iroq qilib tez yordam chaqiring.\n\n"
    "Xavfsiz bo‘lganingizdan so‘ng, sizni Anfa klinikasida shifokorga "
    "yozishda yordam beraman."
)


def _normalize(text: str) -> str:
    out = text.lower()
    for ch in _APOSTROPHES:
        out = out.replace(ch, "")
    return out


def _extract_text(content: Any) -> str:
    """Pull the patient's text out of dispatch `content` (plain str or the
    multimodal list of {type, text/image_url} blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "\n".join(p for p in parts if p)
    return ""


def _has_cyrillic(text: str) -> bool:
    return any("а" <= ch.lower() <= "я" or ch.lower() == "ё" for ch in text)


def emergency_triage(chat_id: int, content: Any) -> Optional[str]:
    """Return an urgent-care message when the message looks like a medical
    emergency, else None. Reply language follows the message script: Cyrillic →
    Russian, otherwise Uzbek (Latin)."""
    raw = _extract_text(content)
    if not raw:
        return None
    if not _is_emergency(_normalize(raw)):
        return None
    return _MSG_RU if _has_cyrillic(raw) else _MSG_UZ
