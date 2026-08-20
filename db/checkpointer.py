"""LangGraph checkpointer scope.

`checkpointer_scope()` is an async context manager each tenant entrypoint wraps
around `Runtime.run_async()`. It yields an `AsyncPostgresSaver` over a psycopg
connection pool, so conversation state survives restarts and is shared by every
process pointed at the same database. **`DATABASE_URL` is required** — the
platform is Postgres-only; there is no JSON / in-memory fallback.

`autocommit=True` + `row_factory=dict_row` are required by the saver (see the
langgraph checkpoint-postgres docs); `prepare_threshold=0` keeps it safe behind
transaction poolers like pgbouncer.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, AsyncIterator

from db.engine import database_configured, database_url

if TYPE_CHECKING:
    from langgraph.checkpoint.base import BaseCheckpointSaver

logger = logging.getLogger(__name__)


@asynccontextmanager
async def checkpointer_scope() -> AsyncIterator["BaseCheckpointSaver"]:
    if not database_configured():
        raise SystemExit(
            "DATABASE_URL is not set. The platform is Postgres-only — set "
            "DATABASE_URL (see docker-compose.yml) and run `uv run alembic upgrade head`."
        )

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    async with AsyncConnectionPool(
        conninfo=database_url(),
        open=False,
        max_size=10,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
        },
    ) as pool:
        checkpointer = AsyncPostgresSaver(pool)
        await checkpointer.setup()  # idempotent; creates the checkpoint tables
        logger.info("LangGraph checkpointer: Postgres")
        yield checkpointer
