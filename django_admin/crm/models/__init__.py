"""Unmanaged Django models mirroring the tables Alembic owns.

Split per tenant to match the repo's layout (`apps/<tenant>/models.py`), plus
`shared.py` for the tenant-agnostic runtime tables in `db/models.py`.
"""

from __future__ import annotations

from .anfa import AnfaCatalogItem, AnfaDoctor
from .byd import BydLead, BydOperatorTask, BydProgram, BydScheduledTask
from .maskan import (
    MaskanCemetery,
    MaskanGrave,
    MaskanLead,
    MaskanOrder,
    MaskanPayment,
    MaskanScheduledTask,
    MaskanService,
)
from .oygul import OygulBouquet, OygulOrder
from .shared import (
    ChatProfile,
    ChatTokenUsage,
    ConversationEvent,
    LearningExample,
    MutedChat,
    PanelSetting,
)

__all__ = [
    "AnfaCatalogItem",
    "AnfaDoctor",
    "BydLead",
    "BydOperatorTask",
    "BydProgram",
    "BydScheduledTask",
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
    "OygulBouquet",
    "OygulOrder",
]
