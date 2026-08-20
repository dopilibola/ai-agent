"""Per-tenant agent prompts — list, read, and edit the system-prompt files.

Backed directly by the Markdown files under ``apps/<tenant>/prompts/`` (see
``admin/prompts.py`` for the registry). The bots re-read these files on every
invoke, so a save here changes the live agent's behaviour without a restart.
The client only ever sends a ``(tenant, key)`` pair; the file path is resolved
server-side from the static registry, so edits are bounded to the known files.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from admin.prompts import get_prompt, list_prompts, read_prompt, write_prompt
from admin.security import RequireUser
from admin.tenants import get_tenant

router = APIRouter(prefix="/tenants/{tenant}", tags=["prompts"])


class PromptIn(BaseModel):
    content: str


def _meta(info, *, content: str | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "key": info.key,
        "label": info.label,
        "note": info.note,
        "filename": info.path.name,
    }
    if content is not None:
        data["content"] = content
    return data


@router.get("/prompts")
async def list_tenant_prompts(tenant: str, user: str = RequireUser) -> list[dict[str, Any]]:
    get_tenant(tenant)
    return [_meta(p) for p in list_prompts(tenant)]


@router.get("/prompts/{key}")
async def get_tenant_prompt(tenant: str, key: str, user: str = RequireUser) -> dict[str, Any]:
    get_tenant(tenant)
    info = get_prompt(tenant, key)
    return _meta(info, content=read_prompt(info))


@router.post("/prompts/{key}")
async def save_tenant_prompt(
    tenant: str, key: str, body: PromptIn, user: str = RequireUser
) -> dict[str, Any]:
    get_tenant(tenant)
    info = get_prompt(tenant, key)
    written = write_prompt(info, body.content)
    return {"key": key, "bytes": written}
