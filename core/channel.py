"""Channel — abstract interface every transport (Telegram bot, userbot, future
WhatsApp/web) implements.

A channel is bound to exactly one Agent. The runtime concurrently runs every
channel; each channel decides how to ingest messages, batch them, transcribe
voice, attach photos, then calls `dispatch()` to drive the agent.
"""

from __future__ import annotations

import inspect
import logging
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from typing import Any, Awaitable, Callable, Optional, Union

from core.agent import Agent
from core.context import (
    current_channel,
    current_chat_id,
    current_images,
    current_tenant_id,
)
from core.mute_store import MuteStore
from core.profile_store import ProfileStore

logger = logging.getLogger(__name__)

# A pre-dispatch guard: given (chat_id, content), return a reply string to send
# *instead of* invoking the agent, or None/"" to let the agent run. Sync or
# async. Used for deterministic, code-side interceptions that must not depend on
# the LLM (e.g. anfa's emergency triage).
MessageGuard = Callable[[int, Any], Union[Optional[str], Awaitable[Optional[str]]]]


class Channel(ABC):
    """Base class for all I/O transports.

    Subclasses implement `run()` (the long-running event loop), `send_text()`,
    `send_photos()`, `typing()`, and `get_chat_info()`. The base provides
    `dispatch()` which sets context vars and invokes the agent.
    """

    name: str
    tenant_id: Optional[str]
    agent: Agent
    mute_store: Optional[MuteStore]
    profile_store: Optional[ProfileStore]
    message_guard: Optional[MessageGuard]

    def __init__(
        self,
        *,
        name: str,
        agent: Agent,
        mute_store: Optional[MuteStore] = None,
        profile_store: Optional[ProfileStore] = None,
        message_guard: Optional[MessageGuard] = None,
    ) -> None:
        self.name = name
        self.agent = agent
        self.mute_store = mute_store
        self.profile_store = profile_store
        self.message_guard = message_guard
        self.tenant_id = None  # set by Runtime when the channel is mounted

    # ----- lifecycle ----------------------------------------------------

    @abstractmethod
    async def run(self) -> None:
        """Start the transport and block until shutdown."""

    # ----- outbound -----------------------------------------------------

    @abstractmethod
    async def send_text(self, chat_id: int, text: str) -> None: ...

    async def send_photos(self, chat_id: int, urls: list[str]) -> None:
        """Send up to 10 photos. Default: no-op (text-only channels)."""
        logger.debug("Channel %s does not support send_photos", self.name)

    async def send_document(
        self, chat_id: int, data: bytes, *, filename: str, caption: Optional[str] = None
    ) -> None:
        """Send an in-memory file (bytes) as a document (e.g. a PDF voucher).
        Default: no-op (overridden by transports that support file uploads)."""
        logger.debug("Channel %s does not support send_document", self.name)

    async def send_file_url(
        self, chat_id: int, url: str, *, caption: Optional[str] = None
    ) -> None:
        """Download a URL (photo/video/document) and send it. Default: no-op."""
        logger.debug("Channel %s does not support send_file_url", self.name)

    async def compose_outbound(self, chat_id: int, directive: str) -> str:
        """Generate a proactive client message through this channel's agent, in the
        customer's language/tone (see `Agent.compose`). Does NOT send — the caller
        sends + records. Returns '' on failure so callers can fall back to a fixed
        template. `chat_id` is published so the composition's tokens are billed to
        the right chat."""
        token = current_chat_id.set(chat_id)
        try:
            return await self.agent.compose(directive, thread_id=self.thread_id(chat_id))
        except Exception:
            logger.debug("compose_outbound failed on %s", self.name, exc_info=True)
            return ""
        finally:
            current_chat_id.reset(token)

    async def record_outbound(self, chat_id: int, text: str) -> None:
        """Record a message we sent to the customer *out of band* (a scheduled
        funnel touch, a voucher, a pushed material) into the agent's conversation
        thread, so its history reflects everything the customer received — not
        just the agent's own replies. Default: no-op (overridden by transports
        that own an agent thread)."""

    @asynccontextmanager
    async def typing(self, chat_id: int):
        """Show a typing indicator for the duration of the block.

        Default: no-op. Override on channels that support it.
        """
        yield

    async def get_chat_info(self, chat_id: int) -> dict:
        """Return basic profile info: {"name": str, "username": str | None}."""
        return {"name": str(chat_id), "username": None}

    def clear_pending_images(self, chat_id: int) -> None:
        """Drop any accumulated inbound image bytes for a chat. Default: no-op
        (only channels that retain images override this — see TelethonChannel)."""

    # ----- dispatch -----------------------------------------------------

    def thread_id(self, chat_id: int, agent: Optional[Agent] = None) -> str:
        """Conversation key used by the checkpointer.

        When a non-default agent serves this chat (e.g. anfa's manager agent on
        the same bot that also serves the booking agent), its thread is
        namespaced by agent name so the two roles never share conversation
        history on a single account. The channel's primary agent keeps the
        bare `tenant:channel:chat_id` key (no migration of existing threads).
        """
        tenant = self.tenant_id or "default"
        if agent is not None and agent is not self.agent:
            return f"{tenant}:{self.name}:{agent.name}:{chat_id}"
        return f"{tenant}:{self.name}:{chat_id}"

    async def maybe_guard_reply(
        self, chat_id: int, content: Any, agent: Optional[Agent] = None
    ) -> Optional[str]:
        """Run the deterministic pre-LLM guard (if any) for this chat.

        Returns a reply string to send *instead of* invoking the agent, or None
        to proceed normally. Only the channel's primary (patient-facing) agent is
        guarded — an overridden agent (e.g. a manager/admin role on the same
        transport) is not a patient path. The guard may be sync or async and must
        be self-contained (it runs before any context vars are published).

        Exposed (not inlined into dispatch) so a transport can also consult it on
        a *muted* chat — an emergency-triage guard must speak over the operator
        handoff even when normal dispatch is suppressed.
        """
        effective_agent = agent or self.agent
        if self.message_guard is None or effective_agent is not self.agent:
            return None
        result = self.message_guard(chat_id, content)
        if inspect.isawaitable(result):
            result = await result
        return result or None

    async def dispatch(
        self,
        chat_id: int,
        content: Any,
        *,
        images: Optional[Any] = None,
        agent: Optional[Agent] = None,
    ) -> str:
        """Invoke an agent with channel + chat context published.

        `agent` overrides the channel's default agent for this turn — used to
        route some senders to a different agent on the same transport (e.g.
        anfa's bot serves patients the booking agent and allow-listed staff the
        manager agent). `images` is the raw bytes of the turn's inbound photos
        (if the channel retains them); they're published on `current_images` so
        a tool can re-use the uploaded image. Returns "" without invoking the
        agent when the chat is muted — the caller treats an empty reply as "no
        message to send", so the customer sees silence once a human operator has
        taken over.
        """
        if self.mute_store is not None and await self.mute_store.is_muted(chat_id):
            logger.info(
                "Channel %s skipped dispatch for muted chat %s", self.name, chat_id
            )
            return ""
        effective_agent = agent or self.agent
        # Deterministic pre-LLM guard (e.g. anfa emergency triage). A non-empty
        # return is sent verbatim and the agent is skipped for this turn.
        guarded = await self.maybe_guard_reply(chat_id, content, effective_agent)
        if guarded:
            logger.info(
                "Channel %s message_guard short-circuited chat %s",
                self.name, chat_id,
            )
            return guarded
        tenant_token = current_tenant_id.set(self.tenant_id)
        chat_token = current_chat_id.set(chat_id)
        channel_token = current_channel.set(self)
        images_token = current_images.set(tuple(images or ()))
        try:
            return await effective_agent.invoke(
                content,
                thread_id=self.thread_id(chat_id, effective_agent),
            )
        finally:
            current_images.reset(images_token)
            current_channel.reset(channel_token)
            current_chat_id.reset(chat_token)
            current_tenant_id.reset(tenant_token)
