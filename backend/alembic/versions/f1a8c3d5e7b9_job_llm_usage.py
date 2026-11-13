"""LLM usage per processing job (calls and tokens, for cost)

Revision ID: f1a8c3d5e7b9
Revises: 34f22bbb314f
Create Date: 2026-11-14
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f1a8c3d5e7b9"
down_revision: str | None = "34f22bbb314f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = ("llm_calls", "llm_input_tokens", "llm_output_tokens", "llm_cached_input_tokens")


def upgrade() -> None:
    for name in COLUMNS:
        op.add_column(
            "processing_jobs",
            sa.Column(name, sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    for name in COLUMNS:
        op.drop_column("processing_jobs", name)
