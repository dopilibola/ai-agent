"""Unmanaged Django models mirroring the tables Alembic owns.

Split per tenant to match the repo's layout (`apps/<tenant>/models.py`), plus
`shared.py` for the tenant-agnostic runtime tables in `db/models.py`.
"""

from __future__ import annotations

from .maskan import (
    MaskanCemetery,
    MaskanGrave,
    MaskanLead,
    MaskanOrder,
    MaskanPayment,
    MaskanScheduledTask,
    MaskanService,
)
from .shared import (
    ChatProfile,
    ChatTokenUsage,
    ConversationEvent,
    LearningExample,
    MutedChat,
    PanelSetting,
)

__all__ = [
    "ChatProfile",
    "ChatTokenUsage",
    "ConversationEvent",
    "LearningExample",
    "MaskanCemetery",
    "MaskanGrave",
    "MaskanLead",
    "MaskanOrder",
    "MaskanPayment",
    "MaskanScheduledTask",
    "MaskanService",
    "MutedChat",
    "PanelSetting",
]
