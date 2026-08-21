"""maskan_payments: invoices paid into the operator's own Payme/Uzum account

Revision ID: 0011_maskan_payments
Revises: 0010_conversation_events
Create Date: 2026-08-20

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_maskan_payments"
down_revision: Union[str, None] = "0010_conversation_events"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "maskan_payments",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("lead_id", sa.BigInteger(), nullable=True),
        sa.Column("order_id", sa.BigInteger(), nullable=True),
        sa.Column("amount_tiyin", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("provider", sa.String(length=16), nullable=True),
        sa.Column("provider_txn_id", sa.String(length=64), nullable=True),
        sa.Column("create_time", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("perform_time", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("cancel_time", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("reason", sa.Integer(), nullable=True),
        sa.Column(
            "detail",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("notified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_maskan_payments_tenant_id", "maskan_payments", ["tenant_id"])
    op.create_index("ix_maskan_payments_chat_id", "maskan_payments", ["chat_id"])
    op.create_index("ix_maskan_payments_order_id", "maskan_payments", ["order_id"])
    op.create_index("ix_maskan_payments_state_col", "maskan_payments", ["state"])
    op.create_index(
        "ix_maskan_payments_provider_txn_id", "maskan_payments", ["provider_txn_id"]
    )
    op.create_index(
        "ix_maskan_payments_state", "maskan_payments", ["state", "notified_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_maskan_payments_state", table_name="maskan_payments")
    op.drop_index("ix_maskan_payments_provider_txn_id", table_name="maskan_payments")
    op.drop_index("ix_maskan_payments_state_col", table_name="maskan_payments")
    op.drop_index("ix_maskan_payments_order_id", table_name="maskan_payments")
    op.drop_index("ix_maskan_payments_chat_id", table_name="maskan_payments")
    op.drop_index("ix_maskan_payments_tenant_id", table_name="maskan_payments")
    op.drop_table("maskan_payments")
