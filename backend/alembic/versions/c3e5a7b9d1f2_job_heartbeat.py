"""processing_jobs.heartbeat_at: written while a job makes progress (watchdog)

Revision ID: c3e5a7b9d1f2
Revises: a9d2e4f6b8c1
Create Date: 2026-11-14
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c3e5a7b9d1f2"
down_revision: str | None = "a9d2e4f6b8c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "processing_jobs", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("processing_jobs", "heartbeat_at")
