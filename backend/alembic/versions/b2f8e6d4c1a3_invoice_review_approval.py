"""invoice review approval columns

Revision ID: b2f8e6d4c1a3
Revises: 9c4e1a7b2d55
Create Date: 2026-11-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "b2f8e6d4c1a3"
down_revision: str | None = "9c4e1a7b2d55"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("invoices", sa.Column("reviewed_by", sa.String(255), nullable=True))
    op.add_column("invoices", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("invoices", sa.Column("review_override_reason", sa.Text(), nullable=True))
    op.add_column("invoices", sa.Column(
        "approved_error_keys", postgresql.JSONB(), server_default="[]", nullable=False))


def downgrade() -> None:
    for column in ("approved_error_keys", "review_override_reason", "reviewed_at",
                   "reviewed_by"):
        op.drop_column("invoices", column)
