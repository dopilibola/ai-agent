"""Shared Telethon channel logic.

Both the user-account channel and the bot-token channel inherit from this:
both run a Telethon `TelegramClient`, both want debounce + voice + photo
download + multimodal user-content construction + typing indicators.

The only difference is how `start_client()` authenticates (user phone-session
vs bot token) and whether an allow-list filter is applied to senders.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from contextlib import AsyncExitStack, asynccontextmanager
from io import BytesIO
from typing import Awaitable, Callable, Iterable, Optional, Union

import httpx
from telethon import TelegramClient, events
from telethon.tl.functions.messages import ReadMessageContentsRequest

from core.agent import Agent
from core.channel import Channel, MessageGuard
from core.mute_store import MuteStore
from core.profile_store import ProfileStore
from voice.transcriber import VoiceTranscriber

logger = logging.getLogger(__name__)

MAX_ALBUM_SIZE = 10
_CT_TO_EXT = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
_FILE_CT_TO_EXT = {
    **_CT_TO_EXT,
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/webm": ".webm",
    "audio/mpeg": ".mp3",
    "audio/ogg": ".ogg",
    "application/pdf": ".pdf",
}

CLEAR_COMMANDS = {"/clear", "/reset"}

# Magic-byte sniffing for the data: URL we hand the model. Telegram photos are
# always JPEG, but an image sent "as a file" keeps its original format and the
# vision APIs reject a data URL whose mime doesn't match the bytes.
_IMAGE_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def _sniff_image_mime(data: bytes) -> str:
    for magic, mime in _IMAGE_MAGIC:
        if data.startswith(magic):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"

# Cap on per-chat retained inbound images (only when retain_images is on) — also
# the Telegram album limit, so a bouquet can't accumulate unbounded bytes.
MAX_RETAINED_IMAGES = 10

# Cap on per-chat remembered outgoing texts used to recognise our own sends and
# keep them out of the operator-handoff capture. Small: only a handful are ever
# in flight before their echo arrives and clears them.
MAX_SUPPRESSED_ECHOES = 32


class TelethonChannel(Channel):
    """Base for both Telethon-backed channels."""

    def __init__(
        self,
        *,
        name: str,
        agent: Agent,
        api_id: int,
        api_hash: str,
        session: str,
        voice: Optional[VoiceTranscriber] = None,
        allowed_user_ids: Optional[Iterable[int]] = None,
        parse_mode: str = "html",
        debounce_seconds: float = 5.0,
        read_delay_seconds: float = 3.0,
        request_timeout: int = 15,
        clear_reply: str = "Контекст очищен. Начнём заново 🌸",
        unsupported_voice_reply: str = "Sorry, I couldn't understand that voice message.",
        error_reply: str = "Sorry, something went wrong. Please try again.",
        mute_store: Optional[MuteStore] = None,
        profile_store: Optional[ProfileStore] = None,
        retain_images: bool = False,
        manager_agent: Optional[Agent] = None,
        manager_user_ids: Optional[Iterable[int]] = None,
        message_guard: Optional[MessageGuard] = None,
        capture_operator_messages: bool = False,
        operator_note_template: str = (
            "[A human operator replied to the customer while the AI was paused: {text}]"
        ),
        outbound_note_template: str = (
            "[An automated message was sent to the customer: {text}]"
        ),
        outbound_observer: Optional[Callable[[int, str], Awaitable[None]]] = None,
    ) -> None:
        super().__init__(
            name=name,
            agent=agent,
            mute_store=mute_store,
            profile_store=profile_store,
            message_guard=message_guard,
        )
        if not api_id or not api_hash:
            raise RuntimeError(
                f"Channel {name}: api_id and api_hash are required "
                "(get them from https://my.telegram.org)."
            )
        self._client = TelegramClient(session, int(api_id), api_hash)
        self._parse_mode = parse_mode
        self._voice = voice
        self._allowed = frozenset(allowed_user_ids) if allowed_user_ids else None
        self._debounce = debounce_seconds
        self._read_delay = read_delay_seconds
        self._timeout = request_timeout
        self._clear_reply = clear_reply
        self._unsupported_voice_reply = unsupported_voice_reply
        self._error_reply = error_reply

        # When set, inbound photo bytes are accumulated per chat across debounce
        # batches (so a tool fired on a later "save" message can still reach a
        # photo uploaded earlier) and published on `current_images`. Gated to
        # channels that need it (the merchant catalog bot) so the customer
        # userbot doesn't retain every photo it's ever shown.
        self._retain_images = retain_images
        self._pending_images: dict[int, list[bytes]] = {}

        # When set, the channel also watches *outgoing* messages: a human typing
        # as this account from another device is treated as an operator takeover.
        # The AI is auto-muted (the same handoff an order/booking triggers) and
        # the operator's messages are recorded into the conversation as labeled
        # notes, so the agent has the operator's side when control is handed back
        # (re-enable via the unmute button, the admin panel, or /clear). Only
        # makes sense on a user-account channel (a human can't type "as" a bot).
        self._capture_operator = capture_operator_messages
        self._operator_note_template = operator_note_template
        self._outbound_note_template = outbound_note_template

        # Optional async (chat_id, text) callback fired after the agent's reply
        # is successfully sent — lets a tenant observe the outbound side of the
        # dialogue (e.g. mirror it into a CRM) without touching the send path.
        # Best-effort: observer failures never affect the conversation.
        self._outbound_observer = outbound_observer
        # Texts this client is about to send, per chat. The outgoing watcher uses
        # them to tell our own programmatic sends (which also echo as outgoing
        # messages) apart from a human operator's. Registered *before* the send
        # is awaited, so the echo can never race ahead of the bookkeeping.
        self._suppressed_echoes: dict[int, list[str]] = {}
        # Same idea for media we send programmatically (photo albums, documents,
        # videos): a media echo carries no matchable text, so we count expected
        # outgoing media per chat and let the watcher consume them — otherwise a
        # tool/scheduler photo/voucher would look like an operator attaching a
        # file and wrongly auto-mute the chat. Bumped before each send.
        self._suppressed_media: dict[int, int] = {}

        # Optional second agent for allow-listed senders (e.g. clinic staff get a
        # management agent on the same bot patients book through). In a private
        # chat the chat_id IS the user id, so we route on it.
        self._manager_agent = manager_agent
        self._manager_user_ids = (
            frozenset(manager_user_ids) if manager_user_ids else frozenset()
        )

        # Last (name, username) persisted per chat this process run — lets us
        # skip a DB write (and the entity lookup) when nothing changed.
        self._known_profiles: dict[int, tuple[Optional[str], Optional[str]]] = {}

        self._buffers: dict[int, list[tuple[events.NewMessage.Event, str, Optional[bytes]]]] = {}
        self._read_timers: dict[int, asyncio.Task] = {}
        self._process_timers: dict[int, asyncio.Task] = {}
        self._typing_stacks: dict[int, AsyncExitStack] = {}
        # One lock per chat, serialising every write to that chat's conversation
        # thread — a customer-turn dispatch (which runs the model) and an
        # operator-handoff record must not interleave, or a concurrent
        # checkpoint write clobbers the other and a message is lost.
        self._thread_locks: dict[int, asyncio.Lock] = {}
        # Registered before run(); attached to the Telethon client inside run()
        # after _start_client() so the bot session is authenticated first.
        self._callback_handlers: list[
            tuple[bytes, Callable[[events.CallbackQuery.Event], Awaitable[None]]]
        ] = []

    # ----- subclass hooks ----------------------------------------------

    async def _start_client(self) -> None:
        """Authenticate the Telethon client. Subclasses override."""
        await self._client.start()

    def _is_authorised(self, sender_id: int) -> bool:
        if self._allowed is None:
            return True
        return sender_id in self._allowed

    # ----- Channel interface -------------------------------------------

    async def run(self) -> None:
        await self._start_client()
        self._client.parse_mode = self._parse_mode
        me = await self._client.get_me()
        logger.info(
            "Channel %s started as %s (id=%s)",
            self.name,
            getattr(me, "username", None) or getattr(me, "first_name", "?"),
            getattr(me, "id", "?"),
        )
        self._register_handlers()
        self._register_callback_handlers()
        await self._client.run_until_disconnected()

    def add_callback_handler(
        self,
        callback_data_prefix: str,
        handler: Callable[[events.CallbackQuery.Event], Awaitable[None]],
    ) -> None:
        """Register a CallbackQuery handler for inline-button clicks.

        Must be called BEFORE Runtime.run() — handlers are attached to the
        Telethon client inside run() after authentication.

        callback_data is compared as a bytes prefix; matching events are
        routed to `handler(event)`.
        """
        self._callback_handlers.append(
            (callback_data_prefix.encode("utf-8"), handler)
        )

    def _register_callback_handlers(self) -> None:
        for prefix, handler in self._callback_handlers:
            self._client.add_event_handler(
                handler, events.CallbackQuery(data=lambda d, p=prefix: d.startswith(p))
            )

    async def send_text(self, chat_id: int, text: str) -> None:
        self._suppress_echo(chat_id, text)
        await self._client.send_message(chat_id, text)

    async def record_outbound(self, chat_id: int, text: str) -> None:
        """Append an out-of-band send (scheduled funnel touch, voucher, pushed
        material) to the chat's agent thread as a labeled note, so the agent has
        the full picture on handback. Held under the same per-chat lock as a
        customer-turn dispatch and an operator-handoff record, so it can't clobber
        a concurrent checkpoint write."""
        text = (text or "").strip()
        if not text:
            return
        agent = self._agent_for(chat_id)
        note = self._outbound_note_template.replace("{text}", text)
        async with self._lock_for(chat_id):
            try:
                await agent.record_user_message(
                    note, thread_id=self.thread_id(chat_id, agent)
                )
            except Exception:
                logger.exception("Failed to record outbound message for %s", chat_id)

    # ----- operator-handoff capture (echo bookkeeping) -----------------

    def _suppress_echo(self, chat_id: int, text: Optional[str]) -> None:
        """Remember a message this client is about to send, so the outgoing
        watcher doesn't mistake its echo for an operator's message. No-op unless
        operator capture is on (only those channels watch outgoing messages)."""
        if not self._capture_operator:
            return
        text = (text or "").strip()
        if not text:
            return
        bucket = self._suppressed_echoes.setdefault(chat_id, [])
        bucket.append(text)
        if len(bucket) > MAX_SUPPRESSED_ECHOES:
            del bucket[:-MAX_SUPPRESSED_ECHOES]

    def _consume_suppressed_echo(self, chat_id: int, text: str) -> bool:
        """True if `text` matches one of our own pending sends (and consumes it)."""
        bucket = self._suppressed_echoes.get(chat_id)
        if not bucket or not text:
            return False
        try:
            bucket.remove(text)
        except ValueError:
            return False
        if not bucket:
            self._suppressed_echoes.pop(chat_id, None)
        return True

    def _suppress_media_echo(self, chat_id: int, count: int = 1) -> None:
        """Expect `count` outgoing media messages from our own send (so the
        outgoing watcher doesn't read them as an operator attaching a file).
        No-op unless operator capture is on."""
        if not self._capture_operator or count <= 0:
            return
        self._suppressed_media[chat_id] = self._suppressed_media.get(chat_id, 0) + count

    def _consume_suppressed_media(self, chat_id: int) -> bool:
        """True if a media message is one of our own pending sends (consumes one)."""
        remaining = self._suppressed_media.get(chat_id, 0)
        if remaining <= 0:
            return False
        remaining -= 1
        if remaining:
            self._suppressed_media[chat_id] = remaining
        else:
            self._suppressed_media.pop(chat_id, None)
        return True

    async def send_photos(self, chat_id: int, urls: list[str]) -> None:
        if not urls:
            return
        downloads = await asyncio.gather(
            *(self._download_url(u) for u in urls[:MAX_ALBUM_SIZE]),
            return_exceptions=True,
        )
        files = [f for f in downloads if isinstance(f, BytesIO)]
        for url, result in zip(urls[:MAX_ALBUM_SIZE], downloads):
            if isinstance(result, Exception):
                logger.warning("Failed to fetch %s: %s", url, result)
        if not files:
            return
        self._suppress_media_echo(chat_id, len(files))
        async with self._client.action(chat_id, "photo"):
            await self._client.send_file(chat_id, files, force_document=False)

    async def send_document(
        self, chat_id: int, data: bytes, *, filename: str, caption: Optional[str] = None
    ) -> None:
        """Send an in-memory file (bytes) as a Telegram document (e.g. the
        Stage-6 PDF voucher)."""
        buf = BytesIO(data)
        buf.name = filename
        self._suppress_media_echo(chat_id, 1)
        async with self._client.action(chat_id, "document"):
            await self._client.send_file(
                chat_id, buf, caption=caption, force_document=True
            )

    async def send_file_url(
        self, chat_id: int, url: str, *, caption: Optional[str] = None
    ) -> None:
        """Download a URL (image/video/document) and send it. Videos are sent
        playable (force_document=False); Telegram infers the type from the
        filename/MIME."""
        try:
            file = await self._download_file(url)
        except Exception:
            logger.warning("Failed to fetch file %s", url, exc_info=True)
            return
        self._suppress_media_echo(chat_id, 1)
        async with self._client.action(chat_id, "document"):
            await self._client.send_file(
                chat_id, file, caption=caption, force_document=False
            )

    @asynccontextmanager
    async def typing(self, chat_id: int):
        await self._start_typing(chat_id)
        try:
            yield
        finally:
            await self._stop_typing(chat_id)

    async def get_chat_info(self, chat_id: int) -> dict:
        try:
            entity = await self._client.get_entity(chat_id)
        except Exception:
            logger.exception("Failed to fetch entity for %s", chat_id)
            return {"name": str(chat_id), "username": None}
        parts = [getattr(entity, "first_name", None), getattr(entity, "last_name", None)]
        name = (
            " ".join(p for p in parts if p)
            or getattr(entity, "title", None)
            or str(chat_id)
        )
        return {
            "name": name,
            "username": getattr(entity, "username", None),
        }

    async def _capture_profile(
        self, event: events.NewMessage.Event, chat_id: int
    ) -> None:
        """Persist the sender's display name/@username so the admin panel can
        label this chat. Best-effort: the sender entity rides along with an
        incoming private message (no extra Telegram round-trip), and we only
        write when the name changed since the last message this run."""
        if self.profile_store is None:
            return
        try:
            sender = await event.get_sender()
            if sender is None:
                return
            parts = [getattr(sender, "first_name", None), getattr(sender, "last_name", None)]
            name = (
                " ".join(p for p in parts if p)
                or getattr(sender, "title", None)
                or None
            )
            username = getattr(sender, "username", None)
            if self._known_profiles.get(chat_id) == (name, username):
                return
            await self.profile_store.upsert(chat_id, name=name, username=username)
            self._known_profiles[chat_id] = (name, username)
        except Exception:
            logger.debug("Failed to capture profile for %s", chat_id, exc_info=True)

    # ----- typing helpers (also callable from tools) -------------------

    async def _start_typing(self, chat_id: int) -> None:
        if chat_id in self._typing_stacks:
            return
        stack = AsyncExitStack()
        await stack.__aenter__()
        try:
            await stack.enter_async_context(self._client.action(chat_id, "typing"))
        except Exception:
            await stack.aclose()
            raise
        self._typing_stacks[chat_id] = stack

    async def _stop_typing(self, chat_id: int) -> None:
        stack = self._typing_stacks.pop(chat_id, None)
        if stack is None:
            return
        try:
            await stack.aclose()
        except Exception:
            logger.exception("Failed to stop typing for %s", chat_id)

    async def start_typing(self, chat_id: int) -> None:
        """Public so tools can manually toggle the indicator."""
        await self._start_typing(chat_id)

    async def stop_typing(self, chat_id: int) -> None:
        await self._stop_typing(chat_id)

    # ----- HTTP download -----------------------------------------------

    async def _download_url(self, url: str) -> BytesIO:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            ct = resp.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            ext = _CT_TO_EXT.get(ct, ".jpg")
            buf = BytesIO(resp.content)
            buf.name = f"photo{ext}"
            return buf

    async def _download_file(self, url: str) -> BytesIO:
        """Download any URL, preserving a sensible filename (from the URL path or
        the content-type) so Telegram renders video/pdf/etc correctly — unlike
        `_download_url`, which always names the result `photo.*`."""
        from urllib.parse import unquote, urlparse

        async with httpx.AsyncClient(
            timeout=self._timeout, follow_redirects=True
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            name = unquote(urlparse(url).path.rsplit("/", 1)[-1]) or "file"
            if "." not in name:
                ct = resp.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                name += _FILE_CT_TO_EXT.get(ct, ".bin")
            buf = BytesIO(resp.content)
            buf.name = name
            return buf

    # ----- inbound -----------------------------------------------------

    def _register_handlers(self) -> None:
        @self._client.on(events.NewMessage(incoming=True))
        async def on_message(event: events.NewMessage.Event) -> None:
            # Reply ONLY in 1:1 DMs with a human. `is_private` already excludes
            # groups, channels, and channel-comment discussion threads; the
            # sender check then drops bot accounts (incl. our own bot/userbot)
            # so the agent never talks to another bot.
            if not event.is_private:
                return
            if not await self._is_human_sender(event):
                return
            sender_id = event.sender_id
            if not self._is_authorised(sender_id):
                logger.warning(
                    "Channel %s rejected message from unauthorised user %s",
                    self.name,
                    sender_id,
                )
                return
            text = (event.raw_text or "").strip()
            if text.lower() in CLEAR_COMMANDS:
                await self._clear_context(event)
                return
            image: Optional[bytes] = None
            if event.message.photo or self._is_image_file(event.message):
                image = await self._download_photo(event)
            elif not text and (event.message.voice or event.message.audio):
                if self._voice is None:
                    return
                text = await self._transcribe_voice(event)
            if not text and not image:
                return
            # If the user replied to / quoted an earlier message, fold that
            # quoted text in so the agent knows what they're pointing at.
            quoted = await self._reply_context(event)
            if quoted:
                text = f"{quoted}\n{text}".strip()
            await self._enqueue(event, text, image)

        if not self._capture_operator:
            return

        @self._client.on(events.NewMessage(outgoing=True))
        async def on_outgoing(event: events.NewMessage.Event) -> None:
            await self._handle_outgoing(event)

    async def _handle_outgoing(self, event: events.NewMessage.Event) -> None:
        """Handle a human operator typing as this account (from another device).

        A human reply means the operator is taking over, so — exactly like an
        order/booking handoff — the AI is auto-muted and goes passive. The
        message is also recorded into the conversation as a *labeled note* (not
        the agent's own words), so the agent has the operator's side when control
        is handed back. Only active on channels with capture_operator_messages
        =True (the client-facing user account).
        """
        if not event.is_private or self.mute_store is None:
            return
        chat_id = event.chat_id
        text = (event.raw_text or "").strip()
        msg = getattr(event, "message", None)
        has_media = bool(
            msg and (msg.photo or msg.document or msg.video or msg.voice or msg.audio)
        )
        # Our own programmatic sends echo back as outgoing messages too; drop them
        # first — otherwise we'd mute the chat on the agent's own reply or on a
        # tool/scheduler photo/voucher. Media is matched by the per-chat counter
        # (its echo has no matchable text); plain text by the suppressed-text set.
        # Registered before each send is awaited, so an echo can't race ahead.
        # (No await before this, so it can't race a concurrent send.)
        if has_media:
            if self._consume_suppressed_media(chat_id):
                return
        elif self._consume_suppressed_echo(chat_id, text):
            return
        note = self._format_operator_note(text, event)
        # Hold the chat lock across mute + flush + record, the same lock a
        # customer-turn dispatch holds. So if the agent is mid-reply we wait for
        # it to finish (then mute), instead of racing its checkpoint write and
        # losing this message.
        async with self._lock_for(chat_id):
            # A human typed here → operator takeover. Mute the AI if it isn't
            # already (an order/booking may have muted it first) so it stops
            # replying.
            if not await self._is_muted(chat_id):
                try:
                    await self.mute_store.mute(chat_id)
                    logger.info(
                        "Channel %s auto-muted chat %s on operator takeover",
                        self.name, chat_id,
                    )
                except Exception:
                    logger.exception(
                        "Failed to auto-mute chat %s on takeover", chat_id
                    )
            # Preserve order: record any debounced-but-unprocessed customer
            # messages before the operator reply that answers them. The chat is
            # muted now, so `_process_items` records them without speaking.
            # Whichever of us pops the buffer first wins (pop is atomic); a
            # pending debounce timer that fires later just sees it empty.
            items = self._buffers.pop(chat_id, [])
            if items:
                await self._process_items(chat_id, items)
            if note is None:
                return
            agent = self._agent_for(chat_id)
            try:
                await agent.record_user_message(
                    note, thread_id=self.thread_id(chat_id, agent)
                )
                logger.info("Recorded operator handoff message for %s", chat_id)
            except Exception:
                logger.exception("Failed to record operator message for %s", chat_id)

    def _format_operator_note(
        self, text: str, event: events.NewMessage.Event
    ) -> Optional[str]:
        """Wrap the operator's message in the labeled-note template, or None to
        skip (e.g. a sticker/service message with nothing meaningful to record).
        Uses str.replace, not str.format, so braces in the text can't blow up."""
        if not text:
            msg = getattr(event, "message", None)
            if msg is not None and (
                msg.photo or msg.document or msg.voice or msg.audio
            ):
                text = "[attachment]"
            else:
                return None
        return self._operator_note_template.replace("{text}", text)

    async def _is_human_sender(self, event: events.NewMessage.Event) -> bool:
        """True only when the message comes from a human user.

        Drops messages from bot accounts and from Telegram's own service
        notifications (id 777000). The sender entity rides along with an
        incoming private message, so this is usually free; if the lookup
        fails we err on the side of NOT replying.
        """
        try:
            sender = await event.get_sender()
        except Exception:
            logger.debug("Failed to resolve sender for %s", event.chat_id, exc_info=True)
            return False
        if sender is None:
            return False
        if getattr(sender, "bot", False) or getattr(sender, "support", False):
            return False
        if getattr(sender, "id", None) == 777000:  # Telegram service notifications
            return False
        return True

    async def _reply_context(self, event: events.NewMessage.Event) -> Optional[str]:
        """When the incoming message replies to / quotes another message, return a
        short labeled note of the quoted text so the agent knows what the user is
        referring to (Telegram replies otherwise arrive as bare text, dropping
        the referent). Prefers the highlighted fragment (Telegram's partial
        quote) over the whole message. Best-effort: a deleted target or a failed
        lookup just yields None.
        """
        msg = getattr(event, "message", None)
        if msg is None or not getattr(msg, "is_reply", False):
            return None
        try:
            replied = await msg.get_reply_message()
        except Exception:
            logger.debug("Failed to fetch reply target for %s", event.chat_id, exc_info=True)
            return None
        if replied is None:
            return None
        reply_to = getattr(msg, "reply_to", None)
        quote = getattr(reply_to, "quote_text", None) or (replied.raw_text or "")
        quote = " ".join(quote.split()).strip()
        if not quote:
            return None
        if len(quote) > 300:
            quote = quote[:300].rstrip() + "…"
        whose = "your earlier message" if getattr(replied, "out", False) else "an earlier message"
        return f'[The user is replying to {whose}: "{quote}"]'

    async def _clear_context(self, event: events.NewMessage.Event) -> None:
        chat_id = event.chat_id
        for table in (self._read_timers, self._process_timers):
            task = table.pop(chat_id, None)
            if task and not task.done():
                task.cancel()
        self._buffers.pop(chat_id, None)
        self._pending_images.pop(chat_id, None)
        await self._stop_typing(chat_id)
        # Clear the thread of the agent that actually serves this sender (manager
        # vs booking), matching the namespaced key used in dispatch.
        agent = self._agent_for(chat_id)
        try:
            await agent.clear_thread(self.thread_id(chat_id, agent))
        except Exception:
            logger.exception("Failed to clear agent thread for %s", chat_id)
        try:
            await agent.clear_tokens(chat_id)
        except Exception:
            logger.exception("Failed to clear token usage for %s", chat_id)
        # A full reset also lifts an operator-handoff mute, so /clear genuinely
        # starts the chat over instead of leaving the bot silently muted after a
        # prior booking (and lets QA reset between booking scenarios).
        if self.mute_store is not None:
            try:
                await self.mute_store.unmute(chat_id)
            except Exception:
                logger.exception("Failed to unmute on /clear for %s", chat_id)
        try:
            await event.mark_read()
        except Exception:
            logger.exception("Failed to mark /clear read for %s", chat_id)
        await event.respond(self._clear_reply)

    def clear_pending_images(self, chat_id: int) -> None:
        """Drop accumulated inbound photo bytes for a chat — called once a tool
        has consumed them (e.g. after a bouquet is saved)."""
        self._pending_images.pop(chat_id, None)

    def _agent_for(self, chat_id: int):
        """Pick the agent for a sender — the manager agent for allow-listed
        staff, otherwise the channel's default (booking) agent."""
        if self._manager_agent is not None and chat_id in self._manager_user_ids:
            return self._manager_agent
        return self.agent

    @staticmethod
    def _is_image_file(msg) -> bool:
        """True for a picture sent uncompressed ("send as file") — Telegram
        delivers it as a document, so `msg.photo` is None. People do this for
        scans and ID photos, exactly the ones the agent most needs to read.
        Stickers and GIFs are image documents too; they aren't content."""
        if msg is None or msg.photo or msg.sticker or msg.gif:
            return False
        doc = msg.document
        if doc is None:
            return False
        return (doc.mime_type or "").lower() in _CT_TO_EXT

    async def _download_photo(self, event: events.NewMessage.Event) -> Optional[bytes]:
        try:
            buf = BytesIO()
            await event.message.download_media(file=buf)
            return buf.getvalue()
        except Exception:
            logger.exception("Failed to download photo from %s", event.chat_id)
            return None

    async def _transcribe_voice(self, event: events.NewMessage.Event) -> str:
        try:
            buf = BytesIO()
            await event.message.download_media(file=buf)
            buf.seek(0)
            text = await self._voice.transcribe(buf, filename="voice.ogg")
            logger.info("Transcribed voice from %s: %s", event.chat_id, text)
            return text
        except Exception:
            logger.exception("Failed to transcribe voice from %s", event.chat_id)
            try:
                await event.respond(self._unsupported_voice_reply)
            except Exception:
                logger.exception("Failed to send voice-error reply to %s", event.chat_id)
            return ""

    async def _mark_voice_consumed(
        self,
        chat_id: int,
        items: list[tuple[events.NewMessage.Event, str, Optional[bytes]]],
    ) -> None:
        """Clear the "unplayed" indicator on voice/audio messages in this batch.

        Best-effort like mark_read: bot accounts can't do this, and a failure
        must never abort the dispatch.
        """
        ids = [
            ev.message.id
            for ev, _, _ in items
            if ev.message and (ev.message.voice or ev.message.audio)
        ]
        if not ids:
            return
        try:
            await self._client(ReadMessageContentsRequest(id=ids))
        except Exception:
            logger.debug("readMessageContents failed for %s", chat_id, exc_info=True)

    async def _enqueue(
        self,
        event: events.NewMessage.Event,
        text: str,
        image: Optional[bytes] = None,
    ) -> None:
        chat_id = event.chat_id
        preview = text if text else f"<photo {len(image) if image else 0}B>"
        logger.info("Buffered from %s on %s: %s", chat_id, self.name, preview)
        self._buffers.setdefault(chat_id, []).append((event, text, image))
        self._reschedule(self._read_timers, chat_id, self._read_delay, self._delayed_read)
        self._reschedule(
            self._process_timers, chat_id, self._debounce, self._delayed_process
        )

    def _reschedule(
        self,
        table: dict[int, asyncio.Task],
        chat_id: int,
        delay: float,
        action,
    ) -> None:
        existing = table.get(chat_id)
        if existing and not existing.done():
            existing.cancel()
        table[chat_id] = asyncio.create_task(self._run_after(delay, action, chat_id))

    @staticmethod
    async def _run_after(delay: float, action, chat_id: int) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        await action(chat_id)

    async def _is_muted(self, chat_id: int) -> bool:
        return self.mute_store is not None and await self.mute_store.is_muted(chat_id)

    async def _delayed_read(self, chat_id: int) -> None:
        items = self._buffers.get(chat_id) or []
        self._read_timers.pop(chat_id, None)
        if not items:
            return
        # A human operator has taken over this chat. Stay passive: don't send
        # a read receipt, otherwise the customer sees "seen" with no reply and
        # feels ignored. The operator reads/answers from the same account.
        if await self._is_muted(chat_id):
            return
        try:
            await items[-1][0].mark_read()
        except Exception:
            logger.exception("Failed to mark read for %s", chat_id)

    def _lock_for(self, chat_id: int) -> asyncio.Lock:
        """Per-chat lock serialising all writes to that chat's thread."""
        lock = self._thread_locks.get(chat_id)
        if lock is None:
            lock = asyncio.Lock()
            self._thread_locks[chat_id] = lock
        return lock

    async def _delayed_process(self, chat_id: int) -> None:
        items = self._buffers.pop(chat_id, [])
        self._process_timers.pop(chat_id, None)
        if not items:
            return
        # Hold the chat lock across the whole turn (incl. the model call) so an
        # operator-handoff record can't write the thread concurrently.
        async with self._lock_for(chat_id):
            await self._process_items(chat_id, items)

    async def _process_items(
        self,
        chat_id: int,
        items: list[tuple[events.NewMessage.Event, str, Optional[bytes]]],
    ) -> None:
        latest_event = items[-1][0]
        texts = [t for _, t, _ in items if t]
        images = [img for _, _, img in items if img]
        joined = "\n".join(texts)

        # Operator has taken over (e.g. order placed). Stay fully passive — no
        # read receipt, no typing/online presence, no reply. Touching the chat
        # here is what made the account look online and "ignore" the customer.
        # EXCEPTION: a deterministic guard (emergency triage) may still speak
        # over the handoff — a medical emergency must never be silenced by the
        # booking mute. We don't unmute or run the agent; only send the safety
        # message so the operator still owns the conversation.
        if await self._is_muted(chat_id):
            agent = self._agent_for(chat_id)
            # The agent stays silent during the handoff, but still record the
            # customer's messages into the conversation thread — otherwise
            # everything said while muted is invisible to the agent once an
            # operator hands control back (clicks "Подключить ИИ") and the chat
            # resumes from the pre-handoff checkpoint.
            user_content = self._build_user_content(joined, images)
            try:
                await agent.record_user_message(
                    user_content, thread_id=self.thread_id(chat_id, agent)
                )
            except Exception:
                logger.exception(
                    "Failed to record muted-chat message for %s", chat_id
                )
            guard_reply = await self.maybe_guard_reply(chat_id, joined, agent)
            if guard_reply:
                logger.info(
                    "Channel %s guard replied over muted chat %s",
                    self.name, chat_id,
                )
                try:
                    self._suppress_echo(chat_id, guard_reply)
                    await latest_event.respond(guard_reply)
                except Exception:
                    logger.exception("Failed to send guard reply to %s", chat_id)
                else:
                    await self._observe_outbound(chat_id, guard_reply)
            else:
                logger.info(
                    "Channel %s passive for muted chat %s — left for operator",
                    self.name, chat_id,
                )
            return

        await self._capture_profile(latest_event, chat_id)

        logger.info(
            "Processing %d msg(s) from %s on %s (texts=%d, images=%d)",
            len(items), chat_id, self.name, len(texts), len(images),
        )

        user_content = self._build_user_content(joined, images)

        # Accumulate inbound photos across batches so a tool fired on a later
        # message (e.g. "save it") can still reach a photo uploaded earlier.
        dispatch_images: Optional[list[bytes]] = images
        if self._retain_images:
            if images:
                buf = self._pending_images.setdefault(chat_id, [])
                buf.extend(images)
                if len(buf) > MAX_RETAINED_IMAGES:
                    del buf[:-MAX_RETAINED_IMAGES]
            dispatch_images = self._pending_images.get(chat_id, [])

        # mark_read isn't permitted on bot accounts (Telegram raises
        # BotMethodInvalidError). It's purely cosmetic, so a failure here must
        # not abort the dispatch and trigger the "something went wrong" reply.
        try:
            await latest_event.mark_read()
        except Exception:
            logger.debug("mark_read failed for %s", chat_id, exc_info=True)

        # mark_read clears the unread badge but NOT the "unplayed" dot on voice/
        # audio messages — that's a separate content-consumption flag cleared
        # only by readMessageContents. Without this, a transcribed voice note
        # still shows as unheard to the customer.
        await self._mark_voice_consumed(chat_id, items)

        try:
            await self._start_typing(chat_id)
            try:
                reply = await self.dispatch(
                    chat_id,
                    user_content,
                    images=dispatch_images,
                    agent=self._agent_for(chat_id),
                )
            finally:
                await self._stop_typing(chat_id)
        except Exception:
            logger.exception("Failed to handle messages from %s", chat_id)
            await self._stop_typing(chat_id)
            reply = self._error_reply

        if reply:
            try:
                self._suppress_echo(chat_id, reply)
                await latest_event.respond(reply)
            except Exception:
                logger.exception("Failed to send reply to %s", chat_id)
            else:
                await self._observe_outbound(chat_id, reply)

    async def _observe_outbound(self, chat_id: int, text: str) -> None:
        if self._outbound_observer is None:
            return
        try:
            await self._outbound_observer(int(chat_id), text)
        except Exception:
            logger.debug("outbound_observer failed for %s", chat_id, exc_info=True)

    @staticmethod
    def _build_user_content(
        text: str, images: list[bytes]
    ) -> Union[str, list[dict]]:
        if not images:
            return text
        parts: list[dict] = [{"type": "text", "text": text or "(user sent photos)"}]
        for img in images:
            b64 = base64.b64encode(img).decode("ascii")
            mime = _sniff_image_mime(img)
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}"},
                }
            )
        return parts
