"""byd funnel: leads + scheduled_tasks + programs + operator_tasks + voucher seq

Revision ID: 0005_byd_funnel
Revises: 0004_chat_profiles
Create Date: 2026-06-24

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_byd_funnel"
down_revision: Union[str, None] = "0004_chat_profiles"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "byd_leads",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False, server_default="byd"),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("current_stage", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("request", sa.Text(), nullable=False, server_default=""),
        sa.Column("city", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("phone", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("tg_username", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("program_code", sa.String(length=16), nullable=True),
        sa.Column("arrival_date", sa.Date(), nullable=True),
        sa.Column("date_of_birth", sa.Date(), nullable=True),
        sa.Column("total_amount", sa.BigInteger(), nullable=True),
        sa.Column("prepayment_amount", sa.BigInteger(), nullable=True),
        sa.Column("prepayment_received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("voucher_number", sa.Integer(), nullable=True),
        sa.Column("operator_first_action_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_call_outcome", sa.String(length=32), nullable=True),
        sa.Column("close_reason", sa.String(length=255), nullable=True),
        sa.Column("stage_entered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_byd_leads_tenant_id", "byd_leads", ["tenant_id"])
    op.create_index("ix_byd_leads_chat_id", "byd_leads", ["chat_id"])
    op.create_index("ix_byd_leads_current_stage", "byd_leads", ["current_stage"])
    op.create_index("ix_byd_leads_status", "byd_leads", ["status"])

    op.create_table(
        "byd_scheduled_tasks",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False, server_default="byd"),
        sa.Column("lead_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("action_type", sa.String(length=64), nullable=False),
        sa.Column("stage", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("payload", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dedup_key", sa.String(length=255), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedup_key", name="uq_byd_scheduled_tasks_dedup_key"),
    )
    op.create_index("ix_byd_scheduled_tasks_tenant_id", "byd_scheduled_tasks", ["tenant_id"])
    op.create_index("ix_byd_scheduled_tasks_lead_id", "byd_scheduled_tasks", ["lead_id"])
    op.create_index("ix_byd_scheduled_tasks_chat_id", "byd_scheduled_tasks", ["chat_id"])
    op.create_index("ix_byd_scheduled_tasks_action_type", "byd_scheduled_tasks", ["action_type"])
    op.create_index("ix_byd_scheduled_tasks_status", "byd_scheduled_tasks", ["status"])
    op.create_index("ix_byd_scheduled_tasks_scheduled_for", "byd_scheduled_tasks", ["scheduled_for"])
    # The poller's hot query: pending rows whose time has come, oldest first.
    op.create_index(
        "ix_byd_scheduled_tasks_due",
        "byd_scheduled_tasks",
        ["status", "scheduled_for"],
    )

    op.create_table(
        "byd_programs",
        sa.Column("code", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("price", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("code"),
    )

    op.create_table(
        "byd_operator_tasks",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False, server_default="byd"),
        sa.Column("lead_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="open"),
        sa.Column("checklist", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_byd_operator_tasks_tenant_id", "byd_operator_tasks", ["tenant_id"])
    op.create_index("ix_byd_operator_tasks_lead_id", "byd_operator_tasks", ["lead_id"])
    op.create_index("ix_byd_operator_tasks_chat_id", "byd_operator_tasks", ["chat_id"])
    op.create_index("ix_byd_operator_tasks_kind", "byd_operator_tasks", ["kind"])
    op.create_index("ix_byd_operator_tasks_status", "byd_operator_tasks", ["status"])

    # Concurrency-safe voucher numbering (Stage 6). A Postgres SEQUENCE avoids
    # the duplicate-number race a counter column would risk.
    op.execute("CREATE SEQUENCE IF NOT EXISTS byd_voucher_seq START WITH 1000")


def downgrade() -> None:
    op.execute("DROP SEQUENCE IF EXISTS byd_voucher_seq")
    op.drop_index("ix_byd_operator_tasks_status", table_name="byd_operator_tasks")
    op.drop_index("ix_byd_operator_tasks_kind", table_name="byd_operator_tasks")
    op.drop_index("ix_byd_operator_tasks_chat_id", table_name="byd_operator_tasks")
    op.drop_index("ix_byd_operator_tasks_lead_id", table_name="byd_operator_tasks")
    op.drop_index("ix_byd_operator_tasks_tenant_id", table_name="byd_operator_tasks")
    op.drop_table("byd_operator_tasks")

    op.drop_table("byd_programs")

    op.drop_index("ix_byd_scheduled_tasks_due", table_name="byd_scheduled_tasks")
    op.drop_index("ix_byd_scheduled_tasks_scheduled_for", table_name="byd_scheduled_tasks")
    op.drop_index("ix_byd_scheduled_tasks_status", table_name="byd_scheduled_tasks")
    op.drop_index("ix_byd_scheduled_tasks_action_type", table_name="byd_scheduled_tasks")
    op.drop_index("ix_byd_scheduled_tasks_chat_id", table_name="byd_scheduled_tasks")
    op.drop_index("ix_byd_scheduled_tasks_lead_id", table_name="byd_scheduled_tasks")
    op.drop_index("ix_byd_scheduled_tasks_tenant_id", table_name="byd_scheduled_tasks")
    op.drop_table("byd_scheduled_tasks")

    op.drop_index("ix_byd_leads_status", table_name="byd_leads")
    op.drop_index("ix_byd_leads_current_stage", table_name="byd_leads")
    op.drop_index("ix_byd_leads_chat_id", table_name="byd_leads")
    op.drop_index("ix_byd_leads_tenant_id", table_name="byd_leads")
    op.drop_table("byd_leads")
