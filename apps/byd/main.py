"""Entrypoints for the BYD Medical tenant.

`run()` runs everything in one process (recommended — the `byd-all` pm2 app):
the customer **userbot** (AI sales agent), the operator **bot** (manager agent +
funnel notifications + inline-button callbacks), and the **deal scheduler**. The
scheduler sends client messages through the userbot's live client, so keep them
co-located (run_userbot bundles the scheduler for that reason).
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
from apps.byd import funnel
from apps.byd.config import CLINIC_TZ, config
from apps.byd.notifications import (
    CALL_CALLBACK_PREFIX,
    MATERIAL_CALLBACK_PREFIX,
    PAID_CALLBACK_PREFIX,
    TASK_DONE_CALLBACK_PREFIX,
)
from apps.byd.repository import get_repository
from apps.byd.scheduler import DealScheduler
from apps.byd.services import (
    get_mute_store,
    get_notifier,
    get_profile_store,
    get_token_store,
    get_voice,
)
from apps.byd.tools import MANAGER_TOOLS, SALES_TOOLS, byd_funnel_guard

logger = logging.getLogger(__name__)

SALES_PROMPT_PATH = Path(__file__).parent / "prompts" / "sales_system.md"
MANAGER_PROMPT_PATH = Path(__file__).parent / "prompts" / "manager_system.md"


def _sales_prompt() -> str:
    """Re-render the sales prompt with the live clinic-tz datetime (re-read each
    invoke so admin-panel edits go live; plain replace, not str.format)."""
    template = SALES_PROMPT_PATH.read_text(encoding="utf-8")
    now = datetime.now(CLINIC_TZ)
    return template.replace("{now_iso}", now.strftime("%Y-%m-%d %H:%M")).replace(
        "{weekday}", now.strftime("%A")
    )


def _check_credentials() -> None:
    if not config.openai_api_key:
        raise SystemExit("Set BYD_OPENAI_API_KEY (or OPENAI_API_KEY) in the environment.")
    os.environ["OPENAI_API_KEY"] = config.openai_api_key
    if config.chat_provider == "groq":
        if not config.groq_api_key:
            raise SystemExit("CHAT_PROVIDER=groq requires BYD_GROQ_API_KEY (or GROQ_API_KEY).")
        os.environ["GROQ_API_KEY"] = config.groq_api_key
    if config.chat_provider == "google_genai":
        if not config.google_api_key:
            raise SystemExit("CHAT_PROVIDER=google_genai requires BYD_GOOGLE_API_KEY (or GOOGLE_API_KEY).")
        os.environ["GOOGLE_API_KEY"] = config.google_api_key


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
        clear_reply="Контекст очищен. Начнём заново 🌿",
        unsupported_voice_reply="Извините, не удалось распознать голосовое сообщение.",
        # Scheduled funnel touches / voucher / materials are recorded into the
        # thread under this label so the agent sees them on handback.
        outbound_note_template="[Клиенту автоматически отправлено сообщение: {text}]",
        mute_store=get_mute_store(),
        profile_store=get_profile_store(),
        # On every inbound message: cancel the no-answer drip if the lead replied.
        message_guard=byd_funnel_guard,
        # Mirror the AI's replies into the Bitrix deal timeline (no-op when the
        # integration or dialogue mirroring is off).
        outbound_observer=funnel.bitrix_mirror_reply,
        # User account: an operator answering by typing as this account = takeover
        # → auto-mute + capture (our own media/text sends are suppressed so they
        # don't false-trigger it).
        capture_operator_messages=True,
    )


def _operator_channel(manager_agent: Agent) -> TelegramBotChannel:
    channel = TelegramBotChannel(
        name="operator",
        agent=manager_agent,
        # Only clinic staff may DM the operator bot (and click its buttons).
        allowed_user_ids=config.manager_allowed_ids or None,
        bot_token=config.bot_token,
        api_id=config.api_id or 0,
        api_hash=config.api_hash,
        session=config.bot_session,
        voice=get_voice(),
        debounce_seconds=config.debounce_seconds,
        read_delay_seconds=config.read_delay_seconds,
        request_timeout=config.request_timeout,
        clear_reply="Контекст очищен.",
        # NO mute_store here on purpose: the AI-mute/handoff is a *customer*-side
        # concept. The operator bot is an internal staff tool and must always
        # answer allow-listed staff — even for a chat_id that is muted on the
        # customer channel (e.g. one person testing as both customer + operator,
        # or staff whose own chat is a muted customer). The unmute button still
        # works: its handler uses the mute-store singleton directly.
        profile_store=get_profile_store(),
    )
    _register_callbacks(channel)
    return channel


def _authorised(event) -> bool:
    sender = event.sender_id
    allowed = config.manager_allowed_ids | config.operator_chat_ids
    return not allowed or sender in allowed


def _register_callbacks(channel: TelegramBotChannel) -> None:
    """Wire every funnel inline button on the operator bot's client."""
    mute_store = get_mute_store()
    repo = get_repository()

    async def _on_call(event) -> None:
        if not _authorised(event):
            await event.answer("Недостаточно прав.", alert=True)
            return
        try:
            data = (event.data or b"").decode("utf-8", "ignore")
            # bydcall:reached:<lead_id> | bydcall:noanswer:<lead_id>
            _, outcome, lead_id = data.split(":", 2)
            reached = outcome == "reached"
            await funnel.record_call_outcome(int(lead_id), reached=reached)
            label = "📞 Дозвонился — переговоры" if reached else "📵 Не дозвонился — рассылка"
            try:
                await event.edit(buttons=[Button.inline(f"✅ {label}", b"noop")])
            except Exception:
                logger.debug("call button edit failed", exc_info=True)
            await event.answer("Зафиксировано.")
        except Exception:
            logger.exception("call-outcome callback failed")
            await event.answer("Ошибка.", alert=True)

    async def _on_paid(event) -> None:
        if not _authorised(event):
            await event.answer("Недостаточно прав.", alert=True)
            return
        try:
            data = (event.data or b"").decode("utf-8", "ignore")
            lead_id = int(data[len(PAID_CALLBACK_PREFIX):])
            ok, message = await funnel.mark_paid(lead_id)
            if ok:
                try:
                    await event.edit(buttons=[Button.inline("✅ Оплата отмечена", b"noop")])
                except Exception:
                    logger.debug("paid button edit failed", exc_info=True)
            await event.answer(message[:180])
        except Exception:
            logger.exception("paid callback failed")
            await event.answer("Ошибка.", alert=True)

    async def _on_task_done(event) -> None:
        if not _authorised(event):
            await event.answer("Недостаточно прав.", alert=True)
            return
        try:
            data = (event.data or b"").decode("utf-8", "ignore")
            task_id = int(data[len(TASK_DONE_CALLBACK_PREFIX):])
            # Through the funnel so the mirrored Bitrix task is completed too.
            await funnel.close_operator_task(task_id)
            try:
                await event.edit(buttons=[Button.inline("✅ Выполнено", b"noop")])
            except Exception:
                logger.debug("task button edit failed", exc_info=True)
            await event.answer("Задача закрыта.")
        except Exception:
            logger.exception("task-done callback failed")
            await event.answer("Ошибка.", alert=True)

    async def _on_material(event) -> None:
        if not _authorised(event):
            await event.answer("Недостаточно прав.", alert=True)
            return
        try:
            data = (event.data or b"").decode("utf-8", "ignore")
            # bydmat:<kind>:<lead_id>
            _, kind, lead_id = data.split(":", 2)
            ok, message = await funnel.send_material(int(lead_id), kind)
            await event.answer(message[:180], alert=not ok)
        except Exception:
            logger.exception("material callback failed")
            await event.answer("Ошибка.", alert=True)

    async def _on_unmute(event) -> None:
        if not _authorised(event):
            await event.answer("Недостаточно прав.", alert=True)
            return
        try:
            data = (event.data or b"").decode("utf-8", "ignore")
            chat_id = int(data[len(UNMUTE_CALLBACK_PREFIX):])
            was_muted = await mute_store.unmute(chat_id)
            try:
                await event.edit(buttons=[Button.inline("✅ ИИ подключён", b"noop")])
            except Exception:
                logger.debug("unmute button edit failed", exc_info=True)
            await event.answer("ИИ подключён." if was_muted else "ИИ уже был активен.")
        except Exception:
            logger.exception("unmute callback failed")
            await event.answer("Ошибка.", alert=True)

    channel.add_callback_handler(CALL_CALLBACK_PREFIX, _on_call)
    channel.add_callback_handler(PAID_CALLBACK_PREFIX, _on_paid)
    channel.add_callback_handler(TASK_DONE_CALLBACK_PREFIX, _on_task_done)
    channel.add_callback_handler(MATERIAL_CALLBACK_PREFIX, _on_material)
    channel.add_callback_handler(UNMUTE_CALLBACK_PREFIX, _on_unmute)


def build_tenant(
    *,
    include_userbot: bool = True,
    include_operator: bool = True,
    include_scheduler: bool = True,
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
            logger.warning("BYD_BOT_TOKEN not set — skipping the operator bot channel.")
        else:
            channels.append(_operator_channel(manager_agent))

    # Wire the funnel's runtime handles: the live customer channel (to message
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
    if include_scheduler:
        sync_jobs.append(DealScheduler(cfg=config))
        # The Bitrix pull job sends client messages through the funnel context,
        # so it lives with the userbot + scheduler.
        if config.bitrix_enabled:
            from apps.byd.bitrix_sync import BitrixFunnelSync

            sync_jobs.append(BitrixFunnelSync(cfg=config))
        else:
            logger.info("Bitrix24 integration off (set BYD_BITRIX_WEBHOOK_URL + BYD_BITRIX_CATEGORY_ID).")
    return Tenant(id="byd", agents=agents, channels=channels, sync_jobs=sync_jobs)


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


def run_userbot() -> None:
    # Userbot + scheduler: the scheduler messages clients through the userbot's
    # client, so they must share a process.
    _bootstrap()
    asyncio.run(_serve(include_userbot=True, include_operator=False, include_scheduler=True))


def run_operator() -> None:
    _bootstrap()
    asyncio.run(_serve(include_userbot=False, include_operator=True, include_scheduler=False))


if __name__ == "__main__":
    run()
