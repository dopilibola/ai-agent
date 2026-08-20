"""TelegramUserChannel — Telethon client logged in as a real user account.

Session must be pre-created via `scripts/anfa_telethon_login.py` or
equivalent (interactive phone + SMS-code flow). The user account talks to
customers from a real account, which feels more like a human handoff than a
@BotFather bot does.
"""

from __future__ import annotations

from typing import Iterable, Optional

from channels.telegram._telethon_base import TelethonChannel
from core.agent import Agent
from voice.transcriber import VoiceTranscriber


class TelegramUserChannel(TelethonChannel):
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
        **kwargs,
    ) -> None:
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

    async def _start_client(self) -> None:
        # `start()` would prompt interactively for phone + SMS code if the
        # session is missing or unauthorised. Production runs are headless, so
        # do an explicit connect + auth check instead: a failure here is
        # caught by the runtime's per-task watcher and only this channel exits.
        await self._client.connect()
        if not await self._client.is_user_authorized():
            await self._client.disconnect()
            raise RuntimeError(
                f"Userbot channel {self.name!r} is not authorised. "
                f"Run an interactive Telethon login script "
                f"(e.g. scripts/<tenant>_telethon_login.py) once to create "
                f"the session, then restart."
            )
