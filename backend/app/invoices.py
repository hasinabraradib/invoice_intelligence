"""Invoice-level logic shared by the pipeline and the API: rebuilding results from the
database, keeping human corrections, applying corrections, and re-validation."""

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from app.config import Settings
from app.extraction.postprocess import normalise_invoice_number, normalise_supplier_key
from app.extraction.schema import LINE_ITEM_FIELDS
from app.extraction.service import (
    FIELD_KINDS,
    ExtractionResult,
    FieldResult,
    LineItemResult,
)
from app.models import Correction, DocumentStatus, Invoice, LineItem
from app.repository import Repository
from app.validation.duplicates import find_duplicate_issues
from app.validation.rules import ValidationIssue, validate_invoice

HEADER_FIELDS = (
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


def typed(name: str, value: Any) -> Any:
    """JSON value -> Python type (Decimal for money, date for dates)."""
    if value is None:
        return None
    kind = FIELD_KINDS[name]
    if kind == "amount":
        return Decimal(str(value))
    if kind == "date":
        return value if isinstance(value, date) else date.fromisoformat(str(value))
    return str(value)


def field_from_json(name: str, data: dict[str, Any] | None) -> FieldResult:
    if not data:
        return FieldResult()
    return FieldResult(**{**data, "value": typed(name, data.get("value"))})


def result_from_db(invoice: Invoice, items: list[LineItem]) -> ExtractionResult:
    """Rebuild an ExtractionResult from stored rows (to re-run validation)."""
    result = ExtractionResult(ok=True, provider="database", supplier_key=invoice.supplier_key)
    result.fields = {n: field_from_json(n, invoice.fields.get(n)) for n in HEADER_FIELDS}
    result.line_items = [
        LineItemResult(**{f: field_from_json(f, item.fields.get(f)) for f in LINE_ITEM_FIELDS})
        for item in items
    ]
    return result


def keep_corrections(result: ExtractionResult, invoice: Invoice, items: list[LineItem]) -> None:
    """Re-processing must never overwrite a human correction: copy corrected header
    fields back in, and keep the stored line items if any of them was corrected."""
    for name, data in (invoice.fields or {}).items():
        if name in HEADER_FIELDS and data.get("status") == "corrected":
            result.fields[name] = field_from_json(name, data)
    if any(d.get("status") == "corrected" for item in items for d in item.fields.values()):
        result.line_items = result_from_db(invoice, items).line_items
    supplier = result.fields.get("supplier_name")
    if supplier and supplier.status == "corrected":
        result.supplier_key = normalise_supplier_key(supplier.value)


def corrected_field(value: Any, by: str) -> dict[str, Any]:
    field = FieldResult(
        raw_value=None if value is None else str(value),
        value=value,
        status="corrected" if value is not None else "missing",
        confidence=1.0 if value is not None else 0.0,
        reasons=[f"corrected by {by}"],
    )
    return field.model_dump(mode="json")


def _as_text(value: Any) -> str | None:
    return None if value is None else str(value)


async def apply_corrections(
    repo: Repository,
    invoice: Invoice,
    header: dict[str, Any],
    line_items: dict[int, dict[str, Any]],
    corrected_by: str,
) -> list[Correction]:
    """Write new values, mark them "corrected", and log every change in `corrections`.
    Unchanged values are skipped (no empty audit entries)."""
    changes: list[Correction] = []
    fields = dict(invoice.fields or {})  # new dict, so SQLAlchemy sees the JSONB change
    for name, new in header.items():
        old = getattr(invoice, name)
        if old == new and fields.get(name, {}).get("status") == "corrected":
            continue
        setattr(invoice, name, new)
        fields[name] = corrected_field(new, corrected_by)
        changes.append(
            Correction(
                invoice_id=invoice.id,
                field=name,
                old_value=_as_text(old),
                new_value=_as_text(new),
                corrected_by=corrected_by,
            )
        )
    invoice.fields = fields
    if "supplier_name" in header:
        invoice.supplier_key = normalise_supplier_key(invoice.supplier_name)
    if "invoice_number" in header:
        invoice.invoice_number_key = normalise_invoice_number(invoice.invoice_number)

    items = {item.position: item for item in await repo.get_line_items(invoice.id)}
    for position, values in line_items.items():
        item = items[position]
        item_fields = dict(item.fields or {})
        for name, new in values.items():
            old = getattr(item, name)
            setattr(item, name, new)
            item_fields[name] = corrected_field(new, corrected_by)
            changes.append(
                Correction(
                    invoice_id=invoice.id,
                    field=f"line_items[{position}].{name}",
                    old_value=_as_text(old),
                    new_value=_as_text(new),
                    corrected_by=corrected_by,
                )
            )
        item.fields = item_fields
    repo.session.add_all(changes)
    await repo.session.flush()
    return changes


async def revalidate(
    repo: Repository, invoice: Invoice, settings: Settings
) -> list[ValidationIssue]:
    """Re-run validation + duplicate checks on the stored values, replace the issues,
    and update needs_review and the document status."""
    result = result_from_db(invoice, await repo.get_line_items(invoice.id))
    report = validate_invoice(
        result, tolerance=settings.money_tolerance, review_confidence=settings.review_confidence
    )
    duplicates = await find_duplicate_issues(
        repo,
        document_id=invoice.document_id,
        supplier_key=invoice.supplier_key,
        invoice_number=invoice.invoice_number,
        invoice_date=invoice.invoice_date,
        total=invoice.total,
    )
    issues = report.issues + duplicates
    invoice.needs_review = apply_review_state(
        invoice, issues, report.needs_review or any(i.severity == "error" for i in duplicates)
    )
    await repo.replace_issues(invoice.id, issues)
    await repo.set_document_status(
        invoice.document_id,
        DocumentStatus.NEEDS_REVIEW if invoice.needs_review else DocumentStatus.PROCESSED,
    )
    return issues


def error_keys(issues) -> list[str]:
    return sorted({f"{i.code}:{i.field or ''}" for i in issues if i.severity == "error"})


def apply_review_state(invoice: Invoice | None, issues, needs_review: bool) -> bool:
    """needs_review after validation, taking an earlier approval into account.

    Not approved yet: whatever validation says. Approved: stays approved unless an error
    appears that the reviewer did not accept; then the approval is cleared.
    """
    if invoice is None or invoice.reviewed_at is None:
        return needs_review
    new_errors = set(error_keys(issues)) - set(invoice.approved_error_keys or [])
    if not new_errors:
        return False
    invoice.reviewed_by = invoice.reviewed_at = invoice.review_override_reason = None
    invoice.approved_error_keys = []
    return True


def duplicate_links(issues) -> list[dict[str, Any]]:
    """Other documents referenced by duplicate issues (ids are stored in `actual`)."""
    links = []
    for issue in issues:
        if issue.code in ("DUPLICATE_INVOICE", "POSSIBLE_DUPLICATE") and issue.actual:
            for raw in issue.actual.split(","):
                try:
                    links.append({"document_id": uuid.UUID(raw.strip()), "code": issue.code})
                except ValueError:
                    continue
    return links
