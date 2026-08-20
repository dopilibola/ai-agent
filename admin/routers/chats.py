"""Per-tenant chats: merged mute + token state, with AI on/off toggle.

Muting takes the AI offline for a chat (a human operator owns it); unmuting
resumes the AI. These write the same `muted_chats` rows the bots read live, so
the effect is identical to the in-Telegram "Подключить ИИ" handoff button —
except the panel doesn't post the operator notification message.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from admin.repository import Repository, get_repo
from admin.security import RequireUser
from admin.tenants import get_tenant

router = APIRouter(prefix="/tenants/{tenant}", tags=["chats"])


@router.get("/chats")
async def list_chats(
    tenant: str,
    repo: Repository = Depends(get_repo),
    user: str = RequireUser,
) -> list[dict[str, Any]]:
    get_tenant(tenant)
    return await repo.chats(tenant)


@router.post("/chats/{chat_id}/mute")
async def mute_chat(
    tenant: str,
    chat_id: int,
    repo: Repository = Depends(get_repo),
    user: str = RequireUser,
) -> dict[str, Any]:
    get_tenant(tenant)
    await repo.mute(tenant, chat_id)
    return {"chat_id": chat_id, "muted": True}


@router.post("/chats/{chat_id}/unmute")
async def unmute_chat(
    tenant: str,
    chat_id: int,
    repo: Repository = Depends(get_repo),
    user: str = RequireUser,
) -> dict[str, Any]:
    get_tenant(tenant)
    was_muted = await repo.unmute(tenant, chat_id)
    return {"chat_id": chat_id, "muted": False, "was_muted": was_muted}
