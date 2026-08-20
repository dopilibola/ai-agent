"""chat_profiles: cached Telegram name/username per chat

Revision ID: 0004_chat_profiles
Revises: 0003_anfa_crm
Create Date: 2026-05-21

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_chat_profiles"
down_revision: Union[str, None] = "0003_anfa_crm"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chat_profiles",
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "chat_id"),
    )


def downgrade() -> None:
    op.drop_table("chat_profiles")
