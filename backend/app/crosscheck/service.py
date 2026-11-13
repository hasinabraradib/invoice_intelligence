"""Run a cross-check: load both sides, compare, store findings and review issues.

Idempotent: running a check again replaces its findings and the issues it raised.
Values are never changed; a mismatch only adds a CROSS_CHECK_MISMATCH issue to the
invoices involved, which sets needs_review (re-validation keeps such issues).
"""

import logging
import uuid
from collections import Counter
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.crosscheck.compare import Entry, Finding, Line, Mode, compare
from app.invoices import revalidate
from app.ledger.store import ledger_rows
from app.models import (
    CrossCheck,
    CrossCheckFinding,
    Document,
    DocumentStatus,
    Invoice,
    JobStatus,
    LedgerRow,
    ValidationIssue,
)
from app.repository import Repository

logger = logging.getLogger(__name__)

FINISHED = {DocumentStatus.PROCESSED, DocumentStatus.NEEDS_REVIEW, DocumentStatus.PARTIALLY_FAILED}
ISSUE_CODE = "CROSS_CHECK_MISMATCH"


class CrossCheckError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


async def _extracted_invoices(session: AsyncSession, document_id: uuid.UUID) -> list[Invoice]:
    rows = await session.scalars(
        select(Invoice)
        .where(Invoice.document_id == document_id, Invoice.job_id.is_not(None))
        .order_by(Invoice.segment_index)
    )
    return list(rows)


async def side_kind(session: AsyncSession, document: Document) -> str | None:
    if await _extracted_invoices(session, document.id):
        return "invoices"
    if await ledger_rows(session, document.id):
        return "ledger"
    return None


async def prepare(
    session: AsyncSession, a_id: uuid.UUID, b_id: uuid.UUID
) -> tuple[uuid.UUID, uuid.UUID, Mode]:
    """Check the pair can be compared; returns (A, B, mode) with invoices always on side A."""
    if a_id == b_id:
        raise CrossCheckError(422, "same_document", "Choose two different documents.")
    docs = {}
    for doc_id in (a_id, b_id):
        document = await session.get(Document, doc_id)
        if document is None:
            raise CrossCheckError(404, "not_found", f"document {doc_id} not found")
        if document.status not in FINISHED:
            raise CrossCheckError(
                409, "not_ready", f"{document.filename} is not processed yet ({document.status})."
            )
        docs[doc_id] = await side_kind(session, document)
    kinds = (docs[a_id], docs[b_id])
    if kinds == ("invoices", "ledger"):
        return a_id, b_id, "invoices_vs_ledger"
    if kinds == ("ledger", "invoices"):
        return b_id, a_id, "invoices_vs_ledger"  # invoices always on side A
    if kinds == ("invoices", "invoices"):
        return a_id, b_id, "invoices_vs_invoices"
    raise CrossCheckError(
        422,
        "nothing_to_compare",
        "One document must contain invoices, the other invoices or a ledger.",
    )


async def _invoice_entries(repo: Repository, document_id: uuid.UUID) -> list[Entry]:
    entries = []
    for invoice in await _extracted_invoices(repo.session, document_id):
        items = await repo.get_line_items(invoice.id)
        pages = {
            name: data["page"]
            for name, data in (invoice.fields or {}).items()
            if isinstance(data, dict) and data.get("page")
        }
        lines = tuple(
            Line(
                i.position,
                i.description,
                i.quantity,
                i.unit_price,
                i.line_total,
                ((i.fields or {}).get("line_total") or {}).get("page") or invoice.page_start,
            )
            for i in items
        )
        entries.append(
            Entry(
                ref=invoice.id,
                number=invoice.invoice_number,
                number_key=invoice.invoice_number_key,
                supplier=invoice.supplier_name,
                supplier_key=invoice.supplier_key,
                invoice_date=invoice.invoice_date,
                due_date=invoice.due_date,
                customer=invoice.customer_name,
                currency=invoice.currency,
                subtotal=invoice.subtotal,
                tax=invoice.tax,
                total=invoice.total,
                lines=lines,
                pages=pages,
                page=invoice.page_start,
                segment=invoice.segment_index,
            )
        )
    return entries


def _ledger_entry(row: LedgerRow) -> Entry:
    return Entry(
        ref=row.id,
        number=row.invoice_number,
        number_key=row.invoice_number_key,
        supplier=row.supplier_name,
        supplier_key=row.supplier_key,
        invoice_date=row.invoice_date,
        currency=row.currency,
        tax=row.tax,
        total=row.total,
        sheet=row.sheet_name,
        row=row.row_number,
    )


def summarise(findings: list[Finding]) -> dict[str, int]:
    kinds = Counter(f.kind for f in findings)
    mismatched_pairs = {
        (f.a.ref, f.b.ref) for f in findings if f.severity == "error" and f.a and f.b
    }
    return {
        "matched": kinds["matched"],
        "mismatched": len(mismatched_pairs),
        "missing_in_a": kinds["missing_in_pdf"] + kinds["missing_in_a"],
        "missing_in_b": kinds["missing_in_ledger"] + kinds["missing_in_b"],
        "duplicates_in_b": kinds["duplicate_in_ledger"],
        "findings": len(findings),
    }


async def run_cross_check(
    session: AsyncSession, cross_check_id: uuid.UUID, settings: Settings
) -> CrossCheck:
    check = await session.get(CrossCheck, cross_check_id)
    if check is None:
        raise ValueError(f"cross-check {cross_check_id} not found")
    check.status, check.error = JobStatus.RUNNING, None
    check.started_at, check.finished_at = datetime.now(UTC), None
    await session.commit()
    try:
        await _run(session, check, settings)
    except Exception as exc:
        await session.rollback()
        logger.exception("cross-check %s failed", cross_check_id)
        check = await session.get(CrossCheck, cross_check_id)
        check.status = JobStatus.FAILED
        check.error = getattr(exc, "message", None) or f"internal error ({type(exc).__name__})"
        check.finished_at = datetime.now(UTC)
        await session.commit()
    return check


async def _run(session: AsyncSession, check: CrossCheck, settings: Settings) -> None:
    repo = Repository(session)
    a_id, b_id, mode = await prepare(session, check.document_a_id, check.document_b_id)
    check.mode = mode
    a = await _invoice_entries(repo, a_id)
    if mode == "invoices_vs_ledger":
        b = [_ledger_entry(r) for r in await ledger_rows(session, b_id)]
    else:
        b = await _invoice_entries(repo, b_id)
    findings = compare(a, b, mode=mode, tolerance=settings.money_tolerance)
    names = {
        d.id: d.filename
        for d in await session.scalars(select(Document).where(Document.id.in_([a_id, b_id])))
    }

    # Replace this check's earlier findings and issues (idempotent).
    previously_flagged = set(
        await session.scalars(
            select(ValidationIssue.invoice_id).where(ValidationIssue.cross_check_id == check.id)
        )
    )
    await session.execute(
        delete(CrossCheckFinding).where(CrossCheckFinding.cross_check_id == check.id)
    )
    await session.execute(delete(ValidationIssue).where(ValidationIssue.cross_check_id == check.id))
    flagged: set[uuid.UUID] = set()
    for position, finding in enumerate(findings):
        session.add(_finding_row(check.id, position, finding, mode))
        if not finding.is_mismatch:
            continue
        for invoice, other_doc, mine, theirs in (
            (finding.a, b_id, finding.value_a, finding.value_b),
            (
                finding.b if mode == "invoices_vs_invoices" else None,
                a_id,
                finding.value_b,
                finding.value_a,
            ),
        ):
            if invoice is None:
                continue
            flagged.add(invoice.ref)
            session.add(
                ValidationIssue(
                    invoice_id=invoice.ref,
                    code=ISSUE_CODE,
                    severity="error",
                    field=finding.field,
                    message=f"Cross-check with {names[other_doc]}: {finding.message}",
                    expected=theirs,
                    actual=mine,
                    cross_check_id=check.id,
                )
            )
    await session.flush()
    # Re-validate every invoice whose cross-check issues changed: needs_review and the
    # document status follow (values themselves are never touched).
    for invoice_id in flagged | previously_flagged:
        invoice = await session.get(Invoice, invoice_id)
        if invoice is not None:
            await revalidate(repo, invoice, settings)
    check.summary = summarise(findings)
    check.status, check.finished_at = JobStatus.SUCCEEDED, datetime.now(UTC)
    await session.commit()
    logger.info("cross-check %s done: %s", check.id, check.summary)


def _finding_row(check_id: uuid.UUID, position: int, f: Finding, mode: Mode) -> CrossCheckFinding:
    ledger = mode == "invoices_vs_ledger"
    return CrossCheckFinding(
        cross_check_id=check_id,
        position=position,
        kind=f.kind,
        severity=f.severity,
        field=f.field,
        value_a=f.value_a,
        value_b=f.value_b,
        message=f.message,
        invoice_a_id=f.a.ref if f.a else None,
        invoice_b_id=f.b.ref if f.b and not ledger else None,
        ledger_row_id=f.b.ref if f.b and ledger else None,
        page_a=f.page_a,
        page_b=f.page_b if not ledger else None,
        sheet_b=f.b.sheet if f.b else None,
        row_b=f.b.row if f.b else None,
        segment_a=f.a.segment if f.a else None,
        segment_b=f.b.segment if f.b and not ledger else None,
    )


async def count_findings(session: AsyncSession, check_id: uuid.UUID) -> int:
    return (
        await session.scalar(
            select(func.count())
            .select_from(CrossCheckFinding)
            .where(CrossCheckFinding.cross_check_id == check_id)
        )
        or 0
    )
