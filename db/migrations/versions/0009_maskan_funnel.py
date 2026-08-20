"""maskan funnel: leads + scheduled_tasks

Revision ID: 0009_maskan_funnel
Revises: 0008_byd_bitrix
Create Date: 2026-08-17

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_maskan_funnel"
down_revision: Union[str, None] = "0008_byd_bitrix"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "maskan_leads",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False, server_default="maskan"),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("current_stage", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("name", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("phone", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("tg_username", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("request", sa.Text(), nullable=False, server_default=""),
        # Handles into the Maskan Django backend (the source of truth).
        sa.Column("django_user_id", sa.BigInteger(), nullable=True),
        sa.Column("django_grave_id", sa.BigInteger(), nullable=True),
        sa.Column("django_order_id", sa.BigInteger(), nullable=True),
        sa.Column("grave_label", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("cemetery_label", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("order_total", sa.BigInteger(), nullable=True),
        sa.Column("order_frequency", sa.String(length=16), nullable=False, server_default="once"),
        sa.Column(
            "service_codes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        # What the watcher has already reacted to — the idempotency anchor.
        sa.Column("last_order_status", sa.String(length=24), nullable=False, server_default=""),
        sa.Column("last_payment_status", sa.String(length=24), nullable=False, server_default=""),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("close_reason", sa.String(length=255), nullable=True),
        sa.Column("stage_entered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_maskan_leads_tenant_id", "maskan_leads", ["tenant_id"])
    op.create_index("ix_maskan_leads_chat_id", "maskan_leads", ["chat_id"])
    op.create_index("ix_maskan_leads_current_stage", "maskan_leads", ["current_stage"])
    op.create_index("ix_maskan_leads_status", "maskan_leads", ["status"])
    op.create_index("ix_maskan_leads_django_order_id", "maskan_leads", ["django_order_id"])

    op.create_table(
        "maskan_scheduled_tasks",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False, server_default="maskan"),
        sa.Column("lead_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("action_type", sa.String(length=64), nullable=False),
        sa.Column("stage", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dedup_key", sa.String(length=255), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        # Idempotent enqueue: ON CONFLICT (dedup_key) resets the row's timer.
        sa.UniqueConstraint("dedup_key", name="uq_maskan_scheduled_tasks_dedup_key"),
    )
    op.create_index("ix_maskan_scheduled_tasks_tenant_id", "maskan_scheduled_tasks", ["tenant_id"])
    op.create_index("ix_maskan_scheduled_tasks_lead_id", "maskan_scheduled_tasks", ["lead_id"])
    op.create_index("ix_maskan_scheduled_tasks_chat_id", "maskan_scheduled_tasks", ["chat_id"])
    op.create_index(
        "ix_maskan_scheduled_tasks_action_type", "maskan_scheduled_tasks", ["action_type"]
    )
    op.create_index("ix_maskan_scheduled_tasks_status", "maskan_scheduled_tasks", ["status"])
    op.create_index(
        "ix_maskan_scheduled_tasks_scheduled_for", "maskan_scheduled_tasks", ["scheduled_for"]
    )
    # The poller's hot query: pending rows whose time has come, oldest first.
    op.create_index(
        "ix_maskan_scheduled_tasks_due",
        "maskan_scheduled_tasks",
        ["status", "scheduled_for"],
    )


def downgrade() -> None:
    op.drop_index("ix_maskan_scheduled_tasks_due", table_name="maskan_scheduled_tasks")
    op.drop_index("ix_maskan_scheduled_tasks_scheduled_for", table_name="maskan_scheduled_tasks")
    op.drop_index("ix_maskan_scheduled_tasks_status", table_name="maskan_scheduled_tasks")
    op.drop_index("ix_maskan_scheduled_tasks_action_type", table_name="maskan_scheduled_tasks")
    op.drop_index("ix_maskan_scheduled_tasks_chat_id", table_name="maskan_scheduled_tasks")
    op.drop_index("ix_maskan_scheduled_tasks_lead_id", table_name="maskan_scheduled_tasks")
    op.drop_index("ix_maskan_scheduled_tasks_tenant_id", table_name="maskan_scheduled_tasks")
    op.drop_table("maskan_scheduled_tasks")

    op.drop_index("ix_maskan_leads_django_order_id", table_name="maskan_leads")
    op.drop_index("ix_maskan_leads_status", table_name="maskan_leads")
    op.drop_index("ix_maskan_leads_current_stage", table_name="maskan_leads")
    op.drop_index("ix_maskan_leads_chat_id", table_name="maskan_leads")
    op.drop_index("ix_maskan_leads_tenant_id", table_name="maskan_leads")
    op.drop_table("maskan_leads")
