"""Tenant list (static registry)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from admin.security import RequireUser
from admin.tenants import TENANTS

router = APIRouter(prefix="/tenants", tags=["tenants"])


@router.get("")
async def list_tenants(user: str = RequireUser) -> list[dict[str, Any]]:
    return [
        {
            "id": t.id,
            "name": t.name,
            "channels": list(t.channels),
            "has_catalog": t.has_catalog,
            "has_orders": t.has_orders,
            "has_services": t.has_services,
            "has_catalog_import": t.has_catalog_import,
            "has_doctors": t.has_doctors,
            "has_prompts": t.has_prompts,
        }
        for t in TENANTS.values()
    ]
