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
from typing import TYPE_CHECKING, Union

from fastapi import HTTPException

if TYPE_CHECKING:
    from apps.anfa.repository import AnfaRepository
    from apps.oygul.repository import OygulRepository


@dataclass(frozen=True)
class TenantInfo:
    id: str
    name: str
    channels: tuple[str, ...]
    # CRM capabilities — which per-tenant domain views the panel should expose.
    # A tenant only has these if it ships `apps/<tenant>/repository.py`.
    has_catalog: bool = False        # oygul bouquets
    has_orders: bool = False         # oygul orders
    has_services: bool = False       # anfa service catalog
    has_catalog_import: bool = False  # anfa Excel catalog upload
    has_doctors: bool = False        # anfa doctor roster (+ Word upload)
    has_prompts: bool = False        # editable agent system prompts (admin/prompts.py)


TENANTS: dict[str, TenantInfo] = {
    "oygul": TenantInfo(
        id="oygul",
        name="Oygul — flower shop",
        channels=("customer", "merchant"),
        has_catalog=True,
        has_orders=True,
        has_prompts=True,
    ),
    "anfa": TenantInfo(
        id="anfa",
        name="Anfa — Tashkent clinic",
        channels=("customer", "merchant"),
        has_services=True,
        has_catalog_import=True,
        has_doctors=True,
        has_prompts=True,
    ),
    "byd": TenantInfo(
        id="byd",
        name="BYD Medical — detox clinic",
        channels=("customer", "operator"),
        has_prompts=True,
    ),
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


def domain_repo(tenant_id: str) -> "Union[OygulRepository, AnfaRepository]":
    """Resolve a tenant's domain repository.

    Imported lazily so the panel only pulls in a tenant's data layer when a
    domain route is actually hit, and so the import graph stays narrow (the
    tenant repositories are import-light — no agent/Telegram runtime). 404s for
    tenants that don't ship a `repository.py`.
    """
    get_tenant(tenant_id)
    if tenant_id == "oygul":
        from apps.oygul.repository import get_repository

        return get_repository()
    if tenant_id == "anfa":
        from apps.anfa.repository import get_repository

        return get_repository()
    raise HTTPException(
        status_code=404, detail=f"Tenant {tenant_id} has no domain repository"
    )
