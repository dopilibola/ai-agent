"""Postgres-backed `MuteStore` / `TokenStore` (the `core` Protocols).

Tenant-scoped: one table is shared across tenants, partitioned by `tenant_id`.
Unlike the JSON stores — whose cross-process safety relies on a per-process
asyncio lock and mtime reloads — these use real Postgres transactions, so
concurrent writes from separate tenant processes can't clobber each other
(`record_run` increments `spent_*` atomically via INSERT ... ON CONFLICT).

sqlalchemy + the models are imported lazily inside each method so that merely
referencing these classes (when DATABASE_URL is unset) never pulls in the
database drivers.
"""

from __future__ import annotations

from datetime import datetime, timezone

from core.profile_store import ChatProfileInfo
from core.token_store import ChatTokens, RunTokens
from db.engine import get_sessionmaker


class PostgresMuteStore:
    """`core.MuteStore` over Postgres, scoped to one tenant."""

    def __init__(self, tenant_id: str) -> None:
        self._tenant = tenant_id

    async def is_muted(self, chat_id: int) -> bool:
        from sqlalchemy import select

        from db.models import MutedChat

        async with get_sessionmaker()() as session:
            found = await session.scalar(
                select(MutedChat.chat_id).where(
                    MutedChat.tenant_id == self._tenant,
                    MutedChat.chat_id == int(chat_id),
                )
            )
            return found is not None

    async def mute(self, chat_id: int) -> None:
        from sqlalchemy.dialects.postgresql import insert

        from db.models import MutedChat

        stmt = (
            insert(MutedChat)
            .values(
                tenant_id=self._tenant,
                chat_id=int(chat_id),
                muted_at=datetime.now(timezone.utc),
            )
            .on_conflict_do_nothing(index_elements=["tenant_id", "chat_id"])
        )
        async with get_sessionmaker()() as session:
            await session.execute(stmt)
            await session.commit()

    async def unmute(self, chat_id: int) -> bool:
        from sqlalchemy import delete

        from db.models import MutedChat

        async with get_sessionmaker()() as session:
            result = await session.execute(
                delete(MutedChat).where(
                    MutedChat.tenant_id == self._tenant,
                    MutedChat.chat_id == int(chat_id),
                )
            )
            await session.commit()
            return bool(result.rowcount)

    async def snapshot(self) -> frozenset[int]:
        from sqlalchemy import select

        from db.models import MutedChat

        async with get_sessionmaker()() as session:
            rows = await session.scalars(
                select(MutedChat.chat_id).where(MutedChat.tenant_id == self._tenant)
            )
            return frozenset(int(r) for r in rows)


class PostgresTokenStore:
    """`core.TokenStore` over Postgres, scoped to one tenant."""

    def __init__(self, tenant_id: str) -> None:
        self._tenant = tenant_id

    async def record_run(self, chat_id: int, run: RunTokens) -> ChatTokens:
        if run.is_empty():
            return await self.get(chat_id)

        from sqlalchemy.dialects.postgresql import insert

        from db.models import ChatTokenUsage

        now = datetime.now(timezone.utc)
        ins = insert(ChatTokenUsage).values(
            tenant_id=self._tenant,
            chat_id=int(chat_id),
            current_input_tokens=run.input_tokens,
            current_cached_input_tokens=run.cached_input_tokens,
            current_output_tokens=run.output_tokens,
            current_total_tokens=run.total_tokens,
            spent_input_tokens=run.input_tokens,
            spent_cached_input_tokens=run.cached_input_tokens,
            spent_output_tokens=run.output_tokens,
            spent_total_tokens=run.total_tokens,
            updated_at=now,
        )
        # current = this run; spent = previous spent + this run (atomic).
        stmt = ins.on_conflict_do_update(
            index_elements=["tenant_id", "chat_id"],
            set_={
                "current_input_tokens": ins.excluded.current_input_tokens,
                "current_cached_input_tokens": ins.excluded.current_cached_input_tokens,
                "current_output_tokens": ins.excluded.current_output_tokens,
                "current_total_tokens": ins.excluded.current_total_tokens,
                "spent_input_tokens": ChatTokenUsage.spent_input_tokens + run.input_tokens,
                "spent_cached_input_tokens": ChatTokenUsage.spent_cached_input_tokens
                + run.cached_input_tokens,
                "spent_output_tokens": ChatTokenUsage.spent_output_tokens + run.output_tokens,
                "spent_total_tokens": ChatTokenUsage.spent_total_tokens + run.total_tokens,
                "updated_at": now,
            },
        )
        async with get_sessionmaker()() as session:
            await session.execute(stmt)
            await session.commit()
        return await self.get(chat_id)

    async def get(self, chat_id: int) -> ChatTokens:
        from db.models import ChatTokenUsage

        async with get_sessionmaker()() as session:
            row = await session.get(ChatTokenUsage, (self._tenant, int(chat_id)))
            return _to_chat_tokens(row) if row is not None else ChatTokens()

    async def snapshot(self) -> dict[int, ChatTokens]:
        from sqlalchemy import select

        from db.models import ChatTokenUsage

        async with get_sessionmaker()() as session:
            rows = await session.scalars(
                select(ChatTokenUsage).where(ChatTokenUsage.tenant_id == self._tenant)
            )
            return {int(r.chat_id): _to_chat_tokens(r) for r in rows}

    async def reset(self, chat_id: int) -> None:
        from sqlalchemy import delete

        from db.models import ChatTokenUsage

        async with get_sessionmaker()() as session:
            await session.execute(
                delete(ChatTokenUsage).where(
                    ChatTokenUsage.tenant_id == self._tenant,
                    ChatTokenUsage.chat_id == int(chat_id),
                )
            )
            await session.commit()


class PostgresProfileStore:
    """`core.ProfileStore` over Postgres, scoped to one tenant."""

    def __init__(self, tenant_id: str) -> None:
        self._tenant = tenant_id

    async def upsert(
        self, chat_id: int, *, name: str | None, username: str | None
    ) -> None:
        from sqlalchemy.dialects.postgresql import insert

        from db.models import ChatProfile

        now = datetime.now(timezone.utc)
        ins = insert(ChatProfile).values(
            tenant_id=self._tenant,
            chat_id=int(chat_id),
            name=name,
            username=username,
            updated_at=now,
        )
        stmt = ins.on_conflict_do_update(
            index_elements=["tenant_id", "chat_id"],
            set_={
                "name": ins.excluded.name,
                "username": ins.excluded.username,
                "updated_at": now,
            },
        )
        async with get_sessionmaker()() as session:
            await session.execute(stmt)
            await session.commit()

    async def snapshot(self) -> dict[int, ChatProfileInfo]:
        from sqlalchemy import select

        from db.models import ChatProfile

        async with get_sessionmaker()() as session:
            rows = await session.scalars(
                select(ChatProfile).where(ChatProfile.tenant_id == self._tenant)
            )
            return {int(r.chat_id): _to_profile_info(r) for r in rows}


def _to_profile_info(row) -> ChatProfileInfo:
    return ChatProfileInfo(
        name=row.name,
        username=row.username,
        updated_at=row.updated_at.isoformat(timespec="seconds") if row.updated_at else None,
    )


def _to_chat_tokens(row) -> ChatTokens:
    return ChatTokens(
        current=RunTokens(
            input_tokens=row.current_input_tokens,
            cached_input_tokens=row.current_cached_input_tokens,
            output_tokens=row.current_output_tokens,
            total_tokens=row.current_total_tokens,
        ),
        spent=RunTokens(
            input_tokens=row.spent_input_tokens,
            cached_input_tokens=row.spent_cached_input_tokens,
            output_tokens=row.spent_output_tokens,
            total_tokens=row.spent_total_tokens,
        ),
        updated_at=row.updated_at.isoformat(timespec="seconds") if row.updated_at else None,
    )
