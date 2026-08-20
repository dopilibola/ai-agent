"""Context variables for tool callbacks.

Tools registered on an Agent run inside `Agent.invoke()`. They often need to
call back into the live channel (send a status message, fan a photo album,
toggle the typing indicator, look up the customer's Telegram username, etc).
Threading those handles through every tool signature would be intrusive, so we
publish them as ContextVars set by the channel just before invoking the agent.

A channel implementation MUST set the tenant/chat/channel vars before invoking
and reset them in a `finally` block — see `core.channel.Channel.dispatch()` for
the canonical pattern. `current_images` is optional: channels that retain the
raw bytes of inbound photos publish them here so a tool can re-use the image
(e.g. oygul's merchant `add_bouquet_tool` uploads + embeds the uploaded photo);
channels that don't leave it at its empty default.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from core.channel import Channel

current_tenant_id: ContextVar[Optional[str]] = ContextVar(
    "current_tenant_id", default=None
)
current_chat_id: ContextVar[Optional[int]] = ContextVar(
    "current_chat_id", default=None
)
current_channel: ContextVar[Optional["Channel"]] = ContextVar(
    "current_channel", default=None
)
# Raw bytes of the inbound photos for the current turn (empty if none / not
# retained). Default is an immutable empty tuple to avoid a shared-mutable.
current_images: ContextVar[tuple[bytes, ...]] = ContextVar(
    "current_images", default=()
)
