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

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
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


class ConversationEvent(Base):
    """Append-only log of what actually happened in a conversation — the corpus
    the agents are improved from.

    One row per *event*, not per turn: the customer's message, each tool call
    with its arguments and result status, the agent's reply, out-of-band funnel
    touches, and the funnel outcome (stage transitions). Rows of one customer
    turn share a `turn_id`, and a whole conversation shares `thread_id`, so an
    export can rebuild the dialogue in order and attach the outcome label to it
    (`scripts/export_training_data.py`).

    Deliberately separate from the LangGraph checkpointer: checkpoints are the
    agent's *working* state — summarised, compacted, overwritten — while this is
    the immutable record. Writing is best-effort (`db/training.py` swallows its
    own errors): a logging failure must never cost the customer a reply.
    """

    __tablename__ = "conversation_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64))
    chat_id: Mapped[int] = mapped_column(BigInteger)
    thread_id: Mapped[str] = mapped_column(String(255))
    channel: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    agent: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # Groups the events of one customer turn (user message → tools → reply).
    turn_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    # user | assistant | tool | outbound | outcome
    role: Mapped[str] = mapped_column(String(16))
    text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Detected language/script of `text`: uz_cyrl | uz_latn | ru | other
    lang: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_conversation_events_chat", "tenant_id", "chat_id", "id"),
        Index("ix_conversation_events_thread", "thread_id", "id"),
        Index("ix_conversation_events_created", "tenant_id", "created_at"),
    )
