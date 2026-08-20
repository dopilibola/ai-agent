"""Environment-driven config for the Maskan tenant (grave care, Uzbekistan).

Maskan is an existing product — a Flutter app on a Django/DRF backend — where a
customer registers a relative's grave, picks care services (weeding, cleaning,
marble polishing, …), pays through Payme, and a caretaker (go'rkov) does the
work and uploads before/after photos. This tenant puts an AI sales agent on a
Telegram **userbot** in front of that product: it advises, registers the grave,
creates the order + payment link, and then follows the order to completion.

Two things make it different from oygul/anfa:

* **The Django backend is the source of truth.** Catalogue, graves and orders
  are never duplicated here — `api_client.py` talks to Maskan's `/api/bot/...`
  surface with a shared secret. Only the *funnel* state (leads + the scheduled
  touch queue) is ours, in Postgres.
* **Payment and work status are observed, never asserted.** Payme's webhook
  flips the order to paid and the caretaker's Telegram workflow moves it to
  done; our `OrderWatcher` polls for those changes and reacts. The agent has no
  tool that can mark an order paid.

Fields resolve tenant-prefixed env first (`MASKAN_*`), then a shared fallback
(`TG_API_ID`, `OPENAI_API_KEY`, …) — mirroring oygul/anfa/byd.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, timedelta, timezone
from typing import Optional

from dotenv import load_dotenv

# `config = MaskanConfig()` runs at import time — load .env before any field
# default_factory reads os.environ.
load_dotenv()


def _int_set(name: str, default: str = "") -> frozenset[int]:
    raw = os.environ.get(name, default)
    return frozenset(int(x.strip()) for x in raw.split(",") if x.strip())


def _first_str(*names: str, default: str = "") -> str:
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    return default


def _first_int(*names: str, default: Optional[int] = None) -> Optional[int]:
    for n in names:
        v = os.environ.get(n)
        if v and v.strip():
            return int(v)
    return default


def _dates(name: str) -> tuple[date, ...]:
    """Parse a comma-separated ISO date list, skipping anything malformed.

    Used for the memorial-day calendar (see `memorial_dates`), which an admin
    edits once a year — a typo there must not stop the tenant from starting.
    """
    out: list[date] = []
    for raw in (os.environ.get(name) or "").split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            out.append(date.fromisoformat(raw))
        except ValueError:
            continue
    return tuple(sorted(out))


# Maskan operates across Uzbekistan — Asia/Tashkent (UTC+5). All day-relative
# scheduling (memorial-day reminders, "in 3 days", the annual nudge) runs here.
MASKAN_TZ = timezone(timedelta(hours=5))


@dataclass(frozen=True)
class MaskanConfig:
    # ----- model / provider -------------------------------------------------
    chat_model: str = field(
        default_factory=lambda: _first_str(
            "MASKAN_CHAT_MODEL", "CHAT_MODEL", default="gemini-3.6-flash"
        )
    )
    chat_provider: str = field(
        default_factory=lambda: _first_str(
            "MASKAN_CHAT_PROVIDER", "CHAT_PROVIDER", default="google_genai"
        )
    )
    transcribe_model: str = field(
        default_factory=lambda: _first_str(
            "MASKAN_TRANSCRIBE_MODEL", "TRANSCRIBE_MODEL", default="gpt-4o-transcribe"
        )
    )
    transcribe_provider: str = field(
        default_factory=lambda: _first_str(
            "MASKAN_TRANSCRIBE_PROVIDER", "TRANSCRIBE_PROVIDER", default="openai"
        )
    )

    # ----- Telegram credentials --------------------------------------------
    api_id: Optional[int] = field(
        default_factory=lambda: _first_int("MASKAN_TG_API_ID", "TG_API_ID")
    )
    api_hash: str = field(
        default_factory=lambda: _first_str("MASKAN_TG_API_HASH", "TG_API_HASH")
    )

    openai_api_key: str = field(
        default_factory=lambda: _first_str("MASKAN_OPENAI_API_KEY", "OPENAI_API_KEY")
    )
    groq_api_key: str = field(
        default_factory=lambda: _first_str("MASKAN_GROQ_API_KEY", "GROQ_API_KEY")
    )
    google_api_key: str = field(
        default_factory=lambda: _first_str("MASKAN_GOOGLE_API_KEY", "GOOGLE_API_KEY")
    )

    # ----- customer userbot (the AI agent talks to clients) ----------------
    userbot_session: str = field(
        default_factory=lambda: os.environ.get("MASKAN_USERBOT_SESSION", "data/maskan_userbot")
    )
    userbot_phone: str = field(
        default_factory=lambda: os.environ.get("MASKAN_USERBOT_PHONE", "")
    )

    # ----- operator bot (notifications + inline callbacks + manager agent) --
    # NOTE: this is a *separate* bot from Maskan's existing order bot
    # (@Maskanuzbot). That one serves caretakers and the admin; this one serves
    # the AI funnel — its own token keeps the two callback namespaces apart.
    bot_token: str = field(default_factory=lambda: os.environ.get("MASKAN_BOT_TOKEN", ""))
    bot_session: str = field(
        default_factory=lambda: os.environ.get("MASKAN_BOT_SESSION", "data/maskan_bot")
    )

    # Telegram ids of Maskan staff who, when they DM the operator bot, get the
    # manager agent and may press the funnel's inline buttons.
    manager_allowed_ids: frozenset[int] = field(
        default_factory=lambda: _int_set("MASKAN_MANAGER_ALLOWED_IDS")
    )
    # Staff who receive funnel notifications (new lead, handoff, SLA breaches).
    operator_chat_ids: frozenset[int] = field(
        default_factory=lambda: _int_set("MASKAN_OPERATOR_CHAT_IDS")
    )

    # ----- Maskan Django backend (the source of truth) ---------------------
    # Production: https://app.mas-kan.uz ; local dev: http://127.0.0.1:8010
    api_base: str = field(
        default_factory=lambda: os.environ.get(
            "MASKAN_API_BASE", "https://app.mas-kan.uz"
        ).rstrip("/")
    )
    # Shared secret sent as `X-Bot-Key`; must equal BOT_API_KEY in the Django
    # backend's .env. Unset = every api_client call fails fast with a clear error.
    api_key: str = field(default_factory=lambda: os.environ.get("MASKAN_API_KEY", ""))
    api_timeout: int = field(
        default_factory=lambda: int(os.environ.get("MASKAN_API_TIMEOUT", "20"))
    )
    # Reference data (services, cemeteries) barely changes; cache it in-process
    # so a chatty conversation doesn't hammer the backend.
    api_cache_seconds: int = field(
        default_factory=lambda: int(os.environ.get("MASKAN_API_CACHE_SECONDS", "300"))
    )

    # ----- scheduler --------------------------------------------------------
    scheduler_interval_seconds: int = field(
        default_factory=lambda: int(os.environ.get("MASKAN_SCHEDULER_INTERVAL_SECONDS", "60"))
    )
    scheduler_batch_size: int = field(
        default_factory=lambda: int(os.environ.get("MASKAN_SCHEDULER_BATCH_SIZE", "20"))
    )
    scheduler_max_attempts: int = field(
        default_factory=lambda: int(os.environ.get("MASKAN_SCHEDULER_MAX_ATTEMPTS", "5"))
    )

    # ----- order watcher ----------------------------------------------------
    # Polls the Django backend for the order-status changes we cannot observe:
    # Payme's webhook marking payment received, the caretaker accepting the job,
    # and the admin confirming the before/after photos.
    watcher_interval_seconds: int = field(
        default_factory=lambda: int(os.environ.get("MASKAN_WATCHER_INTERVAL_SECONDS", "120"))
    )
    watcher_batch_size: int = field(
        default_factory=lambda: int(os.environ.get("MASKAN_WATCHER_BATCH_SIZE", "40"))
    )

    # ----- business facts (interpolated into message templates) ------------
    support_phone: str = field(
        default_factory=lambda: os.environ.get("MASKAN_SUPPORT_PHONE", "+998 71 200 00 00")
    )
    app_android_url: str = field(
        default_factory=lambda: os.environ.get(
            "MASKAN_APP_ANDROID_URL", "https://play.google.com/store/apps/details?id=uz.maskan.app"
        )
    )
    app_ios_url: str = field(
        default_factory=lambda: os.environ.get("MASKAN_APP_IOS_URL", "")
    )
    website_url: str = field(
        default_factory=lambda: os.environ.get("MASKAN_WEBSITE_URL", "https://mas-kan.uz")
    )
    # The existing Maskan bot — where clients get their app login + password.
    account_bot_url: str = field(
        default_factory=lambda: os.environ.get(
            "MASKAN_ACCOUNT_BOT_URL", "https://t.me/Maskanuzbot?start=parol"
        )
    )

    # ----- memorial calendar (the seasonal demand driver) ------------------
    # In Uzbekistan graves are visited before Hayit (Ramazon/Qurbon) and on
    # Arafa. Those dates are lunar, so they move every year — an admin fills in
    # the coming year's dates and the funnel schedules a reminder N days before
    # each one. Empty = the memorial touch is simply never scheduled.
    memorial_dates: tuple[date, ...] = field(
        default_factory=lambda: _dates("MASKAN_MEMORIAL_DATES")
    )
    memorial_lead_days: int = field(
        default_factory=lambda: int(os.environ.get("MASKAN_MEMORIAL_LEAD_DAYS", "10"))
    )
    # Hour-of-day (Tashkent) for day-scale sends, so nothing lands at 03:00.
    daytime_send_hour: int = field(
        default_factory=lambda: int(os.environ.get("MASKAN_DAYTIME_SEND_HOUR", "10"))
    )

    # ----- behaviour tuning -------------------------------------------------
    request_timeout: int = 30
    debounce_seconds: float = 5.0
    read_delay_seconds: float = 3.0

    @property
    def api_configured(self) -> bool:
        return bool(self.api_base and self.api_key)


config = MaskanConfig()
