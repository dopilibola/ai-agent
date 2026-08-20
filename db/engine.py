"""Async SQLAlchemy engine + session factory, gated on DATABASE_URL.

Lazy: sqlalchemy is imported only when an engine is actually requested, so
importing this module (and therefore `db`) costs nothing when Postgres is
unconfigured. One engine per process; its pool is shared by every store and
repository in that process.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

_engine: Optional["AsyncEngine"] = None
_sessionmaker: "Optional[async_sessionmaker[AsyncSession]]" = None


def database_url() -> str:
    """Raw psycopg-style URL (postgresql://...). Empty when unconfigured.

    This form is what psycopg / the LangGraph pool want. SQLAlchemy needs the
    driver in the scheme — see `sqlalchemy_url()`.
    """
    return (os.environ.get("DATABASE_URL") or "").strip()


def database_configured() -> bool:
    return bool(database_url())


def sqlalchemy_url() -> str:
    """DATABASE_URL with the psycopg driver in the scheme for SQLAlchemy."""
    url = database_url()
    if url.startswith("postgresql+"):
        return url
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://") :]
    return url


def get_engine() -> "AsyncEngine":
    global _engine
    if _engine is None:
        if not database_configured():
            raise RuntimeError("DATABASE_URL is not set; cannot create an engine.")
        from sqlalchemy.ext.asyncio import create_async_engine

        _engine = create_async_engine(sqlalchemy_url(), pool_pre_ping=True)
    return _engine


def get_sessionmaker() -> "async_sessionmaker[AsyncSession]":
    global _sessionmaker
    if _sessionmaker is None:
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

        _sessionmaker = async_sessionmaker(
            get_engine(), class_=AsyncSession, expire_on_commit=False
        )
    return _sessionmaker


async def dispose_engine() -> None:
    """Tear down the engine + pool (call on shutdown if you opened one)."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
