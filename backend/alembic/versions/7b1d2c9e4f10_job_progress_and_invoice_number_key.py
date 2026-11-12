"""job progress columns and normalised invoice number key

Revision ID: 7b1d2c9e4f10
Revises: 43585e396d7f
Create Date: 2026-11-13
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "7b1d2c9e4f10"
down_revision: str | None = "43585e396d7f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Progress shown while a job runs (e.g. OCR page 12 of 40).
    op.add_column("processing_jobs",
                  sa.Column("pages_done", sa.Integer(), server_default="0", nullable=False))
    op.add_column("processing_jobs", sa.Column("pages_total", sa.Integer(), nullable=True))

    # Normalised invoice number for duplicate checks; backfill rows that already exist
    # with the same rule as app.extraction.postprocess.normalise_invoice_number.
    op.add_column("invoices", sa.Column("invoice_number_key", sa.String(100), nullable=True))
    op.execute(
        "UPDATE invoices SET invoice_number_key = "
        "NULLIF(regexp_replace(upper(invoice_number), '[[:space:]/.-]', '', 'g'), '')"
    )
    op.drop_index("ix_invoices_supplier_number", table_name="invoices")
    op.create_index("ix_invoices_supplier_number_key", "invoices",
                    ["supplier_key", "invoice_number_key"])


def downgrade() -> None:
    op.drop_index("ix_invoices_supplier_number_key", table_name="invoices")
    op.create_index("ix_invoices_supplier_number", "invoices", ["supplier_key", "invoice_number"])
    op.drop_column("invoices", "invoice_number_key")
    op.drop_column("processing_jobs", "pages_total")
    op.drop_column("processing_jobs", "pages_done")
