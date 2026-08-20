"""TelegramBotChannel — Telethon client authenticated with a @BotFather token.

Best for merchant / staff facing flows where you want explicit allow-listing
(`allowed_user_ids`) and the official Bot API surface (inline keyboards, etc).
"""

from __future__ import annotations

from typing import Iterable, Optional

from channels.telegram._telethon_base import TelethonChannel
from core.agent import Agent
from voice.transcriber import VoiceTranscriber


class TelegramBotChannel(TelethonChannel):
    def __init__(
        self,
        *,
        name: str,
        agent: Agent,
        bot_token: str,
        api_id: int,
        api_hash: str,
        session: str,
        voice: Optional[VoiceTranscriber] = None,
        allowed_user_ids: Optional[Iterable[int]] = None,
        **kwargs,
    ) -> None:
        if not bot_token:
            raise RuntimeError(
                f"Channel {name}: bot_token is required (get one from @BotFather)."
            )
        super().__init__(
            name=name,
            agent=agent,
            api_id=api_id,
            api_hash=api_hash,
            session=session,
            voice=voice,
            allowed_user_ids=allowed_user_ids,
            **kwargs,
        )
        self._bot_token = bot_token

    async def _start_client(self) -> None:
        await self._client.start(bot_token=self._bot_token)
