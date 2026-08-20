"""Client-facing funnel message templates (Russian) — verbatim from the BYD ТЗ.

Pure string builders over `BydConfig` + a lead's fields. No I/O. The scheduler's
action executors call these to compose each touch; placeholders ([Имя], [дата],
[адрес], [ссылка], [месяц]) are filled here so the texts can't drift from the spec.
"""

from __future__ import annotations

from datetime import date, datetime

from apps.byd.config import CLINIC_TZ, BydConfig

_RU_MONTHS_NOM = [
    "", "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
]
_RU_MONTHS_GEN = [
    "", "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


def _name(lead_name: str) -> str:
    return (lead_name or "").strip() or "Здравствуйте"


def arrival_label(arrival: date | None) -> str:
    """Human date for the arrival day, e.g. "5 июля". Empty string if unknown."""
    if arrival is None:
        return ""
    return f"{arrival.day} {_RU_MONTHS_GEN[arrival.month]}"


def current_month_nom() -> str:
    return _RU_MONTHS_NOM[datetime.now(CLINIC_TZ).month]


# ===== Stage 2 — Не дозвон (5-touch drip) ==================================

def s2_touch1(lead_name: str) -> str:
    return (
        f"{_name(lead_name)}, пытались до вас дозвониться 🙂\n"
        "Когда вам удобно поговорить?"
    )


def s2_touch2_caption(lead_name: str) -> str:
    return (
        f"{_name(lead_name)}, пока ждём — отправляем вам информацию "
        "о наших программах 👇"
    )


def s2_touch3(lead_name: str) -> str:
    return (
        f"{_name(lead_name)}, иногда мы так привыкаем к усталости и тяжести "
        "в теле, что начинаем считать это нормой 😔\n"
        "Но это не норма. Ваш организм просто просит о помощи.\n\n"
        "Многие наши пациенты говорили то же самое — «подожду, само пройдёт». "
        "А потом приехали к нам и спрашивали: почему я не сделал это раньше?\n\n"
        "Мы здесь. Напишите — просто поговорим 🙂"
    )


def s2_touch4(lead_name: str) -> str:
    return (
        f"{_name(lead_name)}, у нас осталось несколько свободных мест "
        f"на {current_month_nom()}. Хотим предложить вам место пока оно есть 🙏"
    )


def s2_touch5(lead_name: str) -> str:
    return (
        f"{_name(lead_name)}, хотели поделиться — сейчас у нас проходит "
        "акция на программы очищения 🎁\n"
        "Если забронируете в этом месяце — специальные условия для вас.\n\n"
        "Расскажите что вас беспокоит — подберём программу именно под вас 🙂"
    )


def s2_reactivation(lead_name: str) -> str:
    return (
        f"{_name(lead_name)}, здравствуйте! Вы интересовались программами "
        "очищения в BYD Medical 🌿\n"
        "Если для вас это всё ещё актуально — будем рады подобрать удобное "
        "время. Напишите нам 🙏"
    )


# ===== Chat-silence follow-ups (script §8.3/8.4) ===========================
# Sent when the client goes quiet mid-conversation: one a few hours later, one
# more days after that — then never again (§8.5: initiative is the client's).

def chat_followup1() -> str:
    return (
        "Здравствуйте! Если остались вопросы по программе — я на связи, "
        "с радостью подскажу 🙂"
    )


def chat_followup2() -> str:
    return (
        "Добрый день! Не тороплю, просто на связи, если вопрос ещё актуален — "
        "обращайтесь в любое время."
    )


# ===== Stage 4 — Консультация назначена ====================================

def s4_confirm(lead_name: str, arrival: date | None) -> str:
    when = arrival_label(arrival)
    when_part = f" {when}" if when else ""
    return (
        f"{_name(lead_name)}, отлично! Наш врач будет ждать вас{when_part}. "
        "При заезде врач проведёт личную консультацию и подберёт программу "
        "именно под вас 🙏"
    )


def s4_reminder(lead_name: str, cfg: BydConfig) -> str:
    return (
        f"{_name(lead_name)}, напоминаем — ваш заезд в клинику BYD Medical "
        "через 3 дня 🙂\n"
        f"Адрес: {cfg.clinic_address}\n"
        f"Время заезда: с {cfg.arrival_time}\n"
        "Если есть вопросы — пишите, всегда рады помочь 🙏"
    )


# ===== Stage 5 — Запрос предоплаты =========================================

def s5_payment_request(lead_name: str, payment_url: str, cfg: BydConfig) -> str:
    return (
        f"{_name(lead_name)}, для подтверждения вашего места в клинике "
        f"необходима предоплата — {cfg.prepayment_percent}% от стоимости "
        "программы.\n"
        f"Ссылка для оплаты: {payment_url}\n"
        "После оплаты ваше место будет официально забронировано 🙏"
    )


def s5_payment_reminder(lead_name: str, payment_url: str) -> str:
    return (
        f"{_name(lead_name)}, ваше место ещё свободно 🙂\n"
        f"Напоминаем — ссылка для оплаты: {payment_url}\n"
        "Места ограничены, не хотим чтобы вы его потеряли 🙏"
    )


# ===== Stage 6 — Бронь (voucher) ===========================================

def s6_voucher_caption(lead_name: str, arrival: date | None) -> str:
    when = arrival_label(arrival)
    when_part = f"\nЖдём вас {when} 🙏" if when else ""
    return (
        f"{_name(lead_name)}, поздравляем — ваше место в клинике BYD Medical "
        "официально забронировано! 🎉\n"
        "Во вложении ваш ваучер с деталями заезда." + when_part
    )


# ===== Stage 7 — Подтверждение брони =======================================

def s7_reminder(lead_name: str, cfg: BydConfig) -> str:
    # The spec reuses the same -3d reminder body as Stage 4.
    return s4_reminder(lead_name, cfg)


def s7_morning(lead_name: str, cfg: BydConfig) -> str:
    return (
        "Доброе утро! Ждём вас сегодня в клинике BYD Medical 🙏\n"
        f"Заезд с {cfg.arrival_time}. Если нужна помощь — звоните."
    )


# ===== Stage 8 — Успешно реализовано =======================================

def s8_review(lead_name: str, cfg: BydConfig) -> str:
    return (
        f"{_name(lead_name)}, как вы себя чувствуете после программы? 🙂\n"
        "Нам очень важно ваше мнение! Оставьте отзыв — это займёт 1 минуту:\n"
        f"⭐ Instagram: {cfg.instagram_url}\n"
        f"💬 Наша группа: {cfg.community_url}\n"
        f"🌐 Сайт: {cfg.website_url}"
    )


def s8_referral(lead_name: str) -> str:
    return (
        f"{_name(lead_name)}, если вашим близким тоже нужна помощь — "
        "порекомендуйте нас!\n"
        "За каждого приведённого друга — скидка 10% на ваш следующий курс 🎁"
    )


def s8_reactivation(lead_name: str) -> str:
    return (
        f"{_name(lead_name)}, как ваше самочувствие? 🙂\n"
        "Наши врачи хотят провести для вас бесплатную онлайн-консультацию — "
        "когда вам удобно? 🙏"
    )


def s8_birthday(lead_name: str) -> str:
    return (
        f"{_name(lead_name)}, поздравляем с днём рождения! 🎉\n"
        "В честь праздника дарим вам скидку 10% на следующий курс в BYD Medical.\n"
        "Желаем здоровья и сил! 🙏"
    )
