"""Postgres backend (opt-in via DATABASE_URL).

Unset DATABASE_URL → callers fall back to the JSON / in-memory implementations,
so the apps run with no database and nothing about oygul/anfa changes. Set it →
the same Protocol-typed stores (`core.MuteStore`, `core.TokenStore`) and the
LangGraph checkpointer are backed by Postgres, shared across every tenant
process.

Heavy deps (sqlalchemy, psycopg) are imported lazily inside the functions that
use them — mirroring rag/voice — so importing `db` stays cheap and never
requires the database drivers until Postgres is actually configured.
"""

from __future__ import annotations

from db.checkpointer import checkpointer_scope
from db.engine import database_configured, database_url, dispose_engine

__all__ = [
    "PostgresMuteStore",
    "PostgresProfileStore",
    "PostgresTokenStore",
    "checkpointer_scope",
    "database_configured",
    "database_url",
    "dispose_engine",
]


def __getattr__(name: str):
    # Lazy re-export: `from db import PostgresMuteStore` shouldn't import
    # sqlalchemy until the symbol is actually requested (it only is when
    # DATABASE_URL is set).
    if name in ("PostgresMuteStore", "PostgresProfileStore", "PostgresTokenStore"):
        from db import stores

        return getattr(stores, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
