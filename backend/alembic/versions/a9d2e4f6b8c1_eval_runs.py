"""eval_runs: latest evaluation numbers for the dashboard

Revision ID: a9d2e4f6b8c1
Revises: f1a8c3d5e7b9
Create Date: 2026-11-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a9d2e4f6b8c1"
down_revision: str | None = "f1a8c3d5e7b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "eval_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dataset", sa.String(50), nullable=False),
        sa.Column("split", sa.String(20), nullable=False),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("results_file", sa.String(255), nullable=False),
        sa.Column("headline", postgresql.JSONB(), nullable=False),
    )
    op.create_index("ix_eval_runs_created_at", "eval_runs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_eval_runs_created_at", table_name="eval_runs")
    op.drop_table("eval_runs")
