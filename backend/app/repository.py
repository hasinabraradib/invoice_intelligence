"""All database reads/writes, as typed methods. Callers own the transaction (commit).

Never logs document text (CLAUDE.md rule 5).
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.extraction.postprocess import normalise_invoice_number
from app.extraction.prompt import SourcePage
from app.extraction.service import ExtractionResult, FieldResult
from app.models import (
    Correction,
    Document,
    DocumentStatus,
    Invoice,
    JobStatus,
    LineItem,
    PageText,
    ProcessingJob,
    ValidationIssue,
)
from app.validation.rules import ValidationIssue as Issue

HEADER_COLUMNS = (
    "invoice_number",
    "invoice_date",
    "due_date",
    "supplier_name",
    "customer_name",
    "currency",
    "subtotal",
    "tax",
    "total",
)


def _now() -> datetime:
    return datetime.now(UTC)


def _clean(text: str | None) -> str | None:
    # Postgres TEXT/JSONB cannot store NUL characters, which some PDFs contain.
    return text.replace("\x00", "") if text else text


def _jsonable(value: Any) -> Any:
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


class Repository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ------------------------------------------------------------ documents

    async def get_document(self, document_id: uuid.UUID) -> Document | None:
        return await self.session.get(Document, document_id)

    async def get_document_by_sha256(self, sha256: str) -> Document | None:
        return await self.session.scalar(select(Document).where(Document.sha256 == sha256))

    async def find_document_id_by_sha256(self, sha256: str) -> uuid.UUID | None:
        return await self.session.scalar(select(Document.id).where(Document.sha256 == sha256))

    async def create_document(
        self,
        *,
        filename: str,
        sha256: str,
        mime_type: str,
        size_bytes: int,
        storage_path: str,
        page_count: int = 0,
    ) -> Document:
        document = Document(
            filename=filename,
            sha256=sha256,
            mime_type=mime_type,
            size_bytes=size_bytes,
            storage_path=storage_path,
            page_count=page_count,
            status=DocumentStatus.UPLOADED,
        )
        self.session.add(document)
        await self.session.flush()  # sends the INSERT now, so unique errors surface here
        return document

    async def set_document_status(
        self,
        document_id: uuid.UUID,
        status: DocumentStatus,
        error: str | None = None,
        page_count: int | None = None,
    ) -> None:
        values: dict[str, Any] = {"status": status, "error": error, "updated_at": _now()}
        if page_count is not None:
            values["page_count"] = page_count
        await self.session.execute(
            update(Document).where(Document.id == document_id).values(**values)
        )

    # ------------------------------------------------------------ jobs

    async def start_job(self, document_id: uuid.UUID) -> uuid.UUID:
        job = ProcessingJob(
            document_id=document_id, status=JobStatus.RUNNING, attempts=1, started_at=_now()
        )
        self.session.add(job)
        await self.session.flush()
        return job.id

    async def update_job(self, job_id: uuid.UUID, **values: Any) -> None:
        await self.session.execute(
            update(ProcessingJob).where(ProcessingJob.id == job_id).values(**values)
        )

    async def finish_job(
        self, job_id: uuid.UUID, *, error: str | None = None, note: str | None = None
    ) -> None:
        """error -> job failed; note -> job succeeded with a warning message."""
        await self.update_job(
            job_id,
            status=JobStatus.FAILED if error else JobStatus.SUCCEEDED,
            error=error or note,
            finished_at=_now(),
        )

    # ------------------------------------------------------------ invoices

    async def get_invoices(self, document_id: uuid.UUID) -> list[Invoice]:
        """All invoices found in a document, in page order."""
        rows = await self.session.scalars(
            select(Invoice)
            .where(Invoice.document_id == document_id)
            .order_by(Invoice.segment_index)
        )
        return list(rows)

    async def invoices_for_documents(
        self, document_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, list[Invoice]]:
        """Invoices of many documents in one query (for list pages)."""
        found: dict[uuid.UUID, list[Invoice]] = {i: [] for i in document_ids}
        if document_ids:
            rows = await self.session.scalars(
                select(Invoice)
                .where(Invoice.document_id.in_(document_ids))
                .order_by(Invoice.document_id, Invoice.segment_index)
            )
            for invoice in rows:
                found[invoice.document_id].append(invoice)
        return found

    # ------------------------------------------------------------ page texts

    async def save_page_texts(
        self,
        document_id: uuid.UUID,
        pages: list[SourcePage],
        *,
        page_count: int,
        job_id: uuid.UUID,
    ) -> None:
        """Replace the document's page texts (idempotent) and remember which job wrote them."""
        await self.session.execute(delete(PageText).where(PageText.document_id == document_id))
        for page in _merge_split_pages(pages):
            self.session.add(
                PageText(
                    document_id=document_id,
                    page_number=page.number,
                    source=page.source,
                    text=_clean(page.text) or "",
                    ocr_confidence=page.ocr_confidence,
                )
            )
        await self.session.execute(
            update(Document)
            .where(Document.id == document_id)
            .values(page_count=page_count, text_job_id=job_id)
        )
        await self.session.flush()

    async def load_source_pages(self, document_id: uuid.UUID) -> list[SourcePage]:
        """Page texts saved earlier, as SourcePages (used when a retry resumes)."""
        return [
            SourcePage(
                p.page_number,
                p.text,
                label=f"sheet {p.page_number}" if p.source == "sheet" else "",
                ocr_confidence=p.ocr_confidence,
                source=p.source,
            )
            for p in await self.get_page_texts(document_id)
        ]

    # ------------------------------------------------------------ segments

    async def save_segment(
        self,
        invoice: Invoice,
        *,
        result: ExtractionResult,
        issues: list[Issue],
        needs_review: bool,
        overall_confidence: float,
        job_id: uuid.UUID,
    ) -> Invoice:
        """Write one segment's extraction into its invoice row.

        Idempotent: the row is UPDATED in place (same id, so corrections pointing at it
        survive); its line items and issues are deleted and re-inserted. Caller commits.
        """
        for column in HEADER_COLUMNS:
            value = result.fields[column].value if column in result.fields else None
            setattr(invoice, column, _clean(value) if isinstance(value, str) else value)
        invoice.supplier_key = result.supplier_key
        invoice.invoice_number_key = normalise_invoice_number(invoice.invoice_number)
        invoice.needs_review = needs_review
        invoice.overall_confidence = overall_confidence
        invoice.fields = _jsonable({n: _field_json(f) for n, f in result.fields.items()})
        invoice.raw_model_output = _jsonable(result.raw_outputs)
        invoice.extracted_at = _now()
        invoice.job_id = job_id
        await self.session.execute(delete(LineItem).where(LineItem.invoice_id == invoice.id))
        await self.replace_issues(invoice.id, issues)
        for position, item in enumerate(result.line_items, start=1):
            self.session.add(
                LineItem(
                    invoice_id=invoice.id,
                    position=position,
                    description=_clean(item.description.value),
                    quantity=item.quantity.value,
                    unit_price=item.unit_price.value,
                    line_total=item.line_total.value,
                    fields=_jsonable({n: _field_json(f) for n, f in item}),
                )
            )
        await self.session.flush()
        return invoice

    async def get_invoice(self, invoice_id: uuid.UUID) -> Invoice | None:
        return await self.session.get(Invoice, invoice_id)

    async def get_line_items(self, invoice_id: uuid.UUID) -> list[LineItem]:
        rows = await self.session.scalars(
            select(LineItem).where(LineItem.invoice_id == invoice_id).order_by(LineItem.position)
        )
        return list(rows)

    async def get_issues(self, invoice_id: uuid.UUID) -> list[ValidationIssue]:
        rows = await self.session.scalars(
            select(ValidationIssue)
            .where(ValidationIssue.invoice_id == invoice_id)
            .order_by(ValidationIssue.severity, ValidationIssue.code)
        )
        return list(rows)

    async def replace_issues(self, invoice_id: uuid.UUID, issues: list[Issue]) -> None:
        await self.session.execute(
            delete(ValidationIssue).where(ValidationIssue.invoice_id == invoice_id)
        )
        for issue in issues:
            self.session.add(ValidationIssue(invoice_id=invoice_id, **issue.model_dump()))

    # ------------------------------------------------------------ job queue state

    async def create_job(self, document_id: uuid.UUID) -> ProcessingJob:
        job = ProcessingJob(document_id=document_id, status=JobStatus.QUEUED, attempts=0)
        self.session.add(job)
        await self.session.flush()
        return job

    async def get_job(self, job_id: uuid.UUID) -> ProcessingJob | None:
        return await self.session.get(ProcessingJob, job_id)

    async def latest_job(self, document_id: uuid.UUID) -> ProcessingJob | None:
        return await self.session.scalar(
            select(ProcessingJob)
            .where(ProcessingJob.document_id == document_id)
            .order_by(ProcessingJob.created_at.desc(), ProcessingJob.id)
            .limit(1)
        )

    async def latest_jobs(self, document_ids: list[uuid.UUID]) -> dict[uuid.UUID, ProcessingJob]:
        """Latest job of each document, in one query (for list pages)."""
        if not document_ids:
            return {}
        ranked = (
            select(
                ProcessingJob.id,
                func.row_number()
                .over(
                    partition_by=ProcessingJob.document_id,
                    order_by=(ProcessingJob.created_at.desc(), ProcessingJob.id.desc()),
                )
                .label("position"),
            )
            .where(ProcessingJob.document_id.in_(document_ids))
            .subquery()
        )
        jobs = await self.session.scalars(
            select(ProcessingJob)
            .join(ranked, ranked.c.id == ProcessingJob.id)
            .where(ranked.c.position == 1)
        )
        return {job.document_id: job for job in jobs}

    async def get_corrections(self, invoice_id: uuid.UUID) -> list[Correction]:
        rows = await self.session.scalars(
            select(Correction)
            .where(Correction.invoice_id == invoice_id)
            .order_by(Correction.corrected_at, Correction.id)
        )
        return list(rows)

    async def get_page_texts(self, document_id: uuid.UUID) -> list[PageText]:
        rows = await self.session.scalars(
            select(PageText)
            .where(PageText.document_id == document_id)
            .order_by(PageText.page_number)
        )
        return list(rows)

    async def has_active_job(self, document_id: uuid.UUID) -> bool:
        count = await self.session.scalar(
            select(func.count())
            .select_from(ProcessingJob)
            .where(
                ProcessingJob.document_id == document_id,
                ProcessingJob.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
            )
        )
        return bool(count)

    async def stuck_jobs(self, older_than_minutes: int) -> list[ProcessingJob]:
        cutoff = _now() - timedelta(minutes=older_than_minutes)
        rows = await self.session.scalars(
            select(ProcessingJob).where(
                ProcessingJob.status == JobStatus.RUNNING, ProcessingJob.started_at < cutoff
            )
        )
        return list(rows)

    async def documents_with_failed_latest_job(self) -> list[uuid.UUID]:
        """Documents whose most recent job failed (older failures already retried are
        ignored, so retry-failed never queues the same document twice)."""
        # Number each document's jobs newest-first; row 1 is the latest job.
        ranked = select(
            ProcessingJob.document_id,
            ProcessingJob.status,
            func.row_number()
            .over(
                partition_by=ProcessingJob.document_id,
                order_by=(ProcessingJob.created_at.desc(), ProcessingJob.id.desc()),
            )
            .label("position"),
        ).subquery()
        rows = await self.session.scalars(
            select(ranked.c.document_id).where(
                ranked.c.position == 1, ranked.c.status == JobStatus.FAILED
            )
        )
        return list(rows)

    # ------------------------------------------------------------ duplicate lookups

    async def find_invoices_by_supplier_and_number(
        self, supplier_key: str, invoice_number: str, exclude_invoice_id: uuid.UUID | None
    ) -> list[uuid.UUID]:
        rows = await self.session.scalars(
            select(Invoice.document_id).where(
                Invoice.supplier_key == supplier_key,
                Invoice.invoice_number_key == normalise_invoice_number(invoice_number),
                Invoice.id != exclude_invoice_id,
            )
        )
        return list(rows)

    async def find_invoices_by_supplier_date_total(
        self,
        supplier_key: str,
        invoice_date: date,
        total: Decimal,
        exclude_invoice_id: uuid.UUID | None,
    ) -> list[uuid.UUID]:
        rows = await self.session.scalars(
            select(Invoice.document_id).where(
                Invoice.supplier_key == supplier_key,
                Invoice.invoice_date == invoice_date,
                Invoice.total == total,
                Invoice.id != exclude_invoice_id,
            )
        )
        return list(rows)


def _field_json(field: FieldResult) -> dict[str, Any]:
    # mode="json": Decimal -> "1320.00" string, date -> "2026-11-12" (CLAUDE.md rule 1)
    return field.model_dump(mode="json")


def _merge_split_pages(pages: list[SourcePage]) -> list[SourcePage]:
    """Very long pages are split into several SourcePages with the same number for the
    LLM; the database keeps one row per page."""
    merged: dict[int, SourcePage] = {}
    for page in pages:
        if page.number in merged:
            first = merged[page.number]
            merged[page.number] = SourcePage(
                page.number,
                first.text + "\n" + page.text,
                first.label,
                first.ocr_confidence,
                first.source,
            )
        else:
            merged[page.number] = page
    return list(merged.values())
