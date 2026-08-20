"""Singleton wiring of per-tenant services (voice transcriber + operator
notifier). The vector store + CRM client live in their own modules.
"""

from __future__ import annotations

from typing import Optional

from core import MuteStore, ProfileStore, TokenStore
from db import PostgresMuteStore, PostgresProfileStore, PostgresTokenStore
from notifications import TelegramOperatorNotifier
from voice import VoiceTranscriber
from apps.anfa.config import AnfaConfig, config as default_config

# Hint for Whisper — domain vocabulary + likely languages on short clips. We
# do NOT pin a `language` because Russian/Uzbek code-switching is common.
VOICE_PROMPT = (
    "Voice message from a patient in the Anfa (Анфа) clinic Telegram chat in "
    "Tashkent, Uzbekistan. Patients speak Uzbek (Latin or Cyrillic) or Russian "
    "and sometimes mix the two within one message — transcribe in the "
    "language(s) actually spoken, do not translate. Likely vocabulary: "
    "shifokor, qabul, vrach, priyom, zapis, jadval, UZI, EKG, kardiolog, "
    "ginekolog, terapevt, nevropatolog, urolog, jarrohlik."
)

_voice: Optional[VoiceTranscriber] = None


def get_voice(cfg: AnfaConfig = default_config) -> VoiceTranscriber:
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


def get_notifier(cfg: AnfaConfig = default_config) -> TelegramOperatorNotifier:
    global _notifier
    if _notifier is None:
        _notifier = TelegramOperatorNotifier(
            bot_token=cfg.bot_token,
            admin_chat_ids=cfg.moderator_chat_ids,
            request_timeout=cfg.request_timeout,
        )
    return _notifier


_mute_store: Optional[MuteStore] = None


def get_mute_store(cfg: AnfaConfig = default_config) -> MuteStore:
    """Per-tenant set of chat_ids where the catalog agent is currently muted.
    anfa no longer auto-mutes (it's an info bot that keeps answering); this
    backs only the admin panel's manual mute/unmute. Shared between the bot and
    userbot processes via Postgres."""
    global _mute_store
    if _mute_store is None:
        _mute_store = PostgresMuteStore("anfa")
    return _mute_store


_token_store: Optional[TokenStore] = None


def get_token_store(cfg: AnfaConfig = default_config) -> TokenStore:
    """Per-tenant ledger of token usage per chat — current run + cumulative."""
    global _token_store
    if _token_store is None:
        _token_store = PostgresTokenStore("anfa")
    return _token_store


_profile_store: Optional[ProfileStore] = None


def get_profile_store(cfg: AnfaConfig = default_config) -> ProfileStore:
    """Per-tenant cache of each chat's Telegram name/@username, captured by the
    bot and userbot so the admin panel can label chats by who they are."""
    global _profile_store
    if _profile_store is None:
        _profile_store = PostgresProfileStore("anfa")
    return _profile_store
