"""Turn database rows into API response models."""

from app.api.schemas import (
    DocumentSummary,
    FieldView,
    InvoiceResult,
    InvoiceSummary,
    IssueView,
    LineItemView,
)
from app.extraction.schema import LINE_ITEM_FIELDS
from app.invoices import HEADER_FIELDS, duplicate_links
from app.models import Document, Invoice, LineItem, ValidationIssue


def document_summary(document: Document, invoices: list[Invoice]) -> DocumentSummary:
    summary = DocumentSummary.model_validate(document)
    summary.invoice = InvoiceSummary.model_validate(invoices[0]) if invoices else None
    summary.invoice_count = len(invoices)
    summary.needs_review = any(i.needs_review for i in invoices)
    return summary


def invoice_result(
    invoice: Invoice, items: list[LineItem], issues: list[ValidationIssue]
) -> InvoiceResult:
    stored = invoice.fields or {}
    return InvoiceResult(
        invoice_id=invoice.id,
        document_id=invoice.document_id,
        segment_index=invoice.segment_index,
        page_start=invoice.page_start,
        page_end=invoice.page_end,
        extracted=invoice.job_id is not None,
        needs_review=invoice.needs_review,
        overall_confidence=invoice.overall_confidence,
        reviewed_by=invoice.reviewed_by,
        reviewed_at=invoice.reviewed_at,
        review_override_reason=invoice.review_override_reason,
        extracted_at=invoice.extracted_at,
        fields={name: FieldView(**stored.get(name, {})) for name in HEADER_FIELDS},
        line_items=[
            LineItemView(
                position=item.position,
                **{f: FieldView(**(item.fields or {}).get(f, {})) for f in LINE_ITEM_FIELDS},
            )
            for item in items
        ],
        issues=[IssueView.model_validate(issue) for issue in issues],
        duplicates=duplicate_links(issues),
    )
