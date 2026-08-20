"""Client-facing message templates (Uzbek) for the scheduled funnel touches.

These are **fallbacks**, not the primary voice. Every scheduled touch first goes
through `Channel.compose_outbound()`, which lets the agent write it in the
language and tone of the live conversation (Maskan's clients write Uzbek, Russian
and a mix of both). The template below is what gets sent if composition is
unavailable or returns nothing — so the client always receives *something*.

They are written the way Maskan's own copy is: Uzbek, plain, respectful, no
markdown, no exclamation-mark sales energy. This is a service people buy for a
parent's grave — warmth beats enthusiasm, and nothing here should ever sound
like a promotion.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from apps.maskan.config import MaskanConfig

# Frequency labels as the Maskan backend spells them (orders.Order.FREQ_CHOICES).
FREQ_LABELS_UZ = {
    "once": "bir martalik",
    "monthly": "oylik",
    "quarterly": "choraklik",
    "annual": "yillik",
}


def fmt_sum(amount: Optional[int]) -> str:
    """Thousands-separated UZS, e.g. '120 000 so'm'."""
    if amount is None:
        return "—"
    return f"{int(amount):,}".replace(",", " ") + " so'm"


def date_label(value: Optional[date]) -> str:
    return value.strftime("%d.%m.%Y") if value else "—"


def _name_prefix(name: str) -> str:
    """'Assalomu alaykum, Jahongir aka.' — or the plain greeting if we have no
    name yet. Maskan's clients are usually middle-aged; the bare first name
    without an honorific reads curt in Uzbek."""
    name = (name or "").strip().split()[0] if (name or "").strip() else ""
    return f"Assalomu alaykum, {name}." if name else "Assalomu alaykum."


# ----- stage 1: we don't know whose grave yet -------------------------------

def s1_followup(name: str) -> str:
    return (
        f"{_name_prefix(name)} Qabr parvarishi bo'yicha yozgan edingiz. "
        "Qaysi qabristonda va kimning qabri ekanini aytsangiz, "
        "xizmatlar va narxlarni aniq ko'rsataman."
    )


def s1_last_call(name: str, cfg: MaskanConfig) -> str:
    return (
        f"{_name_prefix(name)} Bezovta qilmayin — savolingiz bo'lsa istalgan payt yozing. "
        f"Telefon orqali ham bog'lanishingiz mumkin: {cfg.support_phone}."
    )


# ----- stage 2: grave known, no order yet -----------------------------------

def s2_offer(name: str, grave: str, cemetery: str) -> str:
    where = f"{cemetery}dagi" if cemetery else ""
    whose = f"{grave} qabri" if grave else "qabr"
    return (
        f"{_name_prefix(name)} {where} {whose} uchun eng ko'p tanlanadigan xizmatlar: "
        "o't tozalash, umumiy tozalash, marmar jilo. "
        "To'liq parvarish esa hammasini birdan qamrab oladi. "
        "Qaysi biri kerakligini ayting — narxini aniq aytaman."
    ).replace("  ", " ")


def s2_followup(name: str) -> str:
    return (
        f"{_name_prefix(name)} Qabr parvarishi bo'yicha o'ylab ko'rdingizmi? "
        "Xizmatni tanlashga yordam beray — bir necha savolga javob bersangiz kifoya."
    )


def s2_last_call(name: str) -> str:
    return (
        f"{_name_prefix(name)} Hozircha bezovta qilmayman. "
        "Qachon kerak bo'lsa — shu yerga yozing, qabr ma'lumotlari saqlanib qoladi."
    )


# ----- stage 3/4: quoted, payment link sent ---------------------------------

def s3_reminder(name: str, total: Optional[int]) -> str:
    return (
        f"{_name_prefix(name)} Tanlagan xizmatlaringiz bo'yicha jami "
        f"{fmt_sum(total)}. To'lovni rasmiylashtirishga yordam beraymi?"
    )


def s4_payment_reminder(name: str, total: Optional[int], link: str) -> str:
    return (
        f"{_name_prefix(name)} To'lov havolasi hali ochiq — {fmt_sum(total)}. "
        f"To'lovdan so'ng buyurtma darhol go'rkovga uzatiladi.\n{link}"
    ).strip()


def s4_expire(name: str) -> str:
    return (
        f"{_name_prefix(name)} To'lov amalga oshmadi, shuning uchun buyurtmani "
        "kutish rejimidan chiqardim. Kerak bo'lsa — bir og'iz yozing, "
        "qaytadan tez rasmiylashtiraman."
    )


# ----- stage 5/6: paid, work in progress ------------------------------------

def s5_paid(name: str, order_id: int, total: Optional[int]) -> str:
    return (
        f"{_name_prefix(name)} To'lovingiz qabul qilindi — rahmat. "
        f"Buyurtma №{order_id} ({fmt_sum(total)}) qabriston xodimiga uzatildi. "
        "Ish boshlanganda va tugaganda xabar beraman, oxirida oldin/keyin "
        "rasmlarini yuboraman."
    )


def s6_accepted(name: str, caretaker: str) -> str:
    who = f" Xodim: {caretaker}." if caretaker else ""
    return (
        f"{_name_prefix(name)} Buyurtmangiz qabul qilindi, ish boshlandi.{who} "
        "Tugagach rasmlarini yuboraman."
    )


def s7_completed(name: str, grave: str) -> str:
    whose = f"{grave} qabri" if grave else "Qabr"
    return (
        f"{_name_prefix(name)} {whose} parvarish qilindi — quyida oldin va keyin "
        "olingan rasmlar. Ishimizdan ko'nglingiz to'ldimi?"
    )


def s7_rejected(name: str) -> str:
    return (
        f"{_name_prefix(name)} Buyurtmangiz bo'yicha aniqlashtirish kerak bo'ldi — "
        "mas'ul xodimimiz tez orada bog'lanadi. Noqulaylik uchun uzr."
    )


# ----- stage 7/8: after the work --------------------------------------------

def s7_review(name: str, cfg: MaskanConfig) -> str:
    return (
        f"{_name_prefix(name)} Parvarish sifati bo'yicha fikringizni bilsak — "
        "biz uchun juda muhim. Bir-ikki og'iz yozsangiz kifoya. "
        f"Ilovada ham baho qoldirishingiz mumkin: {cfg.app_android_url}"
    )


def s7_referral(name: str) -> str:
    return (
        f"{_name_prefix(name)} Agar tanish-bilishlaringizga ham qabr parvarishi "
        "kerak bo'lsa — bemalol shu yerga yo'naltiring, yordam beramiz."
    )


def s8_recurring(name: str, grave: str, freq: str) -> str:
    label = FREQ_LABELS_UZ.get(freq, "keyingi")
    whose = f"{grave} qabri" if grave else "qabr"
    return (
        f"{_name_prefix(name)} {whose} uchun {label} parvarish vaqti keldi. "
        "Avvalgi xizmatlar bilan davom ettiraymi?"
    )


def s8_annual(name: str, grave: str) -> str:
    whose = f"{grave} qabri" if grave else "qabr"
    return (
        f"{_name_prefix(name)} O'tgan yili {whose} parvarish qilgan edik. "
        "Bu yil ham tartibga keltiraymizmi?"
    )


def s8_memorial(name: str, grave: str, when: Optional[date]) -> str:
    whose = f"{grave} qabri" if grave else "qabr"
    day = f" ({date_label(when)})" if when else ""
    return (
        f"{_name_prefix(name)} Yaqin kunlarda ziyorat kunlari{day}. "
        f"Shu kungacha {whose} tartibga keltirib qo'yaymizmi? "
        "Oldindan buyurtma bersangiz, ulgurishimiz aniq."
    )
