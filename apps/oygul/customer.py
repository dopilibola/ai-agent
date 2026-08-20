"""Oygul customer-facing agent (Lola) on a Telegram user account."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from langgraph.checkpoint.base import BaseCheckpointSaver

from channels.telegram import TelegramUserChannel
from core import Agent, Runtime, Tenant
from db import checkpointer_scope
from apps.oygul.config import config
from apps.oygul.services import (
    get_mute_store,
    get_profile_store,
    get_token_store,
    get_voice,
)
from apps.oygul.tools import CUSTOMER_TOOLS

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).parent / "prompts" / "lola_system.md"


def build_tenant(checkpointer: Optional[BaseCheckpointSaver] = None) -> Tenant:
    if not config.openai_api_key:
        raise SystemExit(
            "Set OYGUL_OPENAI_API_KEY (or OPENAI_API_KEY) in the environment."
        )
    # Inject so litellm / openai SDK pick it up.
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

    lola = Agent(
        name="lola",
        model=config.chat_model,
        model_provider=config.chat_provider,
        # Callable so the prompt file is re-read every invoke — edits from the
        # admin panel take effect live (the graph rebuilds only when the text
        # actually changes).
        system_prompt=lambda: PROMPT_PATH.read_text(encoding="utf-8"),
        tools=CUSTOMER_TOOLS,
        checkpointer=checkpointer,
        token_store=get_token_store(),
    )

    customer_channel = TelegramUserChannel(
        name="customer",
        agent=lola,
        api_id=config.api_id or 0,
        api_hash=config.api_hash,
        session=config.customer_session,
        voice=get_voice(),
        debounce_seconds=config.debounce_seconds,
        read_delay_seconds=config.read_delay_seconds,
        request_timeout=config.request_timeout,
        mute_store=get_mute_store(),
        profile_store=get_profile_store(),
        # Lola is a user account: while a chat is muted after a sale, an operator
        # replies by typing as Lola. Capture those so the agent sees them on
        # handback instead of resuming blind to the operator's half.
        capture_operator_messages=True,
    )

    return Tenant(
        id="oygul",
        agents={"lola": lola},
        channels=[customer_channel],
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
