"""Environment-driven config for the BYD Medical tenant (detox clinic, Tashkent).

A faithful rebuild of the BYD Bitrix24 sales funnel on this platform: an AI sales
agent on a customer **userbot** + an operator **bot** (notifications, inline-button
callbacks, and a staff manager agent) + a durable per-deal scheduler that fires the
funnel's ~23 time-delayed actions.

Fields resolve tenant-prefixed env first (`BYD_*`), then a shared fallback
(`TG_API_ID`, `OPENAI_API_KEY`, …) — mirroring oygul/anfa.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import timedelta, timezone
from typing import Optional

from dotenv import load_dotenv

# `config = BydConfig()` runs at import time — load .env before any field
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


# BYD Medical — Asia/Tashkent (UTC+5). All arrival-relative scheduling
# ("morning-of", "3 days before") and the funnel's date math run in this tz.
CLINIC_TZ = timezone(timedelta(hours=5))


@dataclass(frozen=True)
class BydConfig:
    # ----- model / provider -------------------------------------------------
    chat_model: str = field(
        default_factory=lambda: _first_str("BYD_CHAT_MODEL", "CHAT_MODEL", default="gemini-3.6-flash")
    )
    chat_provider: str = field(
        default_factory=lambda: _first_str(
            "BYD_CHAT_PROVIDER", "CHAT_PROVIDER", default="google_genai"
        )
    )
    transcribe_model: str = field(
        default_factory=lambda: _first_str(
            "BYD_TRANSCRIBE_MODEL", "TRANSCRIBE_MODEL", default="gpt-4o-transcribe"
        )
    )
    transcribe_provider: str = field(
        default_factory=lambda: _first_str(
            "BYD_TRANSCRIBE_PROVIDER", "TRANSCRIBE_PROVIDER", default="openai"
        )
    )

    # ----- Telegram credentials --------------------------------------------
    api_id: Optional[int] = field(
        default_factory=lambda: _first_int("BYD_TG_API_ID", "TG_API_ID")
    )
    api_hash: str = field(
        default_factory=lambda: _first_str("BYD_TG_API_HASH", "TG_API_HASH")
    )

    openai_api_key: str = field(
        default_factory=lambda: _first_str("BYD_OPENAI_API_KEY", "OPENAI_API_KEY")
    )
    groq_api_key: str = field(
        default_factory=lambda: _first_str("BYD_GROQ_API_KEY", "GROQ_API_KEY")
    )
    google_api_key: str = field(
        default_factory=lambda: _first_str("BYD_GOOGLE_API_KEY", "GOOGLE_API_KEY")
    )

    # ----- customer userbot (the AI sales agent talks to leads) ------------
    userbot_session: str = field(
        default_factory=lambda: os.environ.get("BYD_USERBOT_SESSION", "data/byd_userbot")
    )
    userbot_phone: str = field(
        default_factory=lambda: os.environ.get("BYD_USERBOT_PHONE", "")
    )

    # ----- operator bot (notifications + inline callbacks + manager agent) --
    bot_token: str = field(default_factory=lambda: os.environ.get("BYD_BOT_TOKEN", ""))
    bot_session: str = field(
        default_factory=lambda: os.environ.get("BYD_BOT_SESSION", "data/byd_bot")
    )

    # Telegram ids of clinic staff who, when they DM the operator bot, are served
    # the manager agent (advance deals, set arrival dates, request prepayment,
    # mark completed) and may click the funnel inline buttons.
    manager_allowed_ids: frozenset[int] = field(
        default_factory=lambda: _int_set("BYD_MANAGER_ALLOWED_IDS")
    )

    # Operators/managers who receive funnel notifications (call tasks, payment
    # alerts, escalations). The operator bot fans out to these chat ids.
    operator_chat_ids: frozenset[int] = field(
        default_factory=lambda: _int_set("BYD_OPERATOR_CHAT_IDS")
    )

    # ----- scheduler --------------------------------------------------------
    # The deal scheduler polls this often for due actions. 60s gives ample
    # precision for a funnel measured in hours/days; lower it for tighter SLAs.
    scheduler_interval_seconds: int = field(
        default_factory=lambda: int(os.environ.get("BYD_SCHEDULER_INTERVAL_SECONDS", "60"))
    )
    # Max due tasks claimed per poll — bounds the Telegram send burst after a
    # restart/backlog so we don't trip flood limits.
    scheduler_batch_size: int = field(
        default_factory=lambda: int(os.environ.get("BYD_SCHEDULER_BATCH_SIZE", "20"))
    )
    # A failed action is retried up to this many times (with linear backoff)
    # before it lands in the `failed` state for manual inspection.
    scheduler_max_attempts: int = field(
        default_factory=lambda: int(os.environ.get("BYD_SCHEDULER_MAX_ATTEMPTS", "5"))
    )

    # ----- clinic facts (interpolated into message templates) --------------
    clinic_address: str = field(
        default_factory=lambda: os.environ.get(
            "BYD_CLINIC_ADDRESS", "г. Ташкент (адрес уточняется)"
        )
    )
    arrival_time: str = field(
        default_factory=lambda: os.environ.get("BYD_ARRIVAL_TIME", "10:00")
    )
    # Hour-of-day (clinic tz) the "morning-of-arrival" message is sent.
    morning_message_hour: int = field(
        default_factory=lambda: int(os.environ.get("BYD_MORNING_MESSAGE_HOUR", "8"))
    )
    # Hour-of-day (clinic tz) the birthday greeting is sent.
    birthday_message_hour: int = field(
        default_factory=lambda: int(os.environ.get("BYD_BIRTHDAY_MESSAGE_HOUR", "9"))
    )

    instagram_url: str = field(
        default_factory=lambda: os.environ.get("BYD_INSTAGRAM_URL", "https://instagram.com/byd.medical")
    )
    community_url: str = field(
        default_factory=lambda: os.environ.get("BYD_COMMUNITY_URL", "https://t.me/byd_medical")
    )
    website_url: str = field(
        default_factory=lambda: os.environ.get("BYD_WEBSITE_URL", "https://byd.medical")
    )

    # ----- materials library (Stage 2/3) ----------------------------------
    # Durable public URLs for the price-list image and the testimonial/tour
    # videos. send_photos fetches image URLs; send_file_url downloads + sends
    # video URLs. Left blank = that media is simply skipped (text still sends).
    price_image_url: str = field(
        default_factory=lambda: os.environ.get("BYD_PRICE_IMAGE_URL", "")
    )
    testimonial_video_url: str = field(
        default_factory=lambda: os.environ.get("BYD_TESTIMONIAL_VIDEO_URL", "")
    )
    tour_video_url: str = field(
        default_factory=lambda: os.environ.get("BYD_TOUR_VIDEO_URL", "")
    )
    before_after_url: str = field(
        default_factory=lambda: os.environ.get("BYD_BEFORE_AFTER_URL", "")
    )
    # Comma-separated clinic photo URLs (sent as an album).
    clinic_photo_urls: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            u.strip()
            for u in os.environ.get("BYD_CLINIC_PHOTO_URLS", "").split(",")
            if u.strip()
        )
    )

    # ----- payments (Click.uz link; payment confirmed by an operator) -------
    prepayment_percent: int = field(
        default_factory=lambda: int(os.environ.get("BYD_PREPAYMENT_PERCENT", "10"))
    )
    click_service_id: str = field(
        default_factory=lambda: os.environ.get("BYD_CLICK_SERVICE_ID", "")
    )
    click_merchant_id: str = field(
        default_factory=lambda: os.environ.get("BYD_CLICK_MERCHANT_ID", "")
    )
    click_transaction_param: str = field(
        default_factory=lambda: os.environ.get("BYD_CLICK_TRANSACTION_PARAM", "")
    )
    click_return_url: str = field(
        default_factory=lambda: os.environ.get("BYD_CLICK_RETURN_URL", "")
    )

    # ----- Bitrix24 CRM (incoming-webhook integration) ----------------------
    # Incoming webhook base, e.g. https://xxx.bitrix24.ru/rest/1/abc123token/
    # (scopes: crm + tasks). Empty = the whole Bitrix integration is off.
    bitrix_webhook_url: str = field(
        default_factory=lambda: os.environ.get("BYD_BITRIX_WEBHOOK_URL", "")
    )
    # Deal pipeline (category) id holding the 8-stage BYD funnel — printed by
    # scripts/byd_bitrix_setup.py. Unset = integration off.
    bitrix_category_id: Optional[int] = field(
        default_factory=lambda: _first_int("BYD_BITRIX_CATEGORY_ID")
    )
    # Optional JSON override {"1": "C5:NEW", ..., "8": "C5:WON"} for portals where
    # the funnel was built by hand; default map derives from the setup script's
    # deterministic stage codes (see apps/byd/bitrix.py).
    bitrix_stage_map_json: str = field(
        default_factory=lambda: os.environ.get("BYD_BITRIX_STAGE_MAP", "")
    )
    # Portal user who owns created contacts/deals (deals land on their kanban).
    bitrix_assigned_by_id: Optional[int] = field(
        default_factory=lambda: _first_int("BYD_BITRIX_ASSIGNED_BY_ID")
    )
    # Assignee for mirrored operator tasks (defaults to bitrix_assigned_by_id).
    bitrix_task_responsible_id: Optional[int] = field(
        default_factory=lambda: _first_int("BYD_BITRIX_TASK_RESPONSIBLE_ID")
    )
    bitrix_currency: str = field(
        default_factory=lambda: os.environ.get("BYD_BITRIX_CURRENCY", "UZS")
    )
    # How often the pull job polls the pipeline for operator-made changes.
    bitrix_sync_interval_seconds: int = field(
        default_factory=lambda: int(os.environ.get("BYD_BITRIX_SYNC_INTERVAL_SECONDS", "30"))
    )
    # Mirror the client dialogue (inbound + automated outbound) into the deal
    # timeline so operators see the whole conversation in the CRM card.
    bitrix_mirror_messages: bool = field(
        default_factory=lambda: os.environ.get("BYD_BITRIX_MIRROR_MESSAGES", "1").lower()
        not in ("0", "false", "no")
    )

    # ----- behaviour tuning -------------------------------------------------
    request_timeout: int = 30
    debounce_seconds: float = 5.0
    read_delay_seconds: float = 3.0

    @property
    def bitrix_enabled(self) -> bool:
        return bool(self.bitrix_webhook_url and self.bitrix_category_id is not None)


config = BydConfig()
