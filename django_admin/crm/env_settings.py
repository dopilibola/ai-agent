"""The handful of `.env` values an operator legitimately needs to change.

Most of `.env` is credentials and wiring — Telegram sessions, Payme keys, the
database URL — and none of that belongs on a web page: a merchant key rendered
into a form is a merchant key one screenshot away from being someone else's.
So this module works from a **fixed allow-list**. A key not listed here is
never read out and never written, which is what makes the page safe to expose.

The other difference from the outreach rules: those are read from Postgres on
every tick, while these are `.env` values folded into a frozen config dataclass
**at import time**. Saving one therefore does nothing until the bot restarts,
and the page says so and offers the button.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_PATH = REPO_ROOT / ".env"


@dataclass(frozen=True)
class EnvField:
    key: str
    label: str
    help: str
    kind: str = "text"  # text | int | list


# Everything an operator can change without a developer, and nothing else.
FIELDS: tuple[EnvField, ...] = (
    EnvField(
        "MASKAN_SUPPORT_PHONE",
        "Qo‘llab-quvvatlash telefoni",
        "Agent kerak bo‘lganda mijozga shu raqamni beradi.",
    ),
    EnvField(
        "MASKAN_OPERATOR_CHAT_IDS",
        "Operator chat id’lari",
        "Bildirishnomalar shularga boradi. Vergul bilan ajrating. "
        "Muhim: har bir operator botga bir marta /start bosishi shart — "
        "aks holda Telegram xabarni yetkazmaydi.",
        kind="list",
    ),
    EnvField(
        "MASKAN_MANAGER_ALLOWED_IDS",
        "Menejer agentiga ruxsati bor id’lar",
        "Operator botida xodimlar agentidan foydalana oladiganlar. Vergul bilan.",
        kind="list",
    ),
    EnvField(
        "MASKAN_MEMORIAL_DATES",
        "Ziyorat kunlari (sanalar)",
        "YYYY-MM-DD ko‘rinishida, vergul bilan. Hayit/Arafa sanalari oy taqvimiga "
        "bog‘liq, shuning uchun qo‘lda kiritiladi.",
        kind="list",
    ),
    EnvField(
        "MASKAN_MEMORIAL_LEAD_DAYS",
        "Ziyorat kunidan necha kun oldin eslatilsin",
        "Faqat avto-xabarlar yoqilgan bo‘lsa ishlaydi.",
        kind="int",
    ),
    EnvField(
        "MASKAN_DAYTIME_SEND_HOUR",
        "Kunlik xabarlar uchun soat",
        "Kunlar bo‘yicha rejalashtirilgan xabar shu soatga tushiriladi (0–23).",
        kind="int",
    ),
    EnvField(
        "MASKAN_SERVICE_AREA_LABEL",
        "Xizmat hududi nomi",
        "Agent hududdan tashqaridagi so‘rovni rad etganda shuni aytadi.",
    ),
)
FIELD_BY_KEY = {f.key: f for f in FIELDS}

# KEY=value, tolerating `export KEY=value` and surrounding spaces.
_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def read_values() -> dict[str, str]:
    """Current values of the allow-listed keys only."""
    values = {f.key: "" for f in FIELDS}
    try:
        text = ENV_PATH.read_text(encoding="utf-8")
    except OSError:
        return values
    for line in text.splitlines():
        match = _LINE.match(line)
        if match and match.group(1) in FIELD_BY_KEY:
            values[match.group(1)] = _unquote(match.group(2))
    return values


def _format(field: EnvField, raw: str) -> tuple[str, str]:
    """(value to store, error). Validation is per kind, and refuses silently
    wrong input rather than writing something the bot will crash on."""
    value = (raw or "").strip()
    if field.kind == "int":
        if not value:
            return "", ""
        if not re.fullmatch(r"-?\d+", value):
            return "", f"{field.label}: faqat raqam kiriting."
        return value, ""
    if field.kind == "list":
        parts = [p.strip() for p in value.replace(";", ",").split(",") if p.strip()]
        if field.key == "MASKAN_MEMORIAL_DATES":
            for p in parts:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", p):
                    return "", f"{field.label}: «{p}» YYYY-MM-DD ko‘rinishida emas."
        if field.key.endswith("_IDS"):
            for p in parts:
                if not re.fullmatch(r"-?\d+", p):
                    return "", f"{field.label}: «{p}» raqam emas."
        return ",".join(parts), ""
    return value, ""


def save_values(posted: dict) -> tuple[bool, list[str]]:
    """Write the allow-listed keys back into `.env`, leaving every other line
    byte-for-byte as it was. Returns (ok, messages).

    A backup is written first: `.env` also holds the live payment credentials,
    and a half-written one takes the whole deployment down.
    """
    new: dict[str, str] = {}
    errors: list[str] = []
    for field in FIELDS:
        if field.key not in posted:
            continue
        value, error = _format(field, posted.get(field.key, ""))
        if error:
            errors.append(error)
        else:
            new[field.key] = value
    if errors:
        return False, errors
    if not new:
        return True, ["O‘zgarish yo‘q."]

    try:
        original = ENV_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        return False, [f".env o‘qilmadi: {exc}"]

    lines = original.splitlines()
    seen: set[str] = set()
    for i, line in enumerate(lines):
        match = _LINE.match(line)
        if not match:
            continue
        key = match.group(1)
        if key in new:
            seen.add(key)
            lines[i] = f"{key}={_quote(new[key])}"
    missing = [k for k in new if k not in seen and new[k] != ""]
    if missing:
        lines.append("")
        lines.append("# Panel orqali qo‘shildi")
        lines.extend(f"{k}={_quote(new[k])}" for k in missing)

    try:
        (ENV_PATH.parent / ".env.bak").write_text(original, encoding="utf-8")
        ENV_PATH.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
    except OSError as exc:
        return False, [f".env yozilmadi: {exc}"]
    return True, [
        "Saqlandi. Bu sozlamalar bot ishga tushganda o‘qiladi — "
        "kuchga kirishi uchun botni qayta ishga tushiring."
    ]


def _quote(value: str) -> str:
    """Quote only when the value would otherwise break `.env` parsing.

    `MASKAN_SUPPORT_PHONE` is the reason this exists: it contains spaces, and an
    unquoted space is what makes `set -a; . ./.env` fail in a deploy script.
    """
    if value == "" or re.fullmatch(r"[A-Za-z0-9_.:@/+,-]+", value):
        return value
    return '"' + value.replace('"', '\\"') + '"'
