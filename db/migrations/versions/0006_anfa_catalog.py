"""anfa: replace the booking CRM with a flat service catalog

Drops anfa_resources + anfa_services + anfa_visits (the old online-booking CRM)
and creates anfa_catalog — a flat priced service list fed by the clinic's Excel
export. The clinic now registers visits offline; the agent only advises on
services + prices.

Revision ID: 0006_anfa_catalog
Revises: 0005_byd_funnel
Create Date: 2026-06-29

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_anfa_catalog"
down_revision: Union[str, None] = "0005_byd_funnel"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("ix_anfa_visits_status", table_name="anfa_visits")
    op.drop_index("ix_anfa_visits_scheduled_at", table_name="anfa_visits")
    op.drop_index("ix_anfa_visits_resource_id", table_name="anfa_visits")
    op.drop_table("anfa_visits")
    op.drop_table("anfa_services")
    op.drop_table("anfa_resources")

    op.create_table(
        "anfa_catalog",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("tab", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("category", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("price", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="UZS"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("content_hash", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_anfa_catalog_tab", "anfa_catalog", ["tab"])
    op.create_index("ix_anfa_catalog_category", "anfa_catalog", ["category"])


def downgrade() -> None:
    op.drop_index("ix_anfa_catalog_category", table_name="anfa_catalog")
    op.drop_index("ix_anfa_catalog_tab", table_name="anfa_catalog")
    op.drop_table("anfa_catalog")

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
