"""Per-tenant catalogue (bouquets) — read + deactivate.

Backed by the tenant's own `repository.py` (the same one the merchant bot writes
through), resolved via `admin.tenants.domain_repo`. 404s for tenants without a
catalogue. Deactivating is a soft-delete on the SQL source of truth; pruning the
bouquet from CLIP/Chroma search is a separate concern.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from admin.security import RequireUser
from admin.tenants import domain_repo

router = APIRouter(prefix="/tenants/{tenant}", tags=["catalog"])


@router.get("/bouquets")
async def list_bouquets(
    tenant: str,
    q: str = "",
    include_inactive: bool = False,
    limit: int = 50,
    offset: int = 0,
    user: str = RequireUser,
) -> dict[str, Any]:
    """Paginated catalogue. `q` filters by name (case-insensitive substring)."""
    repo = domain_repo(tenant)
    search = q.strip() or None
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    items = await repo.list_bouquets(
        include_inactive=include_inactive, search=search, limit=limit, offset=offset
    )
    total = await repo.count_bouquets(include_inactive=include_inactive, search=search)
    return {
        "items": [b.to_dict() for b in items],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.post("/bouquets/{bouquet_id}/deactivate")
async def deactivate_bouquet(
    tenant: str,
    bouquet_id: str,
    user: str = RequireUser,
) -> dict[str, Any]:
    repo = domain_repo(tenant)
    ok = await repo.deactivate_bouquet(bouquet_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Bouquet not found")
    return {"id": bouquet_id, "active": False}


@router.post("/bouquets/{bouquet_id}/reactivate")
async def reactivate_bouquet(
    tenant: str,
    bouquet_id: str,
    user: str = RequireUser,
) -> dict[str, Any]:
    repo = domain_repo(tenant)
    ok = await repo.reactivate_bouquet(bouquet_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Bouquet not found")
    return {"id": bouquet_id, "active": True}
