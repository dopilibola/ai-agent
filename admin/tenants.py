"""Static registry of known tenants.

The platform has no tenant table — a tenant is code under `apps/<tenant>`. The
panel only needs each tenant's id, a display name, and its channel names (the
middle segment of a `thread_id`: ``tenant:channel:chat_id``). Channel lists are
cosmetic — the chats view derives the channels that actually have conversations
from the checkpoint thread_ids — but keeping them here documents the topology.

Keep this in sync when you add a tenant (see CLAUDE.md "Adding a tenant").
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException


@dataclass(frozen=True)
class TenantInfo:
    id: str
    name: str
    channels: tuple[str, ...]
    has_prompts: bool = False        # editable agent system prompts (admin/prompts.py)


TENANTS: dict[str, TenantInfo] = {
    # Maskan has no CRM view here on purpose: its catalogue, graves and orders
    # live in the Maskan Django backend (which ships its own admin), not in this
    # Postgres. Only the prompts are editable from this panel.
    "maskan": TenantInfo(
        id="maskan",
        name="Maskan — grave care",
        channels=("customer", "operator"),
        has_prompts=True,
    ),
}


def get_tenant(tenant_id: str) -> TenantInfo:
    info = TENANTS.get(tenant_id)
    if info is None:
        raise HTTPException(status_code=404, detail=f"Unknown tenant: {tenant_id}")
    return info

