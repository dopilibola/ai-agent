"""The profile-store interface (Protocol) the framework depends on.

A channel knows who it is talking to — a Telegram message carries the sender's
display name and @username. The runtime itself doesn't need that, but the admin
panel does (so a chat is shown as "Lola Karimova @lola" instead of a bare
numeric id). The profile store is the durable, cross-process cache of that
identity, scoped per tenant and keyed by chat_id.

`db.PostgresProfileStore` is the implementation; tenant wiring constructs it in
`services.py` and hands it to the channel, which records the name on inbound
messages.
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable


@runtime_checkable
class ProfileStore(Protocol):
    """The chat-profile interface the framework depends on;
    `db.PostgresProfileStore` satisfies it."""

    async def upsert(
        self, chat_id: int, *, name: Optional[str], username: Optional[str]
    ) -> None: ...
    async def snapshot(self) -> dict[int, "ChatProfileInfo"]: ...


class ChatProfileInfo:
    """Lightweight, ORM-free view of a stored chat profile (what `snapshot`
    returns), so the admin panel can read names without importing sqlalchemy."""

    __slots__ = ("name", "username", "updated_at")

    def __init__(
        self,
        *,
        name: Optional[str],
        username: Optional[str],
        updated_at: Optional[str],
    ) -> None:
        self.name = name
        self.username = username
        self.updated_at = updated_at
