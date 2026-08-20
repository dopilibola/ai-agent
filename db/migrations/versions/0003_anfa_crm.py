"""anfa CRM: anfa_resources + anfa_services + anfa_visits

Revision ID: 0003_anfa_crm
Revises: 0002_oygul_catalog_orders
Create Date: 2026-05-21

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_anfa_crm"
down_revision: Union[str, None] = "0002_oygul_catalog_orders"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "anfa_resources",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("fullname", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("schedule", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "anfa_services",
        sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("category", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("resource_ids", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("price_min", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("price_max", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="UZS"),
        sa.Column("duration_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "anfa_visits",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("resource_id", sa.Integer(), nullable=False),
        sa.Column("service_ids", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("client_name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("client_phone", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("client_age", sa.Integer(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="confirmed"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_anfa_visits_resource_id", "anfa_visits", ["resource_id"])
    op.create_index("ix_anfa_visits_scheduled_at", "anfa_visits", ["scheduled_at"])
    op.create_index("ix_anfa_visits_status", "anfa_visits", ["status"])


def downgrade() -> None:
    op.drop_index("ix_anfa_visits_status", table_name="anfa_visits")
    op.drop_index("ix_anfa_visits_scheduled_at", table_name="anfa_visits")
    op.drop_index("ix_anfa_visits_resource_id", table_name="anfa_visits")
    op.drop_table("anfa_visits")
    op.drop_table("anfa_services")
    op.drop_table("anfa_resources")
