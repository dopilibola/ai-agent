"""learning_examples: approved operator answers the agents retrieve from

Revision ID: 0013_learning_examples
Revises: 0012_maskan_standalone_catalog
Create Date: 2026-08-21

A handoff reply written by a human is the answer the agent should have given.
Approved ones are stored here and pulled back into the prompt at answer time,
which is how the agent improves without anyone touching the model: an operator
approves, the next similar question is answered better.

Retrieval works two ways so the table is useful from day one:
  * `embedding` (pgvector) when an embedding provider is configured;
  * a `pg_trgm` index on `question` otherwise — weaker, but no dependency and no
    cost, and Uzbek typos are exactly what trigram similarity is good at.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013_learning_examples"
down_revision: Union[str, None] = "0012_maskan_standalone_catalog"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("create extension if not exists pg_trgm")
    op.execute("create extension if not exists vector")

    op.create_table(
        "learning_examples",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        # What the customer asked (the last user turn) — the retrieval key.
        sa.Column("question", sa.Text(), nullable=False),
        # The turns leading up to it, for a human reviewing the pair.
        sa.Column("context", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        # What the agent said (or "" if it went straight to handoff).
        sa.Column("ai_attempt", sa.Text(), nullable=True),
        # What the human wrote instead — the thing worth learning.
        sa.Column("human_reply", sa.Text(), nullable=False),
        sa.Column("stage", sa.String(length=32), nullable=True),
        sa.Column("category", sa.String(length=64), nullable=True),
        # null = not reviewed, true = use it, false = rejected (kept, so the same
        # pair is not re-proposed on every import).
        sa.Column("approved", sa.Boolean(), nullable=True),
        sa.Column("source_thread_id", sa.String(length=255), nullable=True),
        sa.Column("source_event_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        # One row per operator message: re-running the import is idempotent.
        sa.UniqueConstraint("source_event_id", name="uq_learning_examples_event"),
    )
    op.create_index(
        "ix_learning_examples_lookup", "learning_examples",
        ["tenant_id", "approved"],
    )
    op.execute(
        "create index ix_learning_examples_question_trgm on learning_examples "
        "using gin (question gin_trgm_ops)"
    )
    # 768 dims = Gemini's text-embedding-004. Nullable: rows are usable before
    # anything is embedded.
    op.execute("alter table learning_examples add column embedding vector(768)")


def downgrade() -> None:
    op.drop_index("ix_learning_examples_question_trgm", table_name="learning_examples")
    op.drop_index("ix_learning_examples_lookup", table_name="learning_examples")
    op.drop_table("learning_examples")
