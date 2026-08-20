"""SQLAlchemy models for shared runtime state (Postgres backend).

These two tables are the Postgres equivalents of the per-tenant JSON stores:
muted chats and per-chat token usage. Both are scoped by `tenant_id`, so every
tenant process shares one database.

Tenant *domain* data (catalog, orders, bookings — the per-tenant CRM) is
intentionally NOT modelled here yet; that's planned separately.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, DateTime, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class MutedChat(Base):
    """A chat where the tenant's agent is muted (operator has taken over)."""

    __tablename__ = "muted_chats"

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    muted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ChatTokenUsage(Base):
    """Per-chat token accounting: `current` = last run, `spent` = cumulative."""

    __tablename__ = "chat_token_usage"

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    current_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    current_cached_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    current_output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    current_total_tokens: Mapped[int] = mapped_column(Integer, default=0)

    spent_input_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    spent_cached_input_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    spent_output_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    spent_total_tokens: Mapped[int] = mapped_column(BigInteger, default=0)

    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ChatProfile(Base):
    """Cached Telegram identity for a chat — the sender's display name and
    @username, captured by the channel on inbound messages so the admin panel
    can label a chat by who it is rather than just its numeric id. Best-effort
    and refreshed when the name changes; never required for the runtime."""

    __tablename__ = "chat_profiles"

    tenant_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
