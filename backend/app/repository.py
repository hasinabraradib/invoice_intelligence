"""All database reads/writes, as typed methods. Callers own the transaction (commit).

Never logs document text (CLAUDE.md rule 5).
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.extraction.prompt import SourcePage
from app.extraction.service import ExtractionResult, FieldResult
from app.models import (
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

    async def finish_job(self, job_id: uuid.UUID, *, error: str | None = None) -> None:
        await self.update_job(
            job_id,
            status=JobStatus.FAILED if error else JobStatus.SUCCEEDED,
            error=error,
            finished_at=_now(),
        )

    # ------------------------------------------------------------ invoices

    async def get_invoice_by_document(self, document_id: uuid.UUID) -> Invoice | None:
        return await self.session.scalar(select(Invoice).where(Invoice.document_id == document_id))

    async def save_extraction(
        self,
        *,
        document_id: uuid.UUID,
        result: ExtractionResult,
        issues: list[Issue],
        needs_review: bool,
        overall_confidence: float,
        pages: list[SourcePage],
    ) -> Invoice:
        """Write (or overwrite) this document's invoice, line items, issues and page texts.

        Idempotent: the invoice row is UPDATED in place (same id, so corrections that point
        at it survive); its child rows and the page texts are deleted and re-inserted.
        Runs in the caller's transaction, so it all lands together or not at all.
        """
        invoice = await self.get_invoice_by_document(document_id)
        if invoice is None:
            invoice = Invoice(document_id=document_id)
            self.session.add(invoice)
        for column in HEADER_COLUMNS:
            value = result.fields[column].value if column in result.fields else None
            setattr(invoice, column, _clean(value) if isinstance(value, str) else value)
        invoice.supplier_key = result.supplier_key
        invoice.needs_review = needs_review
        invoice.overall_confidence = overall_confidence
        invoice.fields = _jsonable({n: _field_json(f) for n, f in result.fields.items()})
        invoice.raw_model_output = _jsonable(result.raw_outputs)
        invoice.extracted_at = _now()
        await self.session.flush()

        await self.session.execute(delete(LineItem).where(LineItem.invoice_id == invoice.id))
        await self.session.execute(
            delete(ValidationIssue).where(ValidationIssue.invoice_id == invoice.id)
        )
        await self.session.execute(delete(PageText).where(PageText.document_id == document_id))
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
        for issue in issues:
            self.session.add(ValidationIssue(invoice_id=invoice.id, **issue.model_dump()))
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
        await self.session.flush()
        return invoice

    # ------------------------------------------------------------ duplicate lookups

    async def find_invoices_by_supplier_and_number(
        self, supplier_key: str, invoice_number: str, exclude_document_id: uuid.UUID
    ) -> list[uuid.UUID]:
        rows = await self.session.scalars(
            select(Invoice.document_id).where(
                Invoice.supplier_key == supplier_key,
                Invoice.invoice_number == invoice_number,
                Invoice.document_id != exclude_document_id,
            )
        )
        return list(rows)

    async def find_invoices_by_supplier_date_total(
        self,
        supplier_key: str,
        invoice_date: date,
        total: Decimal,
        exclude_document_id: uuid.UUID,
    ) -> list[uuid.UUID]:
        rows = await self.session.scalars(
            select(Invoice.document_id).where(
                Invoice.supplier_key == supplier_key,
                Invoice.invoice_date == invoice_date,
                Invoice.total == total,
                Invoice.document_id != exclude_document_id,
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
