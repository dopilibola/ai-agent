"""Singleton wiring of per-tenant services for Maskan.

Lazy module-level singletons (mirrors oygul/anfa/byd): voice transcriber, the
operator notifier (operator bot token → staff), and the Postgres-backed mute /
token / profile stores. Tests/overrides monkey-patch these slots.
"""

from __future__ import annotations

from typing import Optional

from core import MuteStore, ProfileStore, TokenStore
from db import PostgresMuteStore, PostgresProfileStore, PostgresTokenStore
from notifications import TelegramOperatorNotifier
from voice import VoiceTranscriber
from apps.maskan.config import MaskanConfig, config as default_config

# Whisper hint — Maskan's clients are often older, speak Uzbek or Russian, and
# use vocabulary a general model mis-hears ("go'rkov", "qabriston", "marmar").
VOICE_PROMPT = (
    "Voice message from a customer of Maskan, a grave-care service in "
    "Uzbekistan. Speakers use Uzbek or Russian and sometimes mix them — "
    "transcribe in the language(s) actually spoken, do not translate. Likely "
    "vocabulary: qabr, qabriston, marhum, parvarish, tozalash, o't, marmar, "
    "gul, go'rkov, buyurtma, to'lov; могила, кладбище, уборка, памятник."
)

_voice: Optional[VoiceTranscriber] = None


def get_voice(cfg: MaskanConfig = default_config) -> VoiceTranscriber:
    global _voice
    if _voice is None:
        _voice = VoiceTranscriber(
            model=cfg.transcribe_model,
            prompt=VOICE_PROMPT,
            provider=cfg.transcribe_provider,
            api_key=cfg.google_api_key if cfg.transcribe_provider == "google_genai" else None,
        )
    return _voice


_notifier: Optional[TelegramOperatorNotifier] = None


def get_notifier(cfg: MaskanConfig = default_config) -> TelegramOperatorNotifier:
    """Fans out funnel notifications to Maskan staff over the operator bot's
    HTTP API. The same bot token's Telethon client handles the inline callbacks."""
    global _notifier
    if _notifier is None:
        _notifier = TelegramOperatorNotifier(
            bot_token=cfg.bot_token,
            admin_chat_ids=cfg.operator_chat_ids,
            request_timeout=cfg.request_timeout,
        )
    return _notifier


_mute_store: Optional[MuteStore] = None


def get_mute_store(cfg: MaskanConfig = default_config) -> MuteStore:
    """Chats where the AI is muted (a human owns the conversation). Shared
    across the userbot, operator bot, and scheduler via Postgres."""
    global _mute_store
    if _mute_store is None:
        _mute_store = PostgresMuteStore("maskan")
    return _mute_store


_token_store: Optional[TokenStore] = None


def get_token_store(cfg: MaskanConfig = default_config) -> TokenStore:
    global _token_store
    if _token_store is None:
        _token_store = PostgresTokenStore("maskan")
    return _token_store


_profile_store: Optional[ProfileStore] = None


def get_profile_store(cfg: MaskanConfig = default_config) -> ProfileStore:
    global _profile_store
    if _profile_store is None:
        _profile_store = PostgresProfileStore("maskan")
    return _profile_store
