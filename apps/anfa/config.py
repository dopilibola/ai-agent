"""Environment-driven config for the anfa (Anfa clinic) tenant."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import timedelta, timezone
from typing import Optional

from dotenv import load_dotenv

# `config = AnfaConfig()` runs at import time — load .env before any field
# default_factory reads os.environ.
load_dotenv()


def _int(name: str, default: Optional[int] = None) -> Optional[int]:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


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


# Anfa clinic — Asia/Tashkent (UTC+5). Matches the +05:00 offset used by the
# CRM's datetime slots.
CLINIC_TZ = timezone(timedelta(hours=5))


@dataclass(frozen=True)
class AnfaConfig:
    chat_model: str = field(
        default_factory=lambda: _first_str("ANFA_CHAT_MODEL", "CHAT_MODEL", default="gemini-3.6-flash")
    )
    chat_provider: str = field(
        default_factory=lambda: _first_str(
            "ANFA_CHAT_PROVIDER", "CHAT_PROVIDER", default="google_genai"
        )
    )
    transcribe_model: str = field(
        default_factory=lambda: _first_str(
            "ANFA_TRANSCRIBE_MODEL", "TRANSCRIBE_MODEL", default="gpt-4o-transcribe"
        )
    )
    transcribe_provider: str = field(
        default_factory=lambda: _first_str(
            "ANFA_TRANSCRIBE_PROVIDER", "TRANSCRIBE_PROVIDER", default="openai"
        )
    )
    embed_model: str = field(
        default_factory=lambda: _first_str(
            "ANFA_EMBED_MODEL", "EMBED_MODEL", default="openai/text-embedding-3-small"
        )
    )
    embed_batch_size: int = field(
        default_factory=lambda: int(os.environ.get("LITELLM_EMBEDDING_BATCH_SIZE", "50"))
    )

    # Tenant-prefixed first, fall back to shared TG_API_ID/HASH
    api_id: Optional[int] = field(
        default_factory=lambda: _first_int("ANFA_TG_API_ID", "TG_API_ID")
    )
    api_hash: str = field(
        default_factory=lambda: _first_str("ANFA_TG_API_HASH", "TG_API_HASH")
    )

    # Per-tenant OpenAI key — set into os.environ by the entrypoint. Only used
    # by the OpenAI-backed paths (openai chat/transcribe, or an openai/* embed
    # model); unused when chat + transcribe + embeddings all run on Gemini.
    openai_api_key: str = field(
        default_factory=lambda: _first_str("ANFA_OPENAI_API_KEY", "OPENAI_API_KEY")
    )
    groq_api_key: str = field(
        default_factory=lambda: _first_str("ANFA_GROQ_API_KEY", "GROQ_API_KEY")
    )
    google_api_key: str = field(
        default_factory=lambda: _first_str("ANFA_GOOGLE_API_KEY", "GOOGLE_API_KEY")
    )

    # Bot channel
    bot_token: str = field(default_factory=lambda: os.environ.get("ANFA_BOT_TOKEN", ""))
    bot_session: str = field(
        default_factory=lambda: os.environ.get("ANFA_BOT_SESSION", "data/anfa_bot")
    )

    # Userbot channel
    userbot_session: str = field(
        default_factory=lambda: os.environ.get("ANFA_USERBOT_SESSION", "data/anfa_userbot")
    )
    userbot_phone: str = field(
        default_factory=lambda: os.environ.get("ANFA_USERBOT_PHONE", "")
    )

    # Moderator notifications
    moderator_chat_ids: frozenset[int] = field(
        default_factory=lambda: _int_set("ANFA_MODERATOR_CHAT_IDS", "1207413315,276690768")
    )

    # Clinic-admin (manager) access — Telegram user ids of staff who, when they
    # message the anfa bot, are served the MANAGER agent (manage catalog prices,
    # doctors, and muted chats) instead of the client catalog advisor. Defaults
    # to the moderator ids (the same people), so the manager bot works out of the
    # box; set ANFA_MANAGER_ALLOWED_IDS to scope it to a narrower list.
    manager_allowed_ids: frozenset[int] = field(
        default_factory=lambda: (
            _int_set("ANFA_MANAGER_ALLOWED_IDS")
            or _int_set("ANFA_MODERATOR_CHAT_IDS", "1207413315,276690768")
        )
    )

    # Vector DB
    chroma_path: str = field(
        default_factory=lambda: os.environ.get("ANFA_CHROMA_PATH", "data/anfa_chroma")
    )
    collection_name: str = field(
        default_factory=lambda: os.environ.get("ANFA_COLLECTION_NAME", "clinic_kb")
    )

    # Sync loop — short by default so an Excel re-import from the admin panel
    # (which writes Postgres) becomes searchable within minutes.
    sync_interval_seconds: int = field(
        default_factory=lambda: int(os.environ.get("ANFA_SYNC_INTERVAL_SECONDS", "300"))
    )

    request_timeout: int = 30

    # Behaviour tuning
    debounce_seconds: float = 5.0
    read_delay_seconds: float = 3.0


config = AnfaConfig()
