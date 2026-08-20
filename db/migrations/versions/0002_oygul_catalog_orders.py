"""oygul CRM: oygul_bouquets + oygul_orders

Revision ID: 0002_oygul_catalog_orders
Revises: 0001_runtime_state
Create Date: 2026-05-21

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_oygul_catalog_orders"
down_revision: Union[str, None] = "0001_runtime_state"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "oygul_bouquets",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("branch_id", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("tags", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("products_spent", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("photo_url", sa.Text(), nullable=False, server_default=""),
        sa.Column("price", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "oygul_orders",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("customer_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("customer_username", sa.String(length=255), nullable=True),
        sa.Column("bouquet_name", sa.String(length=255), nullable=False),
        sa.Column("bouquet_photo_url", sa.Text(), nullable=False, server_default=""),
        sa.Column("bouquet_price_sum", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("delivery_fee_sum", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("recipient_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("recipient_phone", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("address", sa.Text(), nullable=False, server_default=""),
        sa.Column("delivery_time", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("card_text", sa.Text(), nullable=True),
        sa.Column("is_surprise", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("extra_notes", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_oygul_orders_chat_id", "oygul_orders", ["chat_id"])
    op.create_index("ix_oygul_orders_status", "oygul_orders", ["status"])


def downgrade() -> None:
    op.drop_index("ix_oygul_orders_status", table_name="oygul_orders")
    op.drop_index("ix_oygul_orders_chat_id", table_name="oygul_orders")
    op.drop_table("oygul_orders")
    op.drop_table("oygul_bouquets")
