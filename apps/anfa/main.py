"""Entrypoints for the anfa (Anfa clinic) tenant.

`run()` runs all three workers in one process — the most common deployment.
The split entrypoints exist for ops setups that scale the bot, userbot, or
sync loop independently.

The agent is a service-catalog advisor: it searches the clinic's catalog
(fed by their Excel export → Postgres → Chroma), quotes prices, and directs
clients to walk into the clinic. There is no online booking; the only
human-handoff is the `request_operator` tool, whose moderator notification
re-uses the same bot's "Подключить ИИ" callback button.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from langgraph.checkpoint.base import BaseCheckpointSaver

from telethon import Button

from channels.telegram import TelegramBotChannel, TelegramUserChannel
from core import Agent, Runtime, Tenant
from db import checkpointer_scope
from notifications import UNMUTE_CALLBACK_PREFIX
from apps.anfa.config import CLINIC_TZ, config
from apps.anfa.services import (
    get_mute_store,
    get_profile_store,
    get_token_store,
    get_voice,
)
from apps.anfa.manager_tools import MANAGER_TOOLS
from apps.anfa.sync import KnowledgeBaseSync
from apps.anfa.tools import CATALOG_TOOLS
from apps.anfa.triage import emergency_triage

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent / "prompts" / "catalog_system.md"
MANAGER_PROMPT_PATH = Path(__file__).parent / "prompts" / "manager_system.md"


def _system_prompt() -> str:
    """Re-render the prompt with the current clinic-timezone datetime.

    Re-read every invoke so admin-panel edits go live without a restart. Uses
    plain string replacement (not str.format) so that braces an editor might
    introduce in the prompt body don't blow up rendering — only the two known
    placeholders are substituted.
    """
    template = PROMPT_PATH.read_text(encoding="utf-8")
    now = datetime.now(CLINIC_TZ)
    return template.replace(
        "{now_iso}", now.strftime("%Y-%m-%d %H:%M")
    ).replace("{weekday}", now.strftime("%A"))


def _check_credentials() -> None:
    # OpenAI is only needed for the OpenAI-backed paths (chat/transcribe on
    # openai, or an openai/* embed model). With everything on Gemini it goes
    # unused, but we still require the key present as a fallback safety net.
    if not config.openai_api_key:
        raise SystemExit(
            "Set ANFA_OPENAI_API_KEY (or OPENAI_API_KEY) in the environment."
        )
    os.environ["OPENAI_API_KEY"] = config.openai_api_key
    if config.chat_provider == "groq":
        if not config.groq_api_key:
            raise SystemExit(
                "CHAT_PROVIDER=groq requires ANFA_GROQ_API_KEY (or GROQ_API_KEY)."
            )
        os.environ["GROQ_API_KEY"] = config.groq_api_key
    if config.chat_provider == "google_genai":
        if not config.google_api_key:
            raise SystemExit(
                "CHAT_PROVIDER=google_genai requires ANFA_GOOGLE_API_KEY (or GOOGLE_API_KEY)."
            )
        os.environ["GOOGLE_API_KEY"] = config.google_api_key
    # LiteLLM's Gemini provider (embeddings) reads GEMINI_API_KEY specifically.
    if config.embed_model.startswith("gemini/"):
        if not config.google_api_key:
            raise SystemExit(
                "EMBED_MODEL=gemini/* requires ANFA_GOOGLE_API_KEY (or GOOGLE_API_KEY)."
            )
        os.environ["GEMINI_API_KEY"] = config.google_api_key


def _build_catalog_agent(
    checkpointer: Optional[BaseCheckpointSaver] = None,
) -> Agent:
    _check_credentials()
    return Agent(
        name="catalog",
        model=config.chat_model,
        model_provider=config.chat_provider,
        system_prompt=_system_prompt,  # callable — re-rendered per invoke
        tools=CATALOG_TOOLS,
        checkpointer=checkpointer,
        token_store=get_token_store(),
    )


def _build_manager_agent(
    checkpointer: Optional[BaseCheckpointSaver] = None,
) -> Agent:
    """Clinic-admin agent served to allow-listed staff on the same bot — manages
    catalog prices, the doctor roster, and muted chats. Its prompt is re-read
    per invoke so admin-panel edits go live without a restart."""
    _check_credentials()
    return Agent(
        name="manager",
        model=config.chat_model,
        model_provider=config.chat_provider,
        system_prompt=lambda: MANAGER_PROMPT_PATH.read_text(encoding="utf-8"),
        tools=MANAGER_TOOLS,
        checkpointer=checkpointer,
        token_store=get_token_store(),
    )


def _bot_channel(agent: Agent, manager_agent: Optional[Agent] = None) -> TelegramBotChannel:
    channel = TelegramBotChannel(
        name="merchant",
        agent=agent,
        bot_token=config.bot_token,
        api_id=config.api_id or 0,
        api_hash=config.api_hash,
        session=config.bot_session,
        voice=get_voice(),
        debounce_seconds=config.debounce_seconds,
        read_delay_seconds=config.read_delay_seconds,
        request_timeout=config.request_timeout,
        clear_reply="Контекст очищен / Suhbat tozalandi 🌿",
        unsupported_voice_reply=(
            "Извините, не удалось распознать голосовое. / "
            "Kechirasiz, ovozli xabarni eshita olmadim."
        ),
        mute_store=get_mute_store(),
        profile_store=get_profile_store(),
        # Deterministic emergency triage runs before the agent ever sees the
        # message, so the safety interstitial doesn't depend on the prompt.
        message_guard=emergency_triage,
        # Allow-listed clinic staff are routed to the manager agent on this same
        # bot (ANFA_MANAGER_ALLOWED_IDS); everyone else gets the catalog advisor.
        manager_agent=manager_agent,
        manager_user_ids=config.manager_allowed_ids if manager_agent else None,
    )
    _register_unmute_handler(channel)
    return channel


def _register_unmute_handler(channel: TelegramBotChannel) -> None:
    """Attach the 'Подключить ИИ' callback handler on the channel's bot
    client. anfa's bot is the same bot the notifier uses to message
    moderators, so callback updates arrive on this client."""
    mute_store = get_mute_store()
    moderator_ids = frozenset(config.moderator_chat_ids)

    async def _on_unmute_click(event) -> None:
        try:
            data = (event.data or b"").decode("utf-8", errors="ignore")
            if not data.startswith(UNMUTE_CALLBACK_PREFIX):
                return
            sender_id = event.sender_id
            if moderator_ids and sender_id not in moderator_ids:
                logger.warning("Ignored unmute click from non-moderator %s", sender_id)
                await event.answer(
                    "Только модераторы могут подключать ИИ.", alert=True
                )
                return
            try:
                chat_id = int(data[len(UNMUTE_CALLBACK_PREFIX):])
            except ValueError:
                logger.warning("Bad unmute callback_data: %r", data)
                return
            was_muted = await mute_store.unmute(chat_id)
            try:
                await event.edit(buttons=[Button.inline("✅ ИИ подключён", b"noop")])
            except Exception:
                logger.exception("Failed to swap inline button for %s", chat_id)
            if was_muted:
                await event.answer("ИИ подключён. Бот снова отвечает пациенту.")
            else:
                await event.answer("ИИ уже был активен.")
        except Exception:
            logger.exception("Failed to handle unmute callback")

    channel.add_callback_handler(UNMUTE_CALLBACK_PREFIX, _on_unmute_click)


def _userbot_channel(agent: Agent) -> TelegramUserChannel:
    return TelegramUserChannel(
        name="customer",
        agent=agent,
        api_id=config.api_id or 0,
        api_hash=config.api_hash,
        session=config.userbot_session,
        voice=get_voice(),
        debounce_seconds=config.debounce_seconds,
        read_delay_seconds=config.read_delay_seconds,
        request_timeout=config.request_timeout,
        clear_reply="Контекст очищен / Suhbat tozalandi 🌿",
        unsupported_voice_reply=(
            "Извините, не удалось распознать голосовое. / "
            "Kechirasiz, ovozli xabarni eshita olmadim."
        ),
        mute_store=get_mute_store(),
        profile_store=get_profile_store(),
        message_guard=emergency_triage,
        # User account: while muted after an operator handoff, a person answers
        # the client by typing as this account. Capture those so the agent has
        # the operator's side when control is handed back.
        capture_operator_messages=True,
    )


def build_tenant(
    *,
    include_bot: bool = True,
    include_userbot: bool = True,
    include_sync: bool = True,
    checkpointer: Optional[BaseCheckpointSaver] = None,
) -> Tenant:
    agent = _build_catalog_agent(checkpointer)
    agents: dict = {"catalog": agent}

    # Clinic-admin manager agent — served on the SAME bot to allow-listed staff
    # (ANFA_MANAGER_ALLOWED_IDS, default = the moderators), not a separate bot.
    # Built only when an allow-list is configured.
    manager_agent: Optional[Agent] = None
    if config.manager_allowed_ids:
        manager_agent = _build_manager_agent(checkpointer)
        agents["manager"] = manager_agent

    channels: list = []
    if include_bot:
        if not config.bot_token:
            logger.warning(
                "ANFA_BOT_TOKEN not set — skipping the bot channel."
            )
        else:
            channels.append(_bot_channel(agent, manager_agent))
    if include_userbot:
        if not config.api_id or not config.api_hash:
            logger.warning(
                "TG_API_ID/TG_API_HASH not set — skipping the userbot channel."
            )
        else:
            # If the session isn't authorised, the channel's `_start_client`
            # will fail fast and the runtime keeps the other workers up.
            channels.append(_userbot_channel(agent))
    sync_jobs = [KnowledgeBaseSync(cfg=config)] if include_sync else []
    return Tenant(
        id="anfa",
        agents=agents,
        channels=channels,
        sync_jobs=sync_jobs,
    )


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        stream=sys.stdout,
    )


async def _serve(**flags: bool) -> None:
    async with checkpointer_scope() as checkpointer:
        tenant = build_tenant(checkpointer=checkpointer, **flags)
        await Runtime(tenant).run_async()


def _bootstrap() -> None:
    load_dotenv()
    _setup_logging()


def run() -> None:
    _bootstrap()
    asyncio.run(_serve())


def run_bot() -> None:
    _bootstrap()
    asyncio.run(_serve(include_bot=True, include_userbot=False, include_sync=False))


def run_userbot() -> None:
    _bootstrap()
    asyncio.run(_serve(include_bot=False, include_userbot=True, include_sync=False))


def run_sync() -> None:
    _bootstrap()
    asyncio.run(_serve(include_bot=False, include_userbot=False, include_sync=True))


if __name__ == "__main__":
    run()
