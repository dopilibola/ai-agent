"""Oygul merchant catalog agent on a @BotFather bot (allow-listed)."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from langgraph.checkpoint.base import BaseCheckpointSaver

from telethon import Button

from channels.telegram import TelegramBotChannel
from core import Agent, Runtime, Tenant
from db import checkpointer_scope
from notifications import UNMUTE_CALLBACK_PREFIX
from apps.oygul.config import config
from apps.oygul.services import (
    get_mute_store,
    get_profile_store,
    get_token_store,
    get_voice,
)
from apps.oygul.tools import MERCHANT_TOOLS

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent / "prompts" / "merchant_system.md"


def build_tenant(checkpointer: Optional[BaseCheckpointSaver] = None) -> Tenant:
    if not config.openai_api_key:
        raise SystemExit(
            "Set OYGUL_OPENAI_API_KEY (or OPENAI_API_KEY) in the environment."
        )
    os.environ["OPENAI_API_KEY"] = config.openai_api_key
    if config.chat_provider == "groq":
        if not config.groq_api_key:
            raise SystemExit(
                "CHAT_PROVIDER=groq requires OYGUL_GROQ_API_KEY (or GROQ_API_KEY)."
            )
        os.environ["GROQ_API_KEY"] = config.groq_api_key
    if config.chat_provider == "google_genai":
        if not config.google_api_key:
            raise SystemExit(
                "CHAT_PROVIDER=google_genai requires OYGUL_GOOGLE_API_KEY (or GOOGLE_API_KEY)."
            )
        os.environ["GOOGLE_API_KEY"] = config.google_api_key
    if not config.merchant_bot_token:
        raise SystemExit("OYGUL_MERCHANT_BOT_TOKEN must be set (from @BotFather).")
    if not config.merchant_allowed_ids:
        raise SystemExit(
            "OYGUL_MERCHANT_ALLOWED_IDS must list the Telegram user IDs that may use this bot."
        )

    merchant = Agent(
        name="merchant",
        model=config.chat_model,
        model_provider=config.chat_provider,
        # Callable so the prompt file is re-read every invoke — edits from the
        # admin panel take effect live (the graph rebuilds only when the text
        # actually changes).
        system_prompt=lambda: PROMPT_PATH.read_text(encoding="utf-8"),
        tools=MERCHANT_TOOLS,
        checkpointer=checkpointer,
        token_store=get_token_store(),
    )

    merchant_channel = TelegramBotChannel(
        name="merchant",
        agent=merchant,
        bot_token=config.merchant_bot_token,
        api_id=config.api_id or 0,
        api_hash=config.api_hash,
        session=config.merchant_session,
        voice=get_voice(),
        allowed_user_ids=config.merchant_allowed_ids,
        debounce_seconds=config.debounce_seconds,
        read_delay_seconds=config.read_delay_seconds,
        request_timeout=config.request_timeout,
        retain_images=True,  # keep uploaded photos so add_bouquet_tool can host them
        profile_store=get_profile_store(),
    )

    # The merchant bot is the same bot the operator notifier uses to fan out
    # order messages, so CallbackQuery updates from the "Подключить ИИ" button
    # arrive on this client. Operators click → Lola is unmuted for that chat.
    mute_store = get_mute_store()
    operator_ids = frozenset(config.operator_chat_ids)

    async def _on_unmute_click(event) -> None:
        try:
            data = (event.data or b"").decode("utf-8", errors="ignore")
            if not data.startswith(UNMUTE_CALLBACK_PREFIX):
                return
            sender_id = event.sender_id
            if operator_ids and sender_id not in operator_ids:
                logger.warning(
                    "Ignored unmute click from non-operator %s", sender_id
                )
                await event.answer(
                    "Только операторы могут подключать ИИ.", alert=True
                )
                return
            try:
                chat_id = int(data[len(UNMUTE_CALLBACK_PREFIX) :])
            except ValueError:
                logger.warning("Bad unmute callback_data: %r", data)
                return
            was_muted = await mute_store.unmute(chat_id)
            try:
                await event.edit(buttons=[Button.inline("✅ ИИ подключён", b"noop")])
            except Exception:
                logger.exception(
                    "Failed to swap inline button after unmute for %s", chat_id
                )
            if was_muted:
                await event.answer("ИИ подключён. Лола снова отвечает клиенту.")
            else:
                await event.answer("ИИ уже был активен.")
        except Exception:
            logger.exception("Failed to handle unmute callback")

    merchant_channel.add_callback_handler(UNMUTE_CALLBACK_PREFIX, _on_unmute_click)

    return Tenant(
        id="oygul",
        agents={"merchant": merchant},
        channels=[merchant_channel],
    )


async def _amain() -> None:
    async with checkpointer_scope() as checkpointer:
        await Runtime(build_tenant(checkpointer=checkpointer)).run_async()


def run() -> None:
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        stream=sys.stdout,
    )
    asyncio.run(_amain())


if __name__ == "__main__":
    run()
