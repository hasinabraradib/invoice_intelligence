"""partially_failed document status; jobs can resume an earlier job

Revision ID: e4b9f1c2a7d6
Revises: d7a3c5e9f2b1
Create Date: 2026-11-13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e4b9f1c2a7d6"
down_revision: str | None = "d7a3c5e9f2b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD = ("uploaded", "processing", "processed", "needs_review", "failed")
NEW = (*OLD, "partially_failed")


def _check(values: tuple[str, ...]) -> str:
    return "status IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    # The status column is a string with a CHECK constraint: widen the allowed values.
    op.drop_constraint("document_status", "documents", type_="check")
    op.create_check_constraint("document_status", "documents", _check(NEW))
    op.add_column("processing_jobs", sa.Column("resume_job_id", sa.Uuid(), nullable=True))


def downgrade() -> None:
    op.drop_column("processing_jobs", "resume_job_id")
    op.execute("UPDATE documents SET status = 'failed' WHERE status = 'partially_failed'")
    op.drop_constraint("document_status", "documents", type_="check")
    op.create_check_constraint("document_status", "documents", _check(OLD))
