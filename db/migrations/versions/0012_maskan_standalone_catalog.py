"""maskan standalone: own catalogue, cemeteries, graves and orders

The tenant no longer reads the Maskan Django backend for what it sells or who
it sells to — those four tables move in here, so the bot works (and takes money)
with that backend down.

Revision ID: 0012_maskan_standalone_catalog
Revises: 0011_maskan_payments
Create Date: 2026-08-20

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012_maskan_standalone_catalog"
down_revision: Union[str, None] = "0011_maskan_payments"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "maskan_services",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("name_uz", sa.String(length=160), nullable=False),
        sa.Column("name_ru", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("desc_uz", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("desc_ru", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("price", sa.Integer(), nullable=False),
        sa.Column("sort", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_index("ix_maskan_services_code", "maskan_services", ["code"])

    op.create_table(
        "maskan_cemeteries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name_uz", sa.String(length=160), nullable=False),
        sa.Column("name_ru", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("city", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("district", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_maskan_cemeteries_name_uz", "maskan_cemeteries", ["name_uz"])

    op.create_table(
        "maskan_graves",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("cemetery_id", sa.Integer(), nullable=True),
        sa.Column("cemetery_label", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("relation", sa.String(length=60), nullable=False, server_default=""),
        sa.Column("born", sa.Integer(), nullable=True),
        sa.Column("died", sa.Integer(), nullable=True),
        sa.Column("sector", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_maskan_graves_chat_id", "maskan_graves", ["chat_id"])

    op.create_table(
        "maskan_orders",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("grave_id", sa.Integer(), nullable=True),
        sa.Column("grave_label", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("cemetery_label", sa.String(length=160), nullable=False, server_default=""),
        sa.Column(
            "items",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("frequency", sa.String(length=16), nullable=False, server_default="once"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("payment_id", sa.BigInteger(), nullable=True),
        sa.Column("caretaker", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_maskan_orders_chat_id", "maskan_orders", ["chat_id"])
    op.create_index("ix_maskan_orders_status", "maskan_orders", ["status"])
    op.create_index("ix_maskan_orders_payment_id", "maskan_orders", ["payment_id"])


def downgrade() -> None:
    op.drop_index("ix_maskan_orders_payment_id", table_name="maskan_orders")
    op.drop_index("ix_maskan_orders_status", table_name="maskan_orders")
    op.drop_index("ix_maskan_orders_chat_id", table_name="maskan_orders")
    op.drop_table("maskan_orders")
    op.drop_index("ix_maskan_graves_chat_id", table_name="maskan_graves")
    op.drop_table("maskan_graves")
    op.drop_index("ix_maskan_cemeteries_name_uz", table_name="maskan_cemeteries")
    op.drop_table("maskan_cemeteries")
    op.drop_index("ix_maskan_services_code", table_name="maskan_services")
    op.drop_table("maskan_services")
