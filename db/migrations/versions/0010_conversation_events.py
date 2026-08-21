"""conversation_events: the append-only conversation corpus (training data)

Revision ID: 0010_conversation_events
Revises: 0009_maskan_funnel
Create Date: 2026-08-20

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_conversation_events"
down_revision: Union[str, None] = "0009_maskan_funnel"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "conversation_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("thread_id", sa.String(length=255), nullable=False),
        sa.Column("channel", sa.String(length=64), nullable=True),
        sa.Column("agent", sa.String(length=64), nullable=True),
        sa.Column("turn_id", sa.String(length=36), nullable=True),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("lang", sa.String(length=16), nullable=True),
        sa.Column(
            "meta",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_conversation_events_chat",
        "conversation_events",
        ["tenant_id", "chat_id", "id"],
    )
    op.create_index(
        "ix_conversation_events_thread", "conversation_events", ["thread_id", "id"]
    )
    op.create_index(
        "ix_conversation_events_created",
        "conversation_events",
        ["tenant_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_conversation_events_created", table_name="conversation_events")
    op.drop_index("ix_conversation_events_thread", table_name="conversation_events")
    op.drop_index("ix_conversation_events_chat", table_name="conversation_events")
    op.drop_table("conversation_events")
