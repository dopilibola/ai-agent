"""Read/write access to the shared Postgres, for the admin panel.

Three data sources, all already populated by the running bots:

  - **mutes**  → `db.PostgresMuteStore` (table `muted_chats`)
  - **usage**  → `db.PostgresTokenStore` (table `chat_token_usage`)
  - **conversations** → LangGraph's `AsyncPostgresSaver` (table `checkpoints`)

The mute/usage stores are tenant-scoped and instantiated per call (they're
tiny). Conversation reads go through the `AsyncPostgresSaver` opened for the
app's lifetime; thread enumeration is a `DISTINCT thread_id` query on the same
psycopg pool. A `thread_id` is ``tenant:channel:chat_id``.

A `Repository` instance is built in the app lifespan and stashed on
`app.state.repo`; routes pull it via `get_repo`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import Request

from admin.tenants import TENANTS
from db.stores import PostgresMuteStore, PostgresProfileStore, PostgresTokenStore

if TYPE_CHECKING:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg_pool import AsyncConnectionPool


class Repository:
    def __init__(self, pool: "AsyncConnectionPool", saver: "AsyncPostgresSaver") -> None:
        self._pool = pool
        self._saver = saver

    # ----- mutes (AI handoff) -------------------------------------------

    async def mute(self, tenant: str, chat_id: int) -> None:
        await PostgresMuteStore(tenant).mute(chat_id)

    async def unmute(self, tenant: str, chat_id: int) -> bool:
        return await PostgresMuteStore(tenant).unmute(chat_id)

    # ----- merged chats view --------------------------------------------

    async def chats(self, tenant: str) -> list[dict[str, Any]]:
        """One row per chat the tenant has seen, merging mute + token state and
        the channel(s) the chat has a conversation on."""
        muted = await PostgresMuteStore(tenant).snapshot()  # frozenset[int]
        usage = await PostgresTokenStore(tenant).snapshot()  # dict[int, ChatTokens]
        profiles = await PostgresProfileStore(tenant).snapshot()  # dict[int, ChatProfileInfo]
        channels_by_chat = await self._channels_by_chat(tenant)

        chat_ids = set(usage) | set(muted) | set(channels_by_chat) | set(profiles)
        rows: list[dict[str, Any]] = []
        for cid in chat_ids:
            ct = usage.get(cid)
            prof = profiles.get(cid)
            rows.append(
                {
                    "chat_id": cid,
                    "name": prof.name if prof else None,
                    "username": prof.username if prof else None,
                    "channels": sorted(channels_by_chat.get(cid, set())),
                    "muted": cid in muted,
                    "spent_total_tokens": ct.spent.total_tokens if ct else 0,
                    "current_total_tokens": ct.current.total_tokens if ct else 0,
                    "updated_at": ct.updated_at if ct else None,
                }
            )
        rows.sort(key=lambda r: (r["updated_at"] or ""), reverse=True)
        return rows

    # ----- usage / cost --------------------------------------------------

    async def tenant_usage(self, tenant: str) -> dict[str, Any]:
        usage = await PostgresTokenStore(tenant).snapshot()
        chats = [
            {
                "chat_id": cid,
                "spent": _run_dict(ct.spent),
                "current": _run_dict(ct.current),
                "updated_at": ct.updated_at,
            }
            for cid, ct in usage.items()
        ]
        chats.sort(key=lambda r: (r["updated_at"] or ""), reverse=True)
        return {"tenant": tenant, "totals": _sum_spent(usage), "chats": chats}

    async def usage_rollup(self) -> list[dict[str, Any]]:
        """Per-tenant cost overview for the dashboard."""
        out: list[dict[str, Any]] = []
        for tid, info in TENANTS.items():
            usage = await PostgresTokenStore(tid).snapshot()
            out.append(
                {
                    "tenant": tid,
                    "name": info.name,
                    "chats": len(usage),
                    **_sum_spent(usage),
                }
            )
        return out

    # ----- conversations -------------------------------------------------

    async def conversation(self, thread_id: str) -> list[dict[str, Any]]:
        """Latest checkpoint's message transcript for one thread.

        Returns the *current* conversation state — note that
        SummarizationMiddleware may have compressed older turns into a summary
        message once history passed ~10k tokens.
        """
        cfg = {"configurable": {"thread_id": thread_id}}
        tup = await self._saver.aget_tuple(cfg)
        if tup is None:
            return []
        values = tup.checkpoint.get("channel_values") or {}
        messages = values.get("messages") or []
        return [_message_dict(m) for m in messages]

    async def _channels_by_chat(self, tenant: str) -> dict[int, set[str]]:
        thread_ids = await self._list_thread_ids(tenant)
        out: dict[int, set[str]] = {}
        for tid in thread_ids:
            parts = tid.split(":")
            if len(parts) < 3 or parts[0] != tenant:
                continue
            channel = parts[1]
            try:
                cid = int(parts[-1])
            except ValueError:
                continue
            out.setdefault(cid, set()).add(channel)
        return out

    async def _list_thread_ids(self, tenant: str) -> list[str]:
        async with self._pool.connection() as conn:
            cur = await conn.execute(
                "SELECT DISTINCT thread_id FROM checkpoints WHERE thread_id LIKE %s",
                (f"{tenant}:%",),
            )
            rows = await cur.fetchall()
        # pool is configured with row_factory=dict_row (see app lifespan).
        return [r["thread_id"] for r in rows if r.get("thread_id")]


def get_repo(request: Request) -> Repository:
    return request.app.state.repo


# ----- serialization helpers --------------------------------------------


def _run_dict(run: Any) -> dict[str, int]:
    return {
        "input_tokens": run.input_tokens,
        "cached_input_tokens": run.cached_input_tokens,
        "output_tokens": run.output_tokens,
        "total_tokens": run.total_tokens,
    }


def _sum_spent(usage: dict[int, Any]) -> dict[str, int]:
    return {
        "spent_total_tokens": sum(ct.spent.total_tokens for ct in usage.values()),
        "spent_input_tokens": sum(ct.spent.input_tokens for ct in usage.values()),
        "spent_cached_input_tokens": sum(ct.spent.cached_input_tokens for ct in usage.values()),
        "spent_output_tokens": sum(ct.spent.output_tokens for ct in usage.values()),
    }


def _message_dict(msg: Any) -> dict[str, Any]:
    """Flatten a LangChain message into a JSON-safe transcript entry."""
    role = getattr(msg, "type", "unknown")
    content = getattr(msg, "content", "")
    text, has_image = _extract_text(content)
    out: dict[str, Any] = {"role": role, "text": text, "has_image": has_image}

    name = getattr(msg, "name", None)
    if name:
        out["name"] = name

    tool_calls = getattr(msg, "tool_calls", None)
    if tool_calls:
        out["tool_calls"] = [
            {"name": tc.get("name"), "args": tc.get("args")}
            for tc in tool_calls
            if isinstance(tc, dict)
        ]
    return out


def _extract_text(content: Any) -> tuple[str, bool]:
    if isinstance(content, str):
        return content, False
    if isinstance(content, list):
        parts: list[str] = []
        has_image = False
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                btype = block.get("type")
                if btype == "text":
                    parts.append(block.get("text", ""))
                elif btype in ("image_url", "image"):
                    has_image = True
        return "".join(parts), has_image
    return str(content), False
