"""Batch PDFs: many invoices in one document, resumable per-invoice processing."""

import hashlib
import re
import threading

import pytest
from arq import Retry
from reportlab.pdfgen import canvas
from sqlalchemy import func, select

import app.models as m
from app.extraction.llm import FakeLLMClient, LLMError
from app.pipeline import process_document
from app.repository import Repository

pytestmark = pytest.mark.anyio

MISSING = {"value": None, "status": "missing", "evidence": None, "page": None}


def invoice_pages(number: str, supplier: str, total: str, pages: int = 1) -> list[list[str]]:
    """Text lines for one invoice of `pages` pages (with 'Page k of N' when > 1)."""
    out = []
    for k in range(1, pages + 1):
        lines = ["continued"]
        if k == 1:
            lines = [f"INVOICE  {supplier}", f"Invoice No: {number}", "Invoice Date: 2026-03-15"]
        lines += [f"Item {k}  1  10.00  10.00"]
        if k == pages:
            lines.append(f"Total: {total}")
        if pages > 1:
            lines.append(f"Page {k} of {pages}")
        out.append(lines)
    return out


def make_pdf(path, pages: list[list[str]]):
    c = canvas.Canvas(str(path))
    for lines in pages:
        for i, line in enumerate(lines):
            c.drawString(50, 800 - 20 * i, line)
        c.showPage()
    c.save()
    return path


BATCH = (
    invoice_pages("A-100", "ACME Ltd", "10.00")
    + invoice_pages("B-200", "Globex Corp", "20.00", pages=2)
    + invoice_pages("C-300", "Initech", "10.00")
)


class BatchLLM(FakeLLMClient):
    """Answers like a model would, from what is in the prompt: extraction replies quote
    the invoice found in the given pages; classification replies come from `starts`."""

    def __init__(self, starts=None, fail=None, broken=()):
        super().__init__(lambda system, user: self.answer(system, user))
        self.starts = starts or {}
        self.fail = dict(fail or {})  # invoice number -> times to fail with a 429
        self.broken = set(broken)  # invoice numbers that always get unusable JSON
        self.extractions: list[str] = []
        self.classified: list[int] = []
        self._lock = threading.Lock()

    def answer(self, system: str, user: str):
        if "split a batch" in system:
            asked = [int(n) for n in re.findall(r"=== PAGE (\d+) ===", user)]
            with self._lock:
                self.classified += asked
            return {
                "pages": [
                    {"page": n, "starts_new_invoice": self.starts.get(n, False)} for n in asked
                ]
            }
        number = re.search(r"Invoice No: (\S+)", user)
        number = number[1] if number else "?"
        with self._lock:
            self.extractions.append(number)
            if self.fail.get(number, 0) > 0:
                self.fail[number] -= 1
                raise LLMError("HTTP 429 after 4 attempts", retryable=True)
            if number in self.broken:
                return "{this is not json"  # a permanent failure (no retry helps)
        total = re.search(r"Total: (\S+)", user)
        supplier = re.search(r"INVOICE\s+(.+)", user)
        page = int(re.search(r"=== PAGE (\d+)", user)[1])
        found = {
            "invoice_number": {
                "value": number,
                "status": "found",
                "evidence": f"Invoice No: {number}",
                "page": page,
            },
            "supplier_name": (
                {
                    "value": supplier[1].strip(),
                    "status": "found",
                    "evidence": supplier[1].strip(),
                    "page": page,
                }
                if supplier
                else MISSING
            ),
            "total": (
                {
                    "value": total[1],
                    "status": "found",
                    "evidence": f"Total: {total[1]}",
                    "page": page,
                }
                if total
                else MISSING
            ),
        }
        found["invoice_date"] = {
            "value": "2026-03-15",
            "status": "found",
            "evidence": "Invoice Date: 2026-03-15",
            "page": page,
        }
        reply = {
            name: MISSING for name in ("due_date", "customer_name", "currency", "subtotal", "tax")
        }
        return reply | found | {"line_items": []}


async def _document(session, path):
    data = path.read_bytes()
    doc = await Repository(session).create_document(
        filename=path.name,
        sha256=hashlib.sha256(data).hexdigest(),
        mime_type="application/pdf",
        size_bytes=len(data),
        storage_path=str(path),
    )
    await session.commit()
    return doc.id


async def _invoices(session, doc_id):
    return (
        await session.scalars(
            select(m.Invoice)
            .where(m.Invoice.document_id == doc_id)
            .order_by(m.Invoice.segment_index)
            .execution_options(populate_existing=True)
        )
    ).all()


async def test_three_invoices_split_with_correct_page_ranges(db_session, tmp_path, settings):
    doc_id = await _document(db_session, make_pdf(tmp_path / "batch.pdf", BATCH))
    llm = BatchLLM()

    outcome = await process_document(doc_id, db_session, llm, settings)

    invoices = await _invoices(db_session, doc_id)
    assert outcome.status == m.DocumentStatus.PROCESSED
    assert [(i.segment_index, i.page_start, i.page_end) for i in invoices] == [
        (0, 1, 1),
        (1, 2, 3),
        (2, 4, 4),
    ]  # the 2-page invoice stays one
    assert [i.invoice_number for i in invoices] == ["A-100", "B-200", "C-300"]
    assert [str(i.total) for i in invoices] == ["10.00", "20.00", "10.00"]
    assert sorted(llm.extractions) == ["A-100", "B-200", "C-300"] and llm.classified == []
    job = await db_session.scalar(select(m.ProcessingJob))
    assert (job.segments_done, job.segments_total) == (3, 3)
    assert invoices[1].fields["total"]["page"] == 3  # evidence keeps the document's page number


async def test_unclear_pages_are_classified_by_the_llm(db_session, tmp_path, settings):
    pages = invoice_pages("A-100", "ACME Ltd", "10.00") + [["Delivery note", "Box 7 of 9"]]
    pages += [["Shipping label", "Fragile"]]
    doc_id = await _document(db_session, make_pdf(tmp_path / "unclear.pdf", pages))
    llm = BatchLLM(starts={3: True})  # page 2 continues invoice 1, page 3 starts a new one

    await process_document(doc_id, db_session, llm, settings)

    assert llm.classified == [2, 3]  # one batch, only the unclear pages
    invoices = await _invoices(db_session, doc_id)
    assert [(i.page_start, i.page_end) for i in invoices] == [(1, 2), (3, 3)]


async def test_failure_in_segment_two_then_retry_redoes_only_segment_two(
    api, run_job, tmp_path, monkeypatch
):
    import app.pipeline as pipeline

    parses = []
    real_parse = pipeline.parse_document
    monkeypatch.setattr(
        pipeline, "parse_document", lambda *a, **kw: parses.append(1) or real_parse(*a, **kw)
    )
    pdf = make_pdf(tmp_path / "batch.pdf", BATCH).read_bytes()
    uploaded = (await api.post("/documents", files=[("files", ("batch.pdf", pdf))])).json()[
        "results"
    ][0]
    llm = BatchLLM(fail={"B-200": 1})

    with pytest.raises(Retry):
        await run_job(uploaded["job_id"], llm)
    status = (await api.get(f"/documents/{uploaded['document_id']}/status")).json()
    assert (status["segments_done"], status["segments_total"]) == (2, 3)
    assert "invoice 2 (pages 2-3)" in status["error"]

    assert await run_job(uploaded["job_id"], llm) == "processed"
    assert sorted(llm.extractions) == ["A-100", "B-200", "B-200", "C-300"]  # only #2 again
    assert len(parses) == 1  # the retry reused the saved page texts (no re-parse/OCR)
    status = (await api.get(f"/documents/{uploaded['document_id']}/status")).json()
    assert (status["segments_done"], status["segments_total"], status["attempts"]) == (3, 3, 2)


async def test_reprocess_gives_the_same_rows(db_session, tmp_path, settings):
    doc_id = await _document(db_session, make_pdf(tmp_path / "batch.pdf", BATCH))
    first = await process_document(doc_id, db_session, BatchLLM(), settings)
    second = await process_document(doc_id, db_session, BatchLLM(), settings)

    assert first.invoice_ids == second.invoice_ids and len(first.invoice_ids) == 3
    assert await db_session.scalar(select(func.count()).select_from(m.Invoice)) == 3
    assert await db_session.scalar(select(func.count()).select_from(m.PageText)) == 4


async def test_changed_boundaries_keep_corrections_and_ask_for_review(
    db_session, tmp_path, settings
):
    pages = invoice_pages("A-100", "ACME Ltd", "10.00") + [["Delivery note", "Box 7"]]
    doc_id = await _document(db_session, make_pdf(tmp_path / "b.pdf", pages))
    await process_document(doc_id, db_session, BatchLLM(starts={2: True}), settings)
    [first, second] = await _invoices(db_session, doc_id)
    db_session.add(
        m.Correction(
            invoice_id=second.id, field="total", old_value=None, new_value="5.00", corrected_by="bo"
        )
    )
    await db_session.commit()

    # This time the classifier joins page 2 to invoice 1: the second invoice disappears.
    outcome = await process_document(doc_id, db_session, BatchLLM(starts={2: False}), settings)

    [only] = await _invoices(db_session, doc_id)
    # Pages 1-2 match neither old range (1-1, 2-2): a new row replaces both.
    assert only.id not in (first.id, second.id)
    assert (only.page_start, only.page_end) == (1, 2)
    corrections = (await db_session.scalars(select(m.Correction))).all()
    assert [(c.invoice_id, c.new_value) for c in corrections] == [(only.id, "5.00")]  # kept
    issues = (
        await db_session.scalars(
            select(m.ValidationIssue).where(m.ValidationIssue.invoice_id == only.id)
        )
    ).all()
    assert any(i.code == "SEGMENT_CHANGED" for i in issues)
    assert only.needs_review and outcome.status == m.DocumentStatus.NEEDS_REVIEW


async def test_duplicate_invoice_inside_one_batch_is_flagged(db_session, tmp_path, settings):
    # The same invoice scanned twice, with another in between (back to back, the repeated
    # number would rightly read as one invoice continuing).
    acme = invoice_pages("A-100", "ACME Ltd", "10.00")
    pages = acme + invoice_pages("B-200", "Globex Corp", "20.00") + acme
    doc_id = await _document(db_session, make_pdf(tmp_path / "twice.pdf", pages))
    settings.segment_concurrency = 2

    await process_document(doc_id, db_session, BatchLLM(), settings)

    invoices = await _invoices(db_session, doc_id)
    codes = [
        {
            i.code
            for i in (
                await db_session.scalars(
                    select(m.ValidationIssue).where(m.ValidationIssue.invoice_id == inv.id)
                )
            ).all()
        }
        for inv in invoices
    ]
    # Exactly one of the two (whichever was saved second) points at the other.
    assert sum("DUPLICATE_INVOICE" in c for c in codes) == 1


async def test_api_lists_invoices_per_document_and_search_names_the_invoice(api, run_job, tmp_path):
    pdf = make_pdf(tmp_path / "batch.pdf", BATCH).read_bytes()
    uploaded = (await api.post("/documents", files=[("files", ("batch.pdf", pdf))])).json()[
        "results"
    ][0]
    await run_job(uploaded["job_id"], BatchLLM())
    doc_id = uploaded["document_id"]

    result = (await api.get(f"/documents/{doc_id}/result")).json()
    assert [
        (i["segment_index"], i["page_start"], i["page_end"], i["extracted"])
        for i in result["invoices"]
    ] == [(0, 1, 1, True), (1, 2, 3, True), (2, 4, 4, True)]
    listed = (await api.get("/documents")).json()["items"][0]
    assert listed["invoice_count"] == 3 and listed["invoice"]["invoice_number"] == "A-100"
    status = (await api.get(f"/documents/{doc_id}/status")).json()
    assert (status["segments_done"], status["segments_total"]) == (3, 3)

    hits = (await api.get("/search", params={"q": "Initech", "mode": "keyword"})).json()
    [hit] = hits["items"]
    assert (hit["page_number"], hit["segment_index"], hit["invoice_number"]) == (4, 2, "C-300")
    assert hit["invoice_id"] == result["invoices"][2]["invoice_id"] and hit["invoice_count"] == 3
    # Filters apply to the invoice on the matching page.
    assert (
        await api.get("/search", params={"q": "Initech", "mode": "keyword", "supplier": "acme"})
    ).json()["total"] == 0
    assert (await api.get("/documents", params={"supplier": "globex"})).json()["total"] == 1


async def _upload_batch(api, tmp_path):
    pdf = make_pdf(tmp_path / "batch.pdf", BATCH).read_bytes()
    return (await api.post("/documents", files=[("files", ("batch.pdf", pdf))])).json()["results"][
        0
    ]


@pytest.mark.parametrize("how", ["retry-failed", "reprocess-only-failed"])
async def test_partial_failure_then_retry_redoes_only_the_failed_invoice(
    api, run_job, tmp_path, monkeypatch, how
):
    import app.pipeline as pipeline

    parses = []
    real_parse = pipeline.parse_document
    monkeypatch.setattr(
        pipeline, "parse_document", lambda *a, **kw: parses.append(1) or real_parse(*a, **kw)
    )
    uploaded = await _upload_batch(api, tmp_path)
    doc_id = uploaded["document_id"]

    assert await run_job(uploaded["job_id"], BatchLLM(broken={"B-200"})) == "partially_failed"
    status = (await api.get(f"/documents/{doc_id}/status")).json()
    assert status["status"] == "partially_failed" and status["job_status"] == "failed"
    assert "1 of 3 invoice(s) failed: invoice 2 (pages 2-3)" in status["error"]
    result = (await api.get(f"/documents/{doc_id}/result")).json()["invoices"]
    assert [i["extracted"] for i in result] == [True, False, True]  # the others are kept
    listed = (await api.get("/documents", params={"status": "partially_failed"})).json()
    assert listed["total"] == 1

    if how == "retry-failed":
        retried = (await api.post("/jobs/retry-failed")).json()
        assert retried["requeued"] == 1
        job_id = retried["job_ids"][0]
    else:
        job_id = (
            await api.post(f"/documents/{doc_id}/reprocess", params={"only_failed": "true"})
        ).json()["job_id"]
    fixed = BatchLLM()
    assert await run_job(job_id, fixed) == "processed"

    assert fixed.extractions == ["B-200"]  # only the failed invoice was redone
    assert len(parses) == 1  # page texts reused: no second parse (or OCR)
    result = (await api.get(f"/documents/{doc_id}/result")).json()["invoices"]
    assert [i["extracted"] for i in result] == [True, True, True]
    assert result[1]["fields"]["invoice_number"]["value"] == "B-200"


async def test_full_reprocess_still_redoes_everything(api, run_job, tmp_path):
    uploaded = await _upload_batch(api, tmp_path)
    await run_job(uploaded["job_id"], BatchLLM(broken={"B-200"}))
    job_id = (await api.post(f"/documents/{uploaded['document_id']}/reprocess")).json()["job_id"]
    again = BatchLLM()
    await run_job(job_id, again)
    assert sorted(again.extractions) == ["A-100", "B-200", "C-300"]


async def test_all_invoices_failing_is_plain_failed(db_session, tmp_path, settings):
    doc_id = await _document(db_session, make_pdf(tmp_path / "batch.pdf", BATCH))
    outcome = await process_document(
        doc_id, db_session, BatchLLM(broken={"A-100", "B-200", "C-300"}), settings
    )
    assert outcome.status == m.DocumentStatus.FAILED


async def test_retry_without_llm_keeps_partially_failed(api, run_job, tmp_path):
    uploaded = await _upload_batch(api, tmp_path)
    await run_job(uploaded["job_id"], BatchLLM(broken={"B-200"}))
    job_id = (await api.post("/jobs/retry-failed")).json()["job_ids"][0]

    assert await run_job(job_id, ValueError("LLM_PROVIDER is 'none'")) == "partially_failed"
    result = (await api.get(f"/documents/{uploaded['document_id']}/result")).json()["invoices"]
    assert sum(i["extracted"] for i in result) == 2  # the saved invoices are untouched


async def test_approving_one_invoice_keeps_document_in_review_while_others_wait(
    api, run_job, tmp_path
):
    uploaded = await _upload_batch(api, tmp_path)
    await run_job(uploaded["job_id"], BatchLLM())
    doc_id = uploaded["document_id"]
    first, second, _ = (await api.get(f"/documents/{doc_id}/result")).json()["invoices"]
    for invoice in (first, second):  # clearing a required field makes each need review
        await api.patch(
            f"/invoices/{invoice['invoice_id']}",
            json={"corrected_by": "bo", "invoice_number": None},
        )

    await api.post(
        f"/invoices/{first['invoice_id']}/approve",
        json={"reviewed_by": "bo", "override_reason": "checked on paper"},
    )

    status = (await api.get(f"/documents/{doc_id}/status")).json()["status"]
    assert status == "needs_review"  # the second invoice still needs a look
