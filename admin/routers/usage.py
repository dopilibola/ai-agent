"""Token-usage / cost endpoints.

`GET /usage` is the cross-tenant rollup for the dashboard; the per-tenant route
breaks it down by chat.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from admin.repository import Repository, get_repo
from admin.security import RequireUser
from admin.tenants import get_tenant

router = APIRouter(tags=["usage"])


@router.get("/usage")
async def usage_rollup(
    repo: Repository = Depends(get_repo),
    user: str = RequireUser,
) -> list[dict[str, Any]]:
    return await repo.usage_rollup()


@router.get("/tenants/{tenant}/usage")
async def tenant_usage(
    tenant: str,
    repo: Repository = Depends(get_repo),
    user: str = RequireUser,
) -> dict[str, Any]:
    get_tenant(tenant)
    return await repo.tenant_usage(tenant)
