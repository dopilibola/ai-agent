"""Per-tenant orders — list + status change.

Backed by the tenant's `repository.py` (the same rows `notify_order_tool` /
`update_order_status_tool` write). Changing status here updates the same source
of truth the bot reads; it does NOT edit the operator's Telegram notification
(that fan-out lives in the bot process).
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from admin.security import RequireUser
from admin.tenants import domain_repo

router = APIRouter(prefix="/tenants/{tenant}", tags=["orders"])

# Mirrors apps.oygul.notifications.STATUS_* — kept here so the panel stays
# decoupled from the tenant's notification module.
_VALID_STATUSES = {"pending", "paid"}


class StatusIn(BaseModel):
    status: str


@router.get("/orders")
async def list_orders(
    tenant: str,
    status: Optional[str] = None,
    user: str = RequireUser,
) -> list[dict[str, Any]]:
    repo = domain_repo(tenant)
    items = await repo.list_orders(status=status)
    return [o.to_dict() for o in items]


@router.post("/orders/{order_id}/status")
async def set_order_status(
    tenant: str,
    order_id: int,
    body: StatusIn,
    user: str = RequireUser,
) -> dict[str, Any]:
    if body.status not in _VALID_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"status must be one of {sorted(_VALID_STATUSES)}",
        )
    repo = domain_repo(tenant)
    ok = await repo.set_order_status(order_id, body.status)
    if not ok:
        raise HTTPException(status_code=404, detail="Order not found")
    return {"id": order_id, "status": body.status}
