"""byd bitrix24 integration: deal/contact/stage refs on leads + task ref

Revision ID: 0008_byd_bitrix
Revises: 0007_anfa_doctors
Create Date: 2026-07-29

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008_byd_bitrix"
down_revision: Union[str, None] = "0007_anfa_doctors"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("byd_leads", sa.Column("bitrix_contact_id", sa.BigInteger(), nullable=True))
    op.add_column("byd_leads", sa.Column("bitrix_deal_id", sa.BigInteger(), nullable=True))
    op.add_column("byd_leads", sa.Column("bitrix_stage_id", sa.String(length=64), nullable=True))
    op.create_index("ix_byd_leads_bitrix_deal_id", "byd_leads", ["bitrix_deal_id"])

    op.add_column(
        "byd_operator_tasks", sa.Column("bitrix_task_id", sa.BigInteger(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("byd_operator_tasks", "bitrix_task_id")

    op.drop_index("ix_byd_leads_bitrix_deal_id", table_name="byd_leads")
    op.drop_column("byd_leads", "bitrix_stage_id")
    op.drop_column("byd_leads", "bitrix_deal_id")
    op.drop_column("byd_leads", "bitrix_contact_id")
