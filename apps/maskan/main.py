"""Entrypoints for the Maskan tenant.

`run()` runs everything in one process (recommended — the `maskan-all` pm2 app):
the client **userbot** (the AI care agent), the staff **operator bot** (manager
agent, funnel notifications, inline callbacks), the **care scheduler**, and the
**order watcher**. The scheduler and watcher both send client messages through
the userbot's live client, so keep them co-located — `run_userbot()` bundles
them for that reason.
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
from apps.maskan import funnel
from apps.maskan.config import MASKAN_TZ, config
from apps.maskan.notifications import (
    CLOSE_CALLBACK_PREFIX,
    TASK_DONE_CALLBACK_PREFIX,
)
from apps.maskan import payments
from apps.maskan.payment_watcher import PaymentWatcher
from apps.maskan.scheduler import CareScheduler
from apps.maskan.services import (
    get_mute_store,
    get_notifier,
    get_profile_store,
    get_token_store,
    get_voice,
)
from apps.maskan.tools import MANAGER_TOOLS, SALES_TOOLS, maskan_funnel_guard

logger = logging.getLogger(__name__)

SALES_PROMPT_PATH = Path(__file__).parent / "prompts" / "sales_system.md"
MANAGER_PROMPT_PATH = Path(__file__).parent / "prompts" / "manager_system.md"


def _sales_prompt() -> str:
    """Re-render the sales prompt with the live Tashkent datetime (re-read each
    invoke so admin-panel edits go live; plain replace, not str.format)."""
    template = SALES_PROMPT_PATH.read_text(encoding="utf-8")
    now = datetime.now(MASKAN_TZ)
    return template.replace("{now_iso}", now.strftime("%Y-%m-%d %H:%M")).replace(
        "{weekday}", now.strftime("%A")
    )


def _check_credentials() -> None:
    # OpenAI is only reached when a provider actually routes there; with chat +
    # transcription on Gemini the key goes unused, so don't demand it.
    needs_openai = "openai" in (config.chat_provider, config.transcribe_provider)
    if needs_openai and not config.openai_api_key:
        raise SystemExit("Set MASKAN_OPENAI_API_KEY (or OPENAI_API_KEY) in the environment.")
    if config.openai_api_key:
        os.environ["OPENAI_API_KEY"] = config.openai_api_key
    if config.chat_provider == "groq":
        if not config.groq_api_key:
            raise SystemExit("CHAT_PROVIDER=groq requires MASKAN_GROQ_API_KEY (or GROQ_API_KEY).")
        os.environ["GROQ_API_KEY"] = config.groq_api_key
    if config.chat_provider == "google_genai":
        if not config.google_api_key:
            raise SystemExit(
                "CHAT_PROVIDER=google_genai requires MASKAN_GOOGLE_API_KEY (or GOOGLE_API_KEY)."
            )
        os.environ["GOOGLE_API_KEY"] = config.google_api_key
    if not config.api_configured:
        # Not fatal: the agent can still converse and escalate. But every tool
        # that touches the catalogue or an order will fail, so say so loudly.
        logger.warning(
            "MASKAN_API_BASE/MASKAN_API_KEY not set — the Maskan backend is "
            "unreachable, so catalogue, grave and order tools will fail."
        )


def _build_sales_agent(checkpointer: Optional[BaseCheckpointSaver]) -> Agent:
    _check_credentials()
    return Agent(
        name="sales",
        model=config.chat_model,
        model_provider=config.chat_provider,
        system_prompt=_sales_prompt,  # callable — re-rendered per invoke
        tools=SALES_TOOLS,
        checkpointer=checkpointer,
        token_store=get_token_store(),
        # Approved operator answers are folded into the prompt per message —
        # see `core/learning/example_store.py`. Nothing is retrieved until a
        # human approves a pair in `scripts/gold_review.py`.
        examples_tenant="maskan" if config.learn_from_operators else None,
    )


def _build_manager_agent(checkpointer: Optional[BaseCheckpointSaver]) -> Agent:
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
        clear_reply="Suhbat tozalandi. Boshidan boshlaymiz 🌿",
        unsupported_voice_reply="Uzr, ovozli xabarni tushunib bo'lmadi. Yozib yuborsangiz bo'ladimi?",
        # Scheduled touches and result photos are recorded into the thread under
        # this label so the agent sees them on handback.
        outbound_note_template="[Mijozga avtomatik xabar yuborildi: {text}]",
        mute_store=get_mute_store(),
        profile_store=get_profile_store(),
        # On every inbound message: revive a cold case, re-arm the silence timers.
        message_guard=maskan_funnel_guard,
        # User account: a staff member answering by typing as this account =
        # takeover → auto-mute + capture (our own sends are suppressed so they
        # don't false-trigger it).
        capture_operator_messages=True,
    )


def _operator_channel(manager_agent: Agent) -> TelegramBotChannel:
    channel = TelegramBotChannel(
        name="operator",
        agent=manager_agent,
        # Only Maskan staff may DM the operator bot (and click its buttons).
        allowed_user_ids=config.manager_allowed_ids or None,
        bot_token=config.bot_token,
        api_id=config.api_id or 0,
        api_hash=config.api_hash,
        session=config.bot_session,
        voice=get_voice(),
        debounce_seconds=config.debounce_seconds,
        read_delay_seconds=config.read_delay_seconds,
        request_timeout=config.request_timeout,
        clear_reply="Suhbat tozalandi.",
        # NO mute_store here on purpose: the AI-mute/handoff is a *client*-side
        # concept. The operator bot is an internal staff tool and must always
        # answer allow-listed staff — even for a chat_id that is muted on the
        # client channel. The unmute button still works: its handler uses the
        # mute-store singleton directly.
        profile_store=get_profile_store(),
    )
    _register_callbacks(channel)
    return channel


def _authorised(event) -> bool:
    sender = event.sender_id
    allowed = config.manager_allowed_ids | config.operator_chat_ids
    return not allowed or sender in allowed


def _register_callbacks(channel: TelegramBotChannel) -> None:
    """Wire the funnel's inline buttons on the operator bot's client."""
    mute_store = get_mute_store()

    async def _on_task_done(event) -> None:
        if not _authorised(event):
            await event.answer("Ruxsat yo'q.", alert=True)
            return
        try:
            # msktask:<lead_id>:<kind> — acknowledging an SLA/payment nudge is
            # purely a UI act: it stops the button nagging, the funnel state is
            # driven by the backend.
            data = (event.data or b"").decode("utf-8", "ignore")
            try:
                await event.edit(buttons=[Button.inline("✅ Hal qilindi", b"noop")])
            except Exception:
                logger.debug("task button edit failed", exc_info=True)
            await event.answer("Belgilandi.")
            logger.info("Maskan operator acknowledged %s", data)
        except Exception:
            logger.exception("task-done callback failed")
            await event.answer("Xatolik.", alert=True)

    async def _on_close(event) -> None:
        if not _authorised(event):
            await event.answer("Ruxsat yo'q.", alert=True)
            return
        try:
            data = (event.data or b"").decode("utf-8", "ignore")
            lead_id = int(data[len(CLOSE_CALLBACK_PREFIX):])
            ok, message = await funnel.close_lead(lead_id, "Operator yopdi")
            if ok:
                try:
                    await event.edit(buttons=[Button.inline("🚫 Yopildi", b"noop")])
                except Exception:
                    logger.debug("close button edit failed", exc_info=True)
            await event.answer(message[:180])
        except Exception:
            logger.exception("close callback failed")
            await event.answer("Xatolik.", alert=True)

    async def _on_unmute(event) -> None:
        if not _authorised(event):
            await event.answer("Ruxsat yo'q.", alert=True)
            return
        try:
            data = (event.data or b"").decode("utf-8", "ignore")
            chat_id = int(data[len(UNMUTE_CALLBACK_PREFIX):])
            was_muted = await mute_store.unmute(chat_id)
            try:
                await event.edit(buttons=[Button.inline("✅ SI yoqildi", b"noop")])
            except Exception:
                logger.debug("unmute button edit failed", exc_info=True)
            await event.answer(
                "Sun'iy intellekt yoqildi." if was_muted else "Allaqachon yoqilgan edi."
            )
        except Exception:
            logger.exception("unmute callback failed")
            await event.answer("Xatolik.", alert=True)

    channel.add_callback_handler(TASK_DONE_CALLBACK_PREFIX, _on_task_done)
    channel.add_callback_handler(CLOSE_CALLBACK_PREFIX, _on_close)
    channel.add_callback_handler(UNMUTE_CALLBACK_PREFIX, _on_unmute)


def build_tenant(
    *,
    include_userbot: bool = True,
    include_operator: bool = True,
    include_jobs: bool = True,
    checkpointer: Optional[BaseCheckpointSaver] = None,
) -> Tenant:
    sales_agent = _build_sales_agent(checkpointer)
    manager_agent = _build_manager_agent(checkpointer)
    agents: dict = {"sales": sales_agent, "manager": manager_agent}

    channels: list = []
    customer_channel = None
    if include_userbot:
        if not config.api_id or not config.api_hash:
            logger.warning("TG_API_ID/TG_API_HASH not set — skipping the userbot channel.")
        else:
            customer_channel = _userbot_channel(sales_agent)
            channels.append(customer_channel)
    if include_operator:
        if not config.bot_token:
            logger.warning("MASKAN_BOT_TOKEN not set — skipping the operator bot channel.")
        else:
            channels.append(_operator_channel(manager_agent))

    # Wire the funnel's runtime handles: the live client channel (to message
    # clients out-of-band), the operator notifier, and the mute store.
    funnel.set_context(
        funnel.FunnelContext(
            config=config,
            customer_channel=customer_channel,
            notifier=get_notifier(),
            mute_store=get_mute_store(),
        )
    )

    sync_jobs: list = []
    if include_jobs:
        sync_jobs.append(CareScheduler(cfg=config))
        # Payments taken through our own merchant account: the webhook
        # process only records them, this job does the talking. (There is no
        # OrderWatcher any more — standalone mode owns the orders, so nothing
        # needs polling out of the Django backend.)
        sync_jobs.append(PaymentWatcher(cfg=config))
    return Tenant(id="maskan", agents=agents, channels=channels, sync_jobs=sync_jobs)


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        stream=sys.stdout,
    )


async def _serve(**flags: bool) -> None:
    async with checkpointer_scope() as checkpointer:
        tenant = build_tenant(checkpointer=checkpointer, **flags)
        # Standalone: the catalogue, graves and orders are ours, so there is no
        # backend to probe. What *can* leave the bot unable to close a sale is a
        # missing payment provider — say so once, at startup.
        if not payments.any_provider_enabled(config):
            logger.warning(
                "No payment provider configured (MASKAN_PAYME_MERCHANT_ID / "
                "MASKAN_UZUM_SERVICE_ID) — orders can be created but not paid."
            )
        await Runtime(tenant).run_async()


def _bootstrap() -> None:
    load_dotenv()
    _setup_logging()


def run() -> None:
    _bootstrap()
    asyncio.run(_serve())


def run_userbot() -> None:
    # Userbot + jobs: the scheduler and watcher message clients through the
    # userbot's client, so they must share a process.
    _bootstrap()
    asyncio.run(_serve(include_userbot=True, include_operator=False, include_jobs=True))


def run_operator() -> None:
    _bootstrap()
    asyncio.run(_serve(include_userbot=False, include_operator=True, include_jobs=False))


if __name__ == "__main__":
    run()
