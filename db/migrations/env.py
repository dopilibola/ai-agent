"""Alembic environment — async, driven by db.engine + db.models.

Run with: `uv run alembic upgrade head` (requires DATABASE_URL set).
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from db.engine import database_configured, sqlalchemy_url
from db.models import Base

# Register per-tenant domain tables on the shared Base.metadata so
# `alembic revision --autogenerate` sees them. Each tenant declares its tables
# on db.models.Base in apps/<tenant>/models.py (see CLAUDE.md "Adding a tenant").
import apps.oygul.models  # noqa: E402,F401
import apps.anfa.models  # noqa: E402,F401
import apps.byd.models  # noqa: E402,F401
import apps.maskan.models  # noqa: E402,F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=sqlalchemy_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    if not database_configured():
        raise SystemExit("DATABASE_URL is not set; nothing to migrate.")
    engine = create_async_engine(sqlalchemy_url())
    async with engine.connect() as connection:
        await connection.run_sync(_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
