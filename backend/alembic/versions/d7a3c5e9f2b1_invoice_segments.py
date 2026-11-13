"""invoice segments: many invoices per document, resumable jobs

Revision ID: d7a3c5e9f2b1
Revises: b2f8e6d4c1a3
Create Date: 2026-11-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d7a3c5e9f2b1"
down_revision: str | None = "b2f8e6d4c1a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("invoices", sa.Column("segment_index", sa.Integer(), server_default="0",
                                        nullable=False))
    op.add_column("invoices", sa.Column("page_start", sa.Integer(), nullable=True))
    op.add_column("invoices", sa.Column("page_end", sa.Integer(), nullable=True))
    op.add_column("invoices", sa.Column("job_id", sa.Uuid(), nullable=True))
    # Existing invoices covered their whole document.
    op.execute(
        "UPDATE invoices SET page_start = 1, page_end = GREATEST(d.page_count, 1) "
        "FROM documents d WHERE d.id = invoices.document_id"
    )
    # One invoice per document -> one per (document, segment).
    op.drop_constraint("invoices_document_id_key", "invoices", type_="unique")
    op.create_unique_constraint("uq_invoices_document_segment", "invoices",
                                ["document_id", "segment_index"])
    op.create_index("ix_invoices_document_id", "invoices", ["document_id"])

    op.add_column("processing_jobs", sa.Column("segments_done", sa.Integer(), server_default="0",
                                               nullable=False))
    op.add_column("processing_jobs", sa.Column("segments_total", sa.Integer(), nullable=True))
    op.add_column("processing_jobs", sa.Column("segments", postgresql.JSONB(), nullable=True))
    op.add_column("documents", sa.Column("text_job_id", sa.Uuid(), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "text_job_id")
    for column in ("segments", "segments_total", "segments_done"):
        op.drop_column("processing_jobs", column)
    # Keep only the first invoice of each document so the old unique rule can return.
    op.execute("DELETE FROM invoices WHERE segment_index > 0")
    op.drop_index("ix_invoices_document_id", table_name="invoices")
    op.drop_constraint("uq_invoices_document_segment", "invoices", type_="unique")
    op.create_unique_constraint("invoices_document_id_key", "invoices", ["document_id"])
    for column in ("job_id", "page_end", "page_start", "segment_index"):
        op.drop_column("invoices", column)
