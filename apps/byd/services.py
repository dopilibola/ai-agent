"""Singleton wiring of per-tenant services for BYD Medical.

Lazy module-level singletons (mirrors oygul/anfa): voice transcriber, the
operator notifier (operator bot token → moderators), and the Postgres-backed
mute / token / profile stores. Tests/overrides monkey-patch these slots.
"""

from __future__ import annotations

from typing import Optional

from core import MuteStore, ProfileStore, TokenStore
from db import PostgresMuteStore, PostgresProfileStore, PostgresTokenStore
from notifications import TelegramOperatorNotifier
from voice import VoiceTranscriber
from apps.byd.config import BydConfig, config as default_config

# Whisper hint — likely languages + clinic vocabulary on short voice notes.
VOICE_PROMPT = (
    "Voice message from a prospective patient of BYD Medical, a detox clinic in "
    "Tashkent, Uzbekistan. Speakers use Russian or Uzbek and sometimes mix them — "
    "transcribe in the language(s) actually spoken, do not translate. Likely "
    "vocabulary: детокс, очищение, программа, заезд, предоплата, бронь, "
    "консультация, клиника."
)

_voice: Optional[VoiceTranscriber] = None


def get_voice(cfg: BydConfig = default_config) -> VoiceTranscriber:
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


def get_notifier(cfg: BydConfig = default_config) -> TelegramOperatorNotifier:
    """Fans out funnel notifications to operators/managers over the operator
    bot's HTTP API. The same bot token's Telethon client handles the inline
    callbacks."""
    global _notifier
    if _notifier is None:
        _notifier = TelegramOperatorNotifier(
            bot_token=cfg.bot_token,
            admin_chat_ids=cfg.operator_chat_ids,
            request_timeout=cfg.request_timeout,
        )
    return _notifier


_mute_store: Optional[MuteStore] = None


def get_mute_store(cfg: BydConfig = default_config) -> MuteStore:
    """Chats where the AI is muted (an operator owns the conversation). Shared
    across the userbot, operator bot, and scheduler processes via Postgres."""
    global _mute_store
    if _mute_store is None:
        _mute_store = PostgresMuteStore("byd")
    return _mute_store


_token_store: Optional[TokenStore] = None


def get_token_store(cfg: BydConfig = default_config) -> TokenStore:
    global _token_store
    if _token_store is None:
        _token_store = PostgresTokenStore("byd")
    return _token_store


_profile_store: Optional[ProfileStore] = None


def get_profile_store(cfg: BydConfig = default_config) -> ProfileStore:
    global _profile_store
    if _profile_store is None:
        _profile_store = PostgresProfileStore("byd")
    return _profile_store
