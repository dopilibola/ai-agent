"""Conversation transcript for one chat thread.

Reads the latest LangGraph checkpoint for ``tenant:channel:chat_id`` and returns
its message list. `channel` is the middle thread_id segment (e.g. customer,
merchant, bot, userbot).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from admin.repository import Repository, get_repo
from admin.security import RequireUser
from admin.tenants import get_tenant

router = APIRouter(prefix="/tenants/{tenant}", tags=["conversations"])


@router.get("/conversations/{channel}/{chat_id}")
async def get_conversation(
    tenant: str,
    channel: str,
    chat_id: int,
    repo: Repository = Depends(get_repo),
    user: str = RequireUser,
) -> dict[str, Any]:
    get_tenant(tenant)
    thread_id = f"{tenant}:{channel}:{chat_id}"
    messages = await repo.conversation(thread_id)
    return {
        "thread_id": thread_id,
        "tenant": tenant,
        "channel": channel,
        "chat_id": chat_id,
        "messages": messages,
    }
