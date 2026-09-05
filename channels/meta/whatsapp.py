"""WhatsApp Cloud API as a `Channel`.

Everything above this class — the agent, the funnel, the mute gate, the corpus,
the outreach pacing — is transport-agnostic, so this file only has to do four
things: turn an inbound webhook payload into the same content shape the Telegram
channel produces, send text back, remember who the sender is, and be honest
about the one rule WhatsApp has that Telegram does not.

**The 24-hour window.** Outside 24 hours from the customer's last message, Meta
refuses free-form text; only a pre-approved template may be sent. `send_text`
therefore reports that refusal as exactly what it is (error 131047) instead of a
generic failure, because a scheduled touch silently failing is the failure mode
this whole subsystem exists to avoid.

**The chat id is the phone number.** WhatsApp identifies a person by `wa_id` —
digits, no `+` — which fits in the `BigInteger` every table here already uses
for `chat_id`. So no id mapping table, and the panel keys on it unchanged.

**Debounce, like Telegram.** People send three short messages, not one long one.
Inbound messages are collected per chat and dispatched once the sender pauses,
so the agent answers the thought rather than its first fragment.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any, Optional

import httpx

from core.channel import Channel

logger = logging.getLogger(__name__)

GRAPH = "https://graph.facebook.com"

# Meta's code for "you are outside the 24-hour customer service window".
OUTSIDE_WINDOW_CODE = 131047

# What a message type means to a reader, when we cannot read it ourselves. The
# agent gets these verbatim, the same way the Telegram channel narrates a
# sticker or a location, so no message type is ever met with silence.
ATTACHMENT_NOTES = {
    "sticker": "[Mijoz stiker yubordi]",
    "location": "[Mijoz joylashuvni yubordi]",
    "contacts": "[Mijoz kontakt yubordi]",
    "document": "[Mijoz fayl yubordi]",
    "video": "[Mijoz video yubordi]",
    "unsupported": "[Mijoz qo'llab-quvvatlanmaydigan xabar yubordi]",
}


@dataclass
class _Pending:
    """Messages from one chat that arrived while the sender was still typing."""

    texts: list[str] = field(default_factory=list)
    images: list[bytes] = field(default_factory=list)
    timer: Optional[asyncio.Task] = None
    name: str = ""


class WhatsAppChannel(Channel):
    def __init__(
        self,
        *,
        name: str = "whatsapp",
        agent,
        server,
        token: str,
        phone_id: str,
        api_version: str = "v21.0",
        debounce_seconds: float = 4.0,
        transcriber=None,
        mute_store=None,
        profile_store=None,
        message_guard=None,
        request_timeout: float = 30.0,
    ) -> None:
        super().__init__(
            name=name,
            agent=agent,
            mute_store=mute_store,
            profile_store=profile_store,
            message_guard=message_guard,
        )
        self._server = server
        self._token = token
        self._phone_id = str(phone_id)
        self._base = f"{GRAPH}/{api_version}"
        self._debounce = debounce_seconds
        self._transcriber = transcriber
        self._timeout = request_timeout
        self._pending: dict[int, _Pending] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        server.register("whatsapp", self.handle_payload)

    # ----- lifecycle -----------------------------------------------------

    async def run(self) -> None:
        await self._server.serve_once()

    # ----- inbound -------------------------------------------------------

    async def handle_payload(self, payload: dict) -> None:
        """Unwrap Meta's envelope. One POST can carry several chats' messages."""
        for entry in payload.get("entry") or []:
            for change in entry.get("changes") or []:
                value = change.get("value") or {}
                names = {
                    str(c.get("wa_id")): ((c.get("profile") or {}).get("name") or "")
                    for c in (value.get("contacts") or [])
                }
                for message in value.get("messages") or []:
                    try:
                        await self._on_message(message, names)
                    except Exception:
                        logger.exception("WhatsApp: failed to take in a message")
                # Delivery/read receipts arrive here too; nothing to do with
                # them yet, and logging each one would drown the real traffic.

    async def _on_message(self, message: dict, names: dict[str, str]) -> None:
        wa_id = str(message.get("from") or "")
        if not wa_id.isdigit():
            logger.warning("WhatsApp: message from an unusable id %r", wa_id)
            return
        chat_id = int(wa_id)
        kind = str(message.get("type") or "")

        text = ""
        image: Optional[bytes] = None
        if kind == "text":
            text = ((message.get("text") or {}).get("body") or "").strip()
        elif kind in ("audio", "voice"):
            text = await self._transcribe(message.get(kind) or {})
        elif kind == "image":
            image = await self._download(((message.get("image") or {}).get("id")) or "")
            caption = ((message.get("image") or {}).get("caption") or "").strip()
            text = caption or ""
            if image is None:
                text = text or "[Mijoz rasm yubordi, lekin uni ocholmadim]"
        elif kind in ("button", "interactive"):
            # A tapped reply button is the customer answering, so it is text.
            block = message.get(kind) or {}
            text = (
                block.get("text")
                or ((block.get("button_reply") or {}).get("title"))
                or ((block.get("list_reply") or {}).get("title"))
                or ""
            ).strip()
        else:
            text = ATTACHMENT_NOTES.get(kind, f"[Mijoz {kind} yubordi]")

        if not text and image is None:
            return

        await self._remember(chat_id, names.get(wa_id, ""))
        await self._mark_read(str(message.get("id") or ""))
        self._buffer(chat_id, text, image, names.get(wa_id, ""))

    def _buffer(self, chat_id: int, text: str, image: Optional[bytes], name: str) -> None:
        pending = self._pending.setdefault(chat_id, _Pending())
        if text:
            pending.texts.append(text)
        if image is not None:
            pending.images.append(image)
        if name:
            pending.name = name
        if pending.timer is not None:
            pending.timer.cancel()
        pending.timer = asyncio.create_task(self._flush_after_pause(chat_id))

    async def _flush_after_pause(self, chat_id: int) -> None:
        try:
            await asyncio.sleep(self._debounce)
        except asyncio.CancelledError:
            return
        pending = self._pending.pop(chat_id, None)
        if pending is None:
            return
        content: Any
        text = "\n".join(t for t in pending.texts if t).strip()
        if pending.images:
            blocks: list[dict] = []
            if text:
                blocks.append({"type": "text", "text": text})
            for raw in pending.images:
                blocks.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/jpeg;base64,"
                            + base64.b64encode(raw).decode("ascii")
                        },
                    }
                )
            content = blocks
        else:
            content = text
        if not content:
            return

        lock = self._locks.setdefault(chat_id, asyncio.Lock())
        async with lock:
            try:
                reply = await self.dispatch(chat_id, content, images=pending.images)
            except Exception:
                logger.exception("WhatsApp: agent failed for chat %s", chat_id)
                return
            if reply:
                await self.send_text(chat_id, reply)

    # ----- outbound ------------------------------------------------------

    async def send_text(self, chat_id: int, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        await self._post(
            {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": str(chat_id),
                "type": "text",
                # Let WhatsApp render links as previews; the agent sends payment
                # links and a raw URL with no preview reads as suspicious.
                "text": {"preview_url": True, "body": text[:4096]},
            }
        )

    async def send_photos(self, chat_id: int, urls: list[str]) -> None:
        for url in [u for u in urls if u][:10]:
            await self._post(
                {
                    "messaging_product": "whatsapp",
                    "to": str(chat_id),
                    "type": "image",
                    "image": {"link": url},
                }
            )

    async def _post(self, body: dict) -> Optional[dict]:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{self._base}/{self._phone_id}/messages",
                    json=body,
                    headers={"Authorization": f"Bearer {self._token}"},
                )
        except httpx.HTTPError as exc:
            logger.error("WhatsApp send failed to reach Meta: %s", exc)
            return None
        if response.status_code < 300:
            return response.json()

        error = {}
        try:
            error = (response.json() or {}).get("error") or {}
        except ValueError:
            pass
        code = error.get("code")
        if code == OUTSIDE_WINDOW_CODE:
            # The single most important failure to name precisely: the message
            # was correct and the customer is fine — WhatsApp simply does not
            # allow free text this long after they last wrote.
            logger.error(
                "WhatsApp refused a message to %s: outside the 24-hour window — "
                "only an approved template may be sent now",
                body.get("to"),
            )
        else:
            logger.error(
                "WhatsApp send failed (%s): %s",
                response.status_code, error.get("message") or response.text[:300],
            )
        return None

    # ----- odds and ends -------------------------------------------------

    async def get_chat_info(self, chat_id: int) -> dict:
        """WhatsApp gives a name only on inbound messages, never on demand."""
        return {"name": str(chat_id), "username": None}

    async def _remember(self, chat_id: int, name: str) -> None:
        """Cache the WhatsApp profile name so the panel can label the chat."""
        if not name or self.profile_store is None:
            return
        try:
            await self.profile_store.upsert(chat_id, name=name, username=None)
        except Exception:
            logger.debug("WhatsApp: profile save failed for %s", chat_id, exc_info=True)

    async def _mark_read(self, message_id: str) -> None:
        if not message_id:
            return
        await self._post(
            {"messaging_product": "whatsapp", "status": "read", "message_id": message_id}
        )

    async def _transcribe(self, block: dict) -> str:
        raw = await self._download(str(block.get("id") or ""))
        if raw is None or self._transcriber is None:
            return "[Mijoz ovozli xabar yubordi, lekin uni o'qib bo'lmadi]"
        mime = str(block.get("mime_type") or "audio/ogg").split(";")[0]
        try:
            text = await self._transcriber.transcribe(
                BytesIO(raw),
                filename="voice." + (mime.rsplit("/", 1)[-1] or "ogg"),
                mime_type=mime,
            )
        except Exception:
            logger.exception("WhatsApp: transcription failed")
            text = ""
        return text.strip() or "[Mijoz ovozli xabar yubordi, lekin uni o'qib bo'lmadi]"

    async def _download(self, media_id: str) -> Optional[bytes]:
        """Media is a two-step fetch: id → signed URL → bytes."""
        if not media_id:
            return None
        headers = {"Authorization": f"Bearer {self._token}"}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                meta = await client.get(f"{self._base}/{media_id}", headers=headers)
                meta.raise_for_status()
                url = (meta.json() or {}).get("url")
                if not url:
                    return None
                # The signed URL still needs the token: it is not public.
                blob = await client.get(url, headers=headers)
                blob.raise_for_status()
                return blob.content
        except (httpx.HTTPError, ValueError):
            logger.exception("WhatsApp: could not download media %s", media_id)
            return None
