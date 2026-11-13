"""Database tables (SQLAlchemy 2 typed models). See docs/schema.md for the diagram.

Status columns are short strings with CHECK constraints rather than Postgres ENUM types:
adding a status later is then a simple migration.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

EMBEDDING_DIM = 384  # size of the small sentence-embedding model planned for step 5
Money = Numeric(18, 2)  # exact decimal, never float (CLAUDE.md rule 1)


class DocumentStatus(StrEnum):
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    PROCESSED = "processed"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"
    # Some invoices of a batch failed for good, the others were extracted and kept.
    PARTIALLY_FAILED = "partially_failed"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


def _status(enum: type[StrEnum], name: str) -> Enum:
    return Enum(
        enum,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=20,
        values_callable=lambda members: [m.value for m in members],
    )


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(primary_key=True, default=uuid.uuid4)


def _now(**kwargs: Any) -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), **kwargs)


class Document(Base):
    """One uploaded file. sha256 is unique: the same bytes are stored only once."""

    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = _uuid_pk()
    filename: Mapped[str] = mapped_column(String(255))  # sanitised display name
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    mime_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    storage_path: Mapped[str] = mapped_column(Text)
    status: Mapped[DocumentStatus] = mapped_column(
        _status(DocumentStatus, "document_status"), default=DocumentStatus.UPLOADED
    )
    error: Mapped[str | None] = mapped_column(Text)
    # The job that last saved this document's page texts: a retry of that same job can
    # reuse them instead of parsing and OCR'ing a 1000-page file again.
    text_job_id: Mapped[uuid.UUID | None] = mapped_column()
    # For spreadsheets: per sheet, whether it is a ledger (a list of invoices) and how its
    # columns map to fields ({"sheets": {name: {...}}}). "source": auto or manual.
    ledger_mapping: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now(onupdate=func.now())

    __table_args__ = (Index("ix_documents_status", "status"),)


class ProcessingJob(Base):
    """One processing run of a document; `stage` shows where it is or where it failed."""

    __tablename__ = "processing_jobs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[JobStatus] = mapped_column(
        _status(JobStatus, "job_status"), default=JobStatus.QUEUED
    )
    stage: Mapped[str | None] = mapped_column(String(30))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    pages_done: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    pages_total: Mapped[int | None] = mapped_column(Integer)
    segments_done: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    segments_total: Mapped[int | None] = mapped_column(Integer)
    # The invoice boundaries this job found; a retry reuses them so it can resume.
    segments: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    # "Retry failed": continue from this earlier job (reuse its page texts, boundaries and
    # finished invoices) instead of starting over.
    resume_job_id: Mapped[uuid.UUID | None] = mapped_column()
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()


class Invoice(Base):
    """One invoice found in a document. A batch PDF has many (one per segment, numbered
    by segment_index); re-processing updates the rows whose page range is unchanged."""

    __tablename__ = "invoices"

    id: Mapped[uuid.UUID] = _uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    segment_index: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    page_start: Mapped[int | None] = mapped_column(Integer)
    page_end: Mapped[int | None] = mapped_column(Integer)
    # The job whose extraction is stored here (None = not extracted yet). A retry of the
    # same job skips segments it already finished.
    job_id: Mapped[uuid.UUID | None] = mapped_column()
    invoice_number: Mapped[str | None] = mapped_column(String(100))  # as printed
    # "INV-001", "inv 001" and "INV/001" all become "INV001" (duplicate detection).
    invoice_number_key: Mapped[str | None] = mapped_column(String(100))
    invoice_date: Mapped[date | None] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date)
    supplier_name: Mapped[str | None] = mapped_column(Text)
    supplier_key: Mapped[str | None] = mapped_column(String(255))
    customer_name: Mapped[str | None] = mapped_column(Text)
    currency: Mapped[str | None] = mapped_column(String(3))
    subtotal: Mapped[Decimal | None] = mapped_column(Money)
    tax: Mapped[Decimal | None] = mapped_column(Money)
    total: Mapped[Decimal | None] = mapped_column(Money)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    overall_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    # Per-field status, evidence, page, confidence and reasons (the "why" behind values).
    fields: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    raw_model_output: Mapped[list[Any]] = mapped_column(JSONB, default=list)
    extracted_at: Mapped[datetime] = _now()
    # Human approval. approved_error_keys = the error issues ("CODE:field") the reviewer
    # accepted; only an error NOT in this list re-opens the review later.
    reviewed_by: Mapped[str | None] = mapped_column(String(255))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_override_reason: Mapped[str | None] = mapped_column(Text)
    approved_error_keys: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default="[]")

    __table_args__ = (
        UniqueConstraint("document_id", "segment_index", name="uq_invoices_document_segment"),
        Index("ix_invoices_supplier_number_key", "supplier_key", "invoice_number_key"),
        Index("ix_invoices_supplier_date_total", "supplier_key", "invoice_date", "total"),
    )


class LineItem(Base):
    __tablename__ = "line_items"

    id: Mapped[uuid.UUID] = _uuid_pk()
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)  # 1-based order on the invoice
    description: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))  # 2.5 kg, 0.125 h
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))  # unit prices like 0.1250
    line_total: Mapped[Decimal | None] = mapped_column(Money)
    fields: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)

    __table_args__ = (UniqueConstraint("invoice_id", "position"),)


class ValidationIssue(Base):
    __tablename__ = "validation_issues"

    id: Mapped[uuid.UUID] = _uuid_pk()
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str] = mapped_column(String(50))
    severity: Mapped[str] = mapped_column(String(10))
    field: Mapped[str | None] = mapped_column(String(100))
    message: Mapped[str] = mapped_column(Text)
    expected: Mapped[str | None] = mapped_column(Text)
    actual: Mapped[str | None] = mapped_column(Text)
    # Set for issues raised by a cross-check: they are kept when validation re-runs and are
    # replaced only when that cross-check runs again.
    cross_check_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("cross_checks.id", ondelete="CASCADE"), index=True
    )


class Correction(Base):
    """Audit trail of human edits. Kept when a document is re-processed."""

    __tablename__ = "corrections"

    id: Mapped[uuid.UUID] = _uuid_pk()
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"), index=True
    )
    field: Mapped[str] = mapped_column(String(100))
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    corrected_by: Mapped[str] = mapped_column(String(255))
    corrected_at: Mapped[datetime] = _now()


class PageText(Base):
    """Text of each page (or sheet), for keyword search."""

    __tablename__ = "page_texts"

    id: Mapped[uuid.UUID] = _uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    page_number: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(20))  # text_layer | ocr | sheet
    text: Mapped[str] = mapped_column(Text)
    ocr_confidence: Mapped[float | None] = mapped_column(Float)
    # Postgres keeps this up to date itself. 'simple' = no language-specific stemming,
    # which works the same for English and Bengali text.
    search: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('simple', coalesce(text, ''))", persisted=True)
    )

    __table_args__ = (
        UniqueConstraint("document_id", "page_number"),
        Index("ix_page_texts_search", "search", postgresql_using="gin"),
    )


class TextChunk(Base):
    """~500-token pieces of one page with their embedding, for semantic search.
    A chunk never spans two pages, so every semantic hit has a page number."""

    __tablename__ = "text_chunks"

    id: Mapped[uuid.UUID] = _uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    page_number: Mapped[int] = mapped_column(Integer)
    chunk_index: Mapped[int] = mapped_column(Integer)  # 0, 1, 2... within the page
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))

    __table_args__ = (
        UniqueConstraint("document_id", "page_number", "chunk_index"),
        # HNSW = fast approximate nearest-neighbour index; cosine distance to match the
        # normalised sentence embeddings.
        Index(
            "ix_text_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class LedgerRow(Base):
    """One data row of a spreadsheet. For ledger sheets the mapped invoice fields are filled;
    `cells` keeps the raw row so a corrected column mapping can be applied without re-parsing."""

    __tablename__ = "ledger_rows"

    id: Mapped[uuid.UUID] = _uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    sheet_name: Mapped[str] = mapped_column(String(255))
    row_number: Mapped[int] = mapped_column(Integer)  # as shown in Excel (evidence)
    cells: Mapped[dict[str, Any]] = mapped_column(JSONB)
    invoice_number: Mapped[str | None] = mapped_column(String(100))
    invoice_number_key: Mapped[str | None] = mapped_column(String(100), index=True)
    invoice_date: Mapped[date | None] = mapped_column(Date)
    supplier_name: Mapped[str | None] = mapped_column(Text)
    supplier_key: Mapped[str | None] = mapped_column(String(255))
    total: Mapped[Decimal | None] = mapped_column(Money)
    tax: Mapped[Decimal | None] = mapped_column(Money)
    currency: Mapped[str | None] = mapped_column(String(3))
    problems: Mapped[list[str]] = mapped_column(JSONB, default=list)

    __table_args__ = (UniqueConstraint("document_id", "sheet_name", "row_number"),)


class CrossCheck(Base):
    """Comparison of two documents (invoices vs a ledger, or invoices vs invoices). One row
    per ordered pair: running it again replaces its findings (idempotent)."""

    __tablename__ = "cross_checks"

    id: Mapped[uuid.UUID] = _uuid_pk()
    document_a_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    document_b_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    mode: Mapped[str | None] = mapped_column(
        String(30)
    )  # invoices_vs_ledger | invoices_vs_invoices
    status: Mapped[JobStatus] = mapped_column(
        _status(JobStatus, "cross_check_status"), default=JobStatus.QUEUED
    )
    error: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # counts per kind
    created_at: Mapped[datetime] = _now()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (UniqueConstraint("document_a_id", "document_b_id"),)


class CrossCheckFinding(Base):
    """One result of a cross-check, with the evidence on each side (page or sheet row)."""

    __tablename__ = "cross_check_findings"

    id: Mapped[uuid.UUID] = _uuid_pk()
    cross_check_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cross_checks.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)  # display order
    kind: Mapped[str] = mapped_column(String(40))  # matched, amount_mismatch, missing_in_b...
    severity: Mapped[str] = mapped_column(String(10))  # info | warning | error
    field: Mapped[str | None] = mapped_column(String(100))
    value_a: Mapped[str | None] = mapped_column(Text)
    value_b: Mapped[str | None] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text)
    invoice_a_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("invoices.id", ondelete="SET NULL")
    )
    invoice_b_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("invoices.id", ondelete="SET NULL")
    )
    ledger_row_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ledger_rows.id", ondelete="SET NULL")
    )
    # Evidence kept as plain values too, so it survives re-processing of either document.
    page_a: Mapped[int | None] = mapped_column(Integer)
    page_b: Mapped[int | None] = mapped_column(Integer)
    sheet_b: Mapped[str | None] = mapped_column(String(255))
    row_b: Mapped[int | None] = mapped_column(Integer)
    segment_a: Mapped[int | None] = mapped_column(Integer)
    segment_b: Mapped[int | None] = mapped_column(Integer)
