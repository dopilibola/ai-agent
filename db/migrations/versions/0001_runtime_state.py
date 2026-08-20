"""runtime state: muted_chats + chat_token_usage

Revision ID: 0001_runtime_state
Revises:
Create Date: 2026-05-21

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_runtime_state"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "muted_chats",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("muted_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "chat_id"),
    )
    op.create_table(
        "chat_token_usage",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("current_input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_cached_input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("spent_input_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("spent_cached_input_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("spent_output_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("spent_total_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "chat_id"),
    )


def downgrade() -> None:
    op.drop_table("chat_token_usage")
    op.drop_table("muted_chats")
