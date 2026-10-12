"""drop the oygul, anfa and byd tenants: their tables and their shared-table rows

Revision ID: 0015_drop_removed_tenants
Revises: 0014_panel_settings
Create Date: 2026-10-12

The three tenants were removed from the codebase (apps/, scripts/, both admin
panels); only maskan ships. This drops the tables they owned and deletes the
rows they left in the tenant-scoped shared tables, including their LangGraph
conversation checkpoints (thread_id is ``tenant:channel:chat_id``), so the
panels list nothing but maskan.

One-way: the data is gone, so there is no downgrade.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015_drop_removed_tenants"
down_revision: Union[str, None] = "0014_panel_settings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

REMOVED_TENANTS = ("oygul", "anfa", "byd")

TENANT_TABLES = (
    "oygul_orders",
    "oygul_bouquets",
    "anfa_catalog",
    "anfa_doctors",
    "byd_operator_tasks",
    "byd_scheduled_tasks",
    "byd_programs",
    "byd_leads",
)

# Shared tables keyed by a `tenant_id` column.
SHARED_TABLES = (
    "muted_chats",
    "chat_token_usage",
    "chat_profiles",
    "conversation_events",
    "learning_examples",
    "panel_settings",
)

# LangGraph's own tables (created by the saver, not by Alembic), keyed by thread_id.
CHECKPOINT_TABLES = ("checkpoint_writes", "checkpoint_blobs", "checkpoints")


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(sa.inspect(bind).get_table_names())

    for table in TENANT_TABLES:
        op.execute(f'DROP TABLE IF EXISTS "{table}"')
    op.execute("DROP SEQUENCE IF EXISTS byd_voucher_seq")

    for table in SHARED_TABLES:
        if table in existing:
            bind.execute(
                sa.text(f'DELETE FROM "{table}" WHERE tenant_id = ANY(:tenants)'),
                {"tenants": list(REMOVED_TENANTS)},
            )

    for table in CHECKPOINT_TABLES:
        if table in existing:
            for tenant in REMOVED_TENANTS:
                bind.execute(
                    sa.text(f'DELETE FROM "{table}" WHERE thread_id LIKE :prefix'),
                    {"prefix": f"{tenant}:%"},
                )


def downgrade() -> None:
    raise NotImplementedError(
        "0015 deleted the oygul/anfa/byd data; restore from a backup instead."
    )
