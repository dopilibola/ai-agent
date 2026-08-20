"""The mute-store interface (Protocol) the framework depends on.

When a customer completes a job (pays for a bouquet, books a visit), the AI
should stop responding to that chat until an operator explicitly re-enables it.
The store is the source of truth for that "AI off" flag, and must be shared
across a tenant's processes — the mute is *set* in one process (e.g. oygul's
customer userbot, where the payment tool runs) and *cleared* in another (the
merchant bot, where the operator clicks the inline button).

`db.PostgresMuteStore` is the implementation; tenant wiring constructs it in
`services.py`.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class MuteStore(Protocol):
    """The mute interface the framework depends on; `db.PostgresMuteStore`
    satisfies it."""

    async def is_muted(self, chat_id: int) -> bool: ...
    async def mute(self, chat_id: int) -> None: ...
    async def unmute(self, chat_id: int) -> bool: ...
    async def snapshot(self) -> frozenset[int]: ...
