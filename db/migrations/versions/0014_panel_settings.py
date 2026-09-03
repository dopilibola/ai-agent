"""panel_settings: operator-editable knobs the bots read live

Revision ID: 0014_panel_settings
Revises: 0013_learning_examples
Create Date: 2026-09-04

The admin panel already edits the prompts on disk and the bots re-read them on
every invoke. Outreach pacing needs the same shape but has no file: whether the
funnel may write first at all, the hours it may write in, how many touches a
chat may get in a day and how far apart. Those live here — one JSON row per
`(tenant_id, key)` — and `db/settings.py` reads them with a short TTL cache, so
a switch flipped in the panel reaches the running scheduler in seconds.

Deliberately a generic key/value table rather than a column per knob: the panel
is the only writer and the value is read as a whole dict, so adding a knob is a
form field, not a migration.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014_panel_settings"
down_revision: Union[str, None] = "0013_learning_examples"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "panel_settings",
        # '' is the all-tenants default row; a tenant row overrides it.
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column(
            "value",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "key"),
    )


def downgrade() -> None:
    op.drop_table("panel_settings")
