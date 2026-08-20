"""Operator notification fan-out over the Telegram Bot HTTP API.

Three primitives:
  - `notify_text`        — broadcast plain (or HTML) text to every admin
  - `notify_with_photo`  — broadcast a photo + caption, remembering which
                           messages were sent so they can be updated later
  - `update_tracked`     — edit the captions of a previously-sent photo
                           broadcast and post a reply with a status note

The "tracking key" pattern lets a tenant key on whatever it cares about
(e.g. customer chat id, order id) and update all admin copies atomically.

The `unmute_button` / `unmute_button_disabled` helpers produce the
inline-keyboard markup used to re-enable the AI for a specific customer
chat after a payment / booking has muted it. The callback_data format
(``unmute:<chat_id>``) is shared with the bot channel that handles
CallbackQuery updates.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Optional

import httpx

logger = logging.getLogger(__name__)


UNMUTE_CALLBACK_PREFIX = "unmute:"


def unmute_button(chat_id: int, text: str = "🤖 Подключить ИИ") -> dict:
    """Inline-keyboard markup with a single button that re-enables the AI."""
    return {
        "inline_keyboard": [
            [{"text": text, "callback_data": f"{UNMUTE_CALLBACK_PREFIX}{int(chat_id)}"}]
        ]
    }


def unmute_button_disabled(text: str = "✅ ИИ подключён") -> dict:
    """Same shape but with a no-op callback_data — used to swap the button
    after a successful unmute so operators see the change."""
    return {
        "inline_keyboard": [
            [{"text": text, "callback_data": "noop"}]
        ]
    }


@dataclass
class _TrackedBroadcast:
    caption: str
    admin_messages: list[tuple[int, int]] = field(default_factory=list)


class TelegramOperatorNotifier:
    def __init__(
        self,
        *,
        bot_token: str,
        admin_chat_ids: Iterable[int],
        request_timeout: int = 15,
        default_parse_mode: str = "HTML",
    ) -> None:
        self._token = bot_token
        self._admins = list(admin_chat_ids)
        self._timeout = request_timeout
        self._parse_mode = default_parse_mode
        self._tracked: dict[str, _TrackedBroadcast] = {}
        self._lock = asyncio.Lock()

    def _url(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self._token}/{method}"

    async def notify_text(
        self,
        text: str,
        *,
        parse_mode: Optional[str] = None,
        disable_link_preview: bool = True,
        reply_markup: Optional[dict] = None,
    ) -> int:
        if not self._token or not self._admins:
            logger.warning("Notifier not configured; dropping text broadcast")
            return 0
        sent = 0
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for chat_id in self._admins:
                try:
                    payload = {
                        "chat_id": chat_id,
                        "text": text,
                        "parse_mode": parse_mode or self._parse_mode,
                        "disable_web_page_preview": disable_link_preview,
                    }
                    if reply_markup is not None:
                        payload["reply_markup"] = reply_markup
                    resp = await client.post(
                        self._url("sendMessage"),
                        json=payload,
                    )
                    resp.raise_for_status()
                    sent += 1
                except Exception:
                    logger.exception("Failed to notify admin %s", chat_id)
        return sent

    async def notify_with_photo(
        self,
        *,
        photo_url: str,
        caption: str,
        tracking_key: Optional[str] = None,
        parse_mode: Optional[str] = None,
        reply_markup: Optional[dict] = None,
    ) -> int:
        if not self._token or not self._admins:
            logger.warning("Notifier not configured; dropping photo broadcast")
            return 0
        sent = 0
        message_pairs: list[tuple[int, int]] = []
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for chat_id in self._admins:
                try:
                    payload = {
                        "chat_id": chat_id,
                        "photo": photo_url,
                        "caption": caption,
                        "parse_mode": parse_mode or self._parse_mode,
                    }
                    if reply_markup is not None:
                        payload["reply_markup"] = reply_markup
                    resp = await client.post(
                        self._url("sendPhoto"),
                        json=payload,
                    )
                    resp.raise_for_status()
                    payload = resp.json()
                    message_id = payload.get("result", {}).get("message_id")
                    if message_id is not None:
                        message_pairs.append((chat_id, message_id))
                    sent += 1
                except Exception:
                    logger.exception("Failed to notify admin %s", chat_id)
        if tracking_key is not None:
            async with self._lock:
                # If the same key fires twice (resend), latest wins for updates.
                self._tracked[tracking_key] = _TrackedBroadcast(
                    caption=caption,
                    admin_messages=message_pairs,
                )
        return sent

    async def update_tracked(
        self,
        tracking_key: str,
        *,
        new_caption: str,
        reply_note: Optional[str] = None,
        parse_mode: Optional[str] = None,
        reply_markup: Optional[dict] = None,
    ) -> int:
        async with self._lock:
            tracked = self._tracked.get(tracking_key)
            if tracked is None:
                logger.warning("No tracked broadcast for key %s", tracking_key)
                return 0
            tracked.caption = new_caption
            targets = list(tracked.admin_messages)

        updated = 0
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for chat_id, message_id in targets:
                try:
                    edit_payload = {
                        "chat_id": chat_id,
                        "message_id": message_id,
                        "caption": new_caption,
                        "parse_mode": parse_mode or self._parse_mode,
                    }
                    # editMessageCaption accepts reply_markup, so we can update
                    # the caption and attach the button in a single call.
                    if reply_markup is not None:
                        edit_payload["reply_markup"] = reply_markup
                    edit_resp = await client.post(
                        self._url("editMessageCaption"),
                        json=edit_payload,
                    )
                    edit_resp.raise_for_status()
                    updated += 1
                except Exception:
                    logger.exception("Failed to edit caption for %s/%s", chat_id, message_id)
                if reply_note:
                    try:
                        reply_resp = await client.post(
                            self._url("sendMessage"),
                            json={
                                "chat_id": chat_id,
                                "text": reply_note,
                                "parse_mode": parse_mode or self._parse_mode,
                                "reply_to_message_id": message_id,
                                "disable_web_page_preview": True,
                            },
                        )
                        reply_resp.raise_for_status()
                    except Exception:
                        logger.exception("Failed to send reply for %s/%s", chat_id, message_id)
        return updated
