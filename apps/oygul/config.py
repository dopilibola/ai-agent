"""Environment-driven config for the oygul tenant."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from dotenv import load_dotenv

# `config = OygulConfig()` runs at import time — load .env before any field
# default_factory reads os.environ.
load_dotenv()


def _int(name: str, default: Optional[int] = None) -> Optional[int]:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _int_set(name: str) -> frozenset[int]:
    raw = os.environ.get(name, "")
    return frozenset(int(x.strip()) for x in raw.split(",") if x.strip())


def _first_str(*names: str, default: str = "") -> str:
    """Return the first non-empty env value among `names`."""
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


@dataclass(frozen=True)
class OygulConfig:
    # Shared
    chat_model: str = field(
        default_factory=lambda: _first_str("OYGUL_CHAT_MODEL", "CHAT_MODEL", default="gemini-3.6-flash")
    )
    chat_provider: str = field(
        default_factory=lambda: _first_str(
            "OYGUL_CHAT_PROVIDER", "CHAT_PROVIDER", default="google_genai"
        )
    )
    transcribe_model: str = field(
        default_factory=lambda: _first_str(
            "OYGUL_TRANSCRIBE_MODEL", "TRANSCRIBE_MODEL", default="gpt-4o-transcribe"
        )
    )
    transcribe_provider: str = field(
        default_factory=lambda: _first_str(
            "OYGUL_TRANSCRIBE_PROVIDER", "TRANSCRIBE_PROVIDER", default="openai"
        )
    )

    # Tenant-prefixed first, fall back to shared TG_API_ID/HASH
    api_id: Optional[int] = field(
        default_factory=lambda: _first_int("OYGUL_TG_API_ID", "TG_API_ID")
    )
    api_hash: str = field(
        default_factory=lambda: _first_str("OYGUL_TG_API_HASH", "TG_API_HASH")
    )

    # Per-tenant OpenAI key — set into os.environ by the entrypoint so litellm /
    # openai SDK pick it up. Still required for voice transcription + CLIP
    # downloads even when the chat provider is something else.
    openai_api_key: str = field(
        default_factory=lambda: _first_str("OYGUL_OPENAI_API_KEY", "OPENAI_API_KEY")
    )
    groq_api_key: str = field(
        default_factory=lambda: _first_str("OYGUL_GROQ_API_KEY", "GROQ_API_KEY")
    )
    google_api_key: str = field(
        default_factory=lambda: _first_str("OYGUL_GOOGLE_API_KEY", "GOOGLE_API_KEY")
    )

    # Customer (user account) channel
    customer_session: str = field(
        default_factory=lambda: os.environ.get("OYGUL_CUSTOMER_SESSION", "data/oygul_customer")
    )

    # Merchant (bot token) channel
    merchant_bot_token: str = field(
        default_factory=lambda: os.environ.get("OYGUL_MERCHANT_BOT_TOKEN", "")
    )
    merchant_session: str = field(
        default_factory=lambda: os.environ.get("OYGUL_MERCHANT_SESSION", "data/oygul_merchant")
    )
    merchant_allowed_ids: frozenset[int] = field(
        default_factory=lambda: _int_set("OYGUL_MERCHANT_ALLOWED_IDS")
    )

    # Operator notifications (reuses merchant bot for fan-out)
    operator_chat_ids: frozenset[int] = field(
        default_factory=lambda: _int_set("OYGUL_OPERATOR_CHAT_IDS")
    )

    # Click.uz payments
    click_service_id: str = field(
        default_factory=lambda: os.environ.get("OYGUL_CLICK_SERVICE_ID", "30067")
    )
    click_merchant_id: str = field(
        default_factory=lambda: os.environ.get("OYGUL_CLICK_MERCHANT_ID", "22535")
    )
    click_transaction_param: str = field(
        default_factory=lambda: os.environ.get("OYGUL_CLICK_TRANSACTION_PARAM", "165884")
    )
    click_return_url: str = field(
        default_factory=lambda: os.environ.get(
            "OYGUL_CLICK_RETURN_URL", "tg://user?id=7451326382"
        )
    )

    # Vector store
    chroma_path: str = field(
        default_factory=lambda: os.environ.get("OYGUL_CHROMA_PATH", "data/oygul_chroma")
    )
    collection_name: str = field(
        default_factory=lambda: os.environ.get("OYGUL_COLLECTION_NAME", "bouquets")
    )
    clip_model: str = field(
        default_factory=lambda: os.environ.get("CLIP_MODEL", "clip-ViT-B-32")
    )
    request_timeout: int = 15

    # Branch a merchant-added bouquet is filed under (stored on the row + Chroma
    # metadata). Single-branch shops can leave it blank.
    branch_id: str = field(
        default_factory=lambda: os.environ.get("OYGUL_BRANCH_ID", "")
    )

    # Cloudflare Images — durable public hosting for merchant-uploaded bouquet
    # photos (the catalogue stores the delivery URL; Telegram fetches it when
    # Lola sends the album). Token is Images-scoped; keep it out of source.
    cf_account_id: str = field(
        default_factory=lambda: _first_str("OYGUL_CF_ACCOUNT_ID", "CF_ACCOUNT_ID")
    )
    cf_images_token: str = field(
        default_factory=lambda: _first_str("OYGUL_CF_IMAGES_TOKEN", "CF_IMAGES_TOKEN")
    )
    cf_images_variant: str = field(
        default_factory=lambda: os.environ.get("OYGUL_CF_IMAGES_VARIANT", "public")
    )

    # Behaviour tuning
    debounce_seconds: float = 5.0
    read_delay_seconds: float = 3.0
    search_status_delay_seconds: float = 2.0
    delivery_fee_sum: int = 70_000


config = OygulConfig()
