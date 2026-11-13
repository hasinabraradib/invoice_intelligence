"""Repository + process_document against a real (throw-away) Postgres database."""

import uuid

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import func, select, text

import app.models as m
from app.config import Settings
from app.extraction.llm import FakeLLMClient
from app.pipeline import process_document
from app.repository import Repository
from tests import factories

pytestmark = pytest.mark.anyio


# The text that factories.text_pdf() prints, as a model would quote it.
def _f(value, evidence=None, page=1):
    return {"value": value, "status": "found", "evidence": evidence or value, "page": page}


MISSING = {"value": None, "status": "missing", "evidence": None, "page": None}
GOOD_REPLY = {
    "invoice_number": _f("INV-2024-001", "Invoice INV-2024-001"),
    "invoice_date": _f("15 January 2024", "Invoice date: 15 January 2024"),
    "due_date": _f("14 February 2024", "Due date: 14 February 2024"),
    "supplier_name": _f("ACME Supplies Ltd"),
    "customer_name": _f("Example Customer GmbH", "Bill to: Example Customer GmbH"),
    "currency": _f("EUR", "4.50 EUR"),
    "subtotal": _f("4.50", "4.50 EUR"),
    "tax": MISSING,
    "total": _f("4.50", "Total due: 4.50 EUR"),
    "line_items": [
        {
            "description": _f("Bolts M8 x 100"),
            "quantity": _f("3", "3 x 1.50"),
            "unit_price": _f("1.50", "3 x 1.50"),
            "line_total": _f("4.50", "4.50 EUR"),
        }
    ],
}


@pytest.fixture
def settings(upload_dir):
    return Settings(upload_dir=upload_dir, ocr_enabled=False, embeddings_enabled=False)


async def _document(session, path) -> uuid.UUID:
    data = path.read_bytes()
    import hashlib

    doc = await Repository(session).create_document(
        filename=path.name,
        sha256=hashlib.sha256(data).hexdigest(),
        mime_type="application/pdf",
        size_bytes=len(data),
        storage_path=str(path),
    )
    await session.commit()
    return doc.id


async def _count(session, model, **where) -> int:
    query = select(func.count()).select_from(model)
    for key, value in where.items():
        query = query.where(getattr(model, key) == value)
    return await session.scalar(query)


async def test_migration_matches_models(db_session):
    def diff(sync_conn):
        return compare_metadata(MigrationContext.configure(sync_conn), m.Base.metadata)

    conn = await db_session.connection()
    assert await conn.run_sync(diff) == []  # no table/column/index drift
    assert await db_session.scalar(text("SELECT 1 FROM pg_extension WHERE extname='vector'"))


async def test_process_document_saves_everything(db_session, tmp_path, settings):
    doc_id = await _document(db_session, factories.text_pdf(tmp_path / "inv.pdf", pages=1))

    outcome = await process_document(doc_id, db_session, FakeLLMClient(GOOD_REPLY), settings)

    assert outcome.status == m.DocumentStatus.PROCESSED and outcome.error is None
    invoice = await db_session.scalar(select(m.Invoice).where(m.Invoice.document_id == doc_id))
    assert invoice.invoice_number == "INV-2024-001" and invoice.supplier_key == "acme supplies"
    assert str(invoice.total) == "4.50" and invoice.currency == "EUR"
    assert invoice.fields["total"]["evidence"] == "Total due: 4.50 EUR"
    assert invoice.fields["total"]["value"] == "4.50"  # Decimal stored as a string in JSON
    [line] = (await db_session.scalars(select(m.LineItem))).all()
    assert (line.position, str(line.quantity), str(line.line_total)) == (1, "3.0000", "4.50")
    [page] = (await db_session.scalars(select(m.PageText))).all()
    assert page.source == "text_layer" and "INV-2024-001" in page.text
    # The generated tsvector column is filled by Postgres and searchable.
    hit = await db_session.scalar(
        text("SELECT count(*) FROM page_texts WHERE search @@ plainto_tsquery('simple', 'bolts')")
    )
    assert hit == 1
    job = await db_session.scalar(select(m.ProcessingJob))
    assert (job.status, job.stage, job.attempts) == (m.JobStatus.SUCCEEDED, "extracting", 1)
    assert job.finished_at is not None


async def test_processing_twice_is_idempotent(db_session, tmp_path, settings):
    doc_id = await _document(db_session, factories.text_pdf(tmp_path / "inv.pdf", pages=1))
    llm = FakeLLMClient(GOOD_REPLY)

    first = await process_document(doc_id, db_session, llm, settings)
    second = await process_document(doc_id, db_session, llm, settings)

    assert first.invoice_id == second.invoice_id  # same row updated, not a new one
    assert await _count(db_session, m.Invoice) == 1
    assert await _count(db_session, m.LineItem) == 1
    assert await _count(db_session, m.PageText) == 1
    assert await _count(db_session, m.ValidationIssue) == first.issue_count == second.issue_count
    assert await _count(db_session, m.ProcessingJob) == 2  # one job row per run


async def test_corrections_survive_reprocessing(db_session, tmp_path, settings):
    doc_id = await _document(db_session, factories.text_pdf(tmp_path / "inv.pdf", pages=1))
    outcome = await process_document(doc_id, db_session, FakeLLMClient(GOOD_REPLY), settings)
    db_session.add(
        m.Correction(
            invoice_id=outcome.invoice_id,
            field="total",
            old_value="4.50",
            new_value="4.60",
            corrected_by="reviewer",
        )
    )
    await db_session.commit()

    await process_document(doc_id, db_session, FakeLLMClient(GOOD_REPLY), settings)

    assert await _count(db_session, m.Correction) == 1


async def test_failed_extraction_sets_status_failed(db_session, tmp_path, settings):
    doc_id = await _document(db_session, factories.text_pdf(tmp_path / "inv.pdf", pages=1))

    outcome = await process_document(doc_id, db_session, FakeLLMClient("{bad", "{bad"), settings)

    document = await db_session.get(m.Document, doc_id, populate_existing=True)
    assert outcome.status == document.status == m.DocumentStatus.FAILED
    assert "invalid JSON" in document.error
    job = await db_session.scalar(select(m.ProcessingJob))
    assert job.status == m.JobStatus.FAILED and job.stage == "extracting"
    # The planned invoice row stays (a retry resumes from it) but holds no extraction.
    [placeholder] = (await db_session.scalars(select(m.Invoice))).all()
    assert placeholder.job_id is None and placeholder.invoice_number is None


async def test_unreadable_file_fails_cleanly(db_session, tmp_path, settings):
    doc_id = await _document(db_session, factories.truncated_pdf(tmp_path / "broken.pdf"))

    outcome = await process_document(doc_id, db_session, FakeLLMClient(GOOD_REPLY), settings)

    assert outcome.status == m.DocumentStatus.FAILED
    assert outcome.error == "The PDF is damaged or unreadable."


async def test_bad_page_does_not_fail_the_document(db_session, tmp_path, settings, monkeypatch):
    from app.parsing import pdf_parser

    real = pdf_parser.pdfplumber.page.Page.extract_text

    def flaky(page, *a, **kw):
        if page.page_number == 2:
            raise ValueError("broken page")
        return real(page, *a, **kw)

    monkeypatch.setattr(pdf_parser.pdfplumber.page.Page, "extract_text", flaky)
    doc_id = await _document(db_session, factories.text_pdf(tmp_path / "inv.pdf", pages=2))

    outcome = await process_document(doc_id, db_session, FakeLLMClient(GOOD_REPLY), settings)

    assert outcome.status in (m.DocumentStatus.PROCESSED, m.DocumentStatus.NEEDS_REVIEW)
    assert await _count(db_session, m.PageText) == 1  # the good page is kept


async def test_same_supplier_and_number_in_another_file_is_flagged(db_session, tmp_path, settings):
    first = await _document(db_session, factories.text_pdf(tmp_path / "a.pdf", pages=1))
    second = await _document(db_session, factories.text_pdf(tmp_path / "b.pdf", pages=2))
    await process_document(first, db_session, FakeLLMClient(GOOD_REPLY), settings)

    outcome = await process_document(second, db_session, FakeLLMClient(GOOD_REPLY), settings)

    assert outcome.status == m.DocumentStatus.NEEDS_REVIEW
    issues = (await db_session.scalars(select(m.ValidationIssue))).all()
    duplicate = next(i for i in issues if i.code == "DUPLICATE_INVOICE")
    assert duplicate.actual == str(first) and duplicate.severity == "error"
    assert not any(i.code == "POSSIBLE_DUPLICATE" for i in issues)  # not reported twice


async def test_repository_duplicate_lookups(db_session):
    repo = Repository(db_session)
    doc = await repo.create_document(
        filename="a.pdf",
        sha256="a" * 64,
        mime_type="application/pdf",
        size_bytes=1,
        storage_path="/x",
    )
    from datetime import date
    from decimal import Decimal

    invoice_id = uuid.uuid4()
    db_session.add(
        m.Invoice(
            id=invoice_id,
            document_id=doc.id,
            supplier_key="acme",
            invoice_number="7",
            invoice_number_key="7",
            invoice_date=date(2026, 1, 2),
            total=Decimal("10.00"),
        )
    )
    await db_session.commit()
    other = uuid.uuid4()

    assert await repo.find_document_id_by_sha256("a" * 64) == doc.id
    assert await repo.find_invoices_by_supplier_and_number("acme", "7", other) == [doc.id]
    # Only the invoice itself is excluded (returned ids are DOCUMENT ids).
    assert await repo.find_invoices_by_supplier_and_number("acme", "7", invoice_id) == []
    assert await repo.find_invoices_by_supplier_date_total(
        "acme", date(2026, 1, 2), Decimal("10.00"), other
    ) == [doc.id]
