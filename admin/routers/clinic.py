"""anfa's service-catalog views — list rows, toggle a row, and import an Excel
export.

Backed by `apps/anfa/repository.py` via `domain_repo`. Endpoints are gated on
the tenant's capability flags, so they only serve a clinic-type tenant (404
otherwise). The Excel upload reconciles the whole catalog against the file and
the bot's KB sync mirrors it into the vector DB on its next tick.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, UploadFile

from admin.security import RequireUser
from admin.tenants import domain_repo, get_tenant

router = APIRouter(prefix="/tenants/{tenant}", tags=["clinic"])


def _require(tenant: str, capability: str):
    info = get_tenant(tenant)
    if not getattr(info, capability, False):
        raise HTTPException(status_code=404, detail=f"Tenant {tenant} has no {capability[4:]}")
    return domain_repo(tenant)


# ----- catalog ----------------------------------------------------------


@router.get("/services")
async def list_services(tenant: str, user: str = RequireUser) -> list[dict[str, Any]]:
    repo = _require(tenant, "has_services")
    return [s.to_dict() for s in await repo.list_catalog(active_only=False)]


@router.post("/services/{service_id}/deactivate")
async def deactivate_service(
    tenant: str, service_id: int, user: str = RequireUser
) -> dict[str, Any]:
    repo = _require(tenant, "has_services")
    if not await repo.set_item_active(service_id, False):
        raise HTTPException(status_code=404, detail="Service not found")
    return {"id": service_id, "active": False}


@router.post("/services/{service_id}/reactivate")
async def reactivate_service(
    tenant: str, service_id: int, user: str = RequireUser
) -> dict[str, Any]:
    repo = _require(tenant, "has_services")
    if not await repo.set_item_active(service_id, True):
        raise HTTPException(status_code=404, detail="Service not found")
    return {"id": service_id, "active": True}


# ----- Excel import -----------------------------------------------------


@router.post("/catalog/import")
async def import_catalog(
    tenant: str, file: UploadFile, user: str = RequireUser
) -> dict[str, Any]:
    """Upload the clinic's Excel service export and replace the catalog.

    The import is authoritative: rows in the file are upserted and any catalog
    row no longer present is removed. Returns a summary of what changed.
    """
    _require(tenant, "has_catalog_import")
    name = (file.filename or "").lower()
    if not name.endswith((".xlsx", ".xlsm")):
        raise HTTPException(status_code=422, detail="Expected an .xlsx file.")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=422, detail="Uploaded file is empty.")

    from apps.anfa.import_catalog import import_workbook_bytes

    try:
        summary = await import_workbook_bytes(data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return summary


# ----- doctors (reference roster) ---------------------------------------


@router.get("/doctors")
async def list_doctors(tenant: str, user: str = RequireUser) -> list[dict[str, Any]]:
    repo = _require(tenant, "has_doctors")
    return [d.to_dict() for d in await repo.list_doctors(active_only=False)]


@router.post("/doctors/{doctor_id}/deactivate")
async def deactivate_doctor(
    tenant: str, doctor_id: int, user: str = RequireUser
) -> dict[str, Any]:
    repo = _require(tenant, "has_doctors")
    if not await repo.set_doctor_active(doctor_id, False):
        raise HTTPException(status_code=404, detail="Doctor not found")
    return {"id": doctor_id, "active": False}


@router.post("/doctors/{doctor_id}/reactivate")
async def reactivate_doctor(
    tenant: str, doctor_id: int, user: str = RequireUser
) -> dict[str, Any]:
    repo = _require(tenant, "has_doctors")
    if not await repo.set_doctor_active(doctor_id, True):
        raise HTTPException(status_code=404, detail="Doctor not found")
    return {"id": doctor_id, "active": True}


@router.post("/doctors/import")
async def import_doctors(
    tenant: str, file: UploadFile, user: str = RequireUser
) -> dict[str, Any]:
    """Upload the clinic's Word doctor roster and replace the roster table."""
    _require(tenant, "has_doctors")
    name = (file.filename or "").lower()
    if not name.endswith(".docx"):
        raise HTTPException(status_code=422, detail="Expected a .docx file.")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=422, detail="Uploaded file is empty.")

    from apps.anfa.import_doctors import import_docx_bytes

    try:
        summary = await import_docx_bytes(data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return summary
