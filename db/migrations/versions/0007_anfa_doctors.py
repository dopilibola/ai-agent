"""anfa: add the doctor roster (reference data)

Adds anfa_doctors — the clinic's physician list (name, speciality, experience,
weekly reception schedule), imported from a Word export. Reference data only:
the agent names doctors and shows walk-in hours; there is no booking.

Revision ID: 0007_anfa_doctors
Revises: 0006_anfa_catalog
Create Date: 2026-07-02

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_anfa_doctors"
down_revision: Union[str, None] = "0006_anfa_catalog"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "anfa_doctors",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("fullname", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("speciality", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("experience", sa.Text(), nullable=False, server_default=""),
        sa.Column("schedule", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("hours_label", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("content_hash", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_anfa_doctors_speciality", "anfa_doctors", ["speciality"])


def downgrade() -> None:
    op.drop_index("ix_anfa_doctors_speciality", table_name="anfa_doctors")
    op.drop_table("anfa_doctors")
