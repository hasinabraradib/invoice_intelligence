"""processing_jobs: vision-fallback calls and tokens (part of the LLM totals)

Revision ID: d4f6b8c0e2a3
Revises: c3e5a7b9d1f2
Create Date: 2026-11-14
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d4f6b8c0e2a3"
down_revision: str | None = "c3e5a7b9d1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = ("llm_vision_calls", "llm_vision_input_tokens", "llm_vision_output_tokens")


def upgrade() -> None:
    for name in COLUMNS:
        op.add_column(
            "processing_jobs",
            sa.Column(name, sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    for name in COLUMNS:
        op.drop_column("processing_jobs", name)
