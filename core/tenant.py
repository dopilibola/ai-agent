"""Tenant — a declarative bundle of agents, channels, and sync jobs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.agent import Agent
    from core.channel import Channel
    from sync.loop import SyncJob


@dataclass
class Tenant:
    """All the moving parts a single tenant deploys.

    `agents` is a dict for easy reference (e.g. when one channel routes to a
    customer agent and another to an admin agent); each channel already holds a
    direct reference to its bound agent, so the dict is mostly for introspection.
    """

    id: str
    agents: dict[str, "Agent"]
    channels: list["Channel"]
    sync_jobs: list["SyncJob"] = field(default_factory=list)

    def __post_init__(self) -> None:
        for channel in self.channels:
            channel.tenant_id = self.id
