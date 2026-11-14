"""Upload -> worker job -> status/result/corrections/search, through the real API."""

import shutil
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from arq import Retry
from reportlab.pdfgen import canvas
from sqlalchemy import select

import app.models as m
from app.extraction.llm import FakeLLMClient, LLMError
from app.extraction.postprocess import normalise_invoice_number
from app.jobs import requeue_stuck_jobs
from app.repository import Repository
from tests import factories
from tests.test_pipeline import GOOD_REPLY

pytestmark = pytest.mark.anyio


async def _upload(api, name, data):
    response = await api.post("/documents", files=[("files", (name, data, "application/pdf"))])
    assert response.status_code == 202
    return response.json()["results"][0]


async def _processed(api, run_job, tmp_path, name="inv.pdf", pages=1, reply=GOOD_REPLY):
    uploaded = await _upload(
        api, name, factories.text_pdf(tmp_path / name, pages=pages).read_bytes()
    )
    assert await run_job(uploaded["job_id"], FakeLLMClient(reply)) in ("processed", "needs_review")
    return uploaded


async def test_upload_then_job_gives_processed_result_with_evidence(api, run_job, tmp_path):
    uploaded = await _processed(api, run_job, tmp_path, pages=2)
    doc_id = uploaded["document_id"]

    status = (await api.get(f"/documents/{doc_id}/status")).json()
    assert status["status"] == "processed" and status["job_status"] == "succeeded"
    assert (status["stage"], status["pages_done"], status["pages_total"]) == ("embedding", 2, 2)
    assert status["attempts"] == 1 and status["error"] is None

    result = (await api.get(f"/documents/{doc_id}/result")).json()["invoices"][0]
    assert result["fields"]["total"]["value"] == "4.50"
    assert result["fields"]["total"]["evidence"] == "Total due: 4.50 EUR"
    assert result["fields"]["total"]["page"] == 1 and result["fields"]["total"]["confidence"] > 0.9
    assert result["line_items"][0]["line_total"]["value"] == "4.50"
    assert result["duplicates"] == []

    detail = (await api.get(f"/documents/{doc_id}")).json()
    assert detail["latest_job"]["status"] == "succeeded" and detail["page_count"] == 2
    assert detail["invoice"]["invoice_number"] == "INV-2024-001"


async def test_temporary_429_is_retried_then_succeeds(api, run_job, tmp_path):
    uploaded = await _upload(api, "inv.pdf", factories.text_pdf(tmp_path / "a.pdf", 1).read_bytes())
    llm = FakeLLMClient(LLMError("HTTP 429 after 4 attempts", retryable=True), GOOD_REPLY)

    with pytest.raises(Retry) as retry:
        await run_job(uploaded["job_id"], llm)
    assert 5 <= retry.value.defer_score / 1000 <= 10  # ~10 s backoff with jitter (in ms)
    status = (await api.get(f"/documents/{uploaded['document_id']}/status")).json()
    assert status["job_status"] == "queued" and status["attempts"] == 1
    assert "will retry" in status["error"]

    assert await run_job(uploaded["job_id"], llm) == "processed"  # arq runs it again
    status = (await api.get(f"/documents/{uploaded['document_id']}/status")).json()
    assert status["status"] == "processed" and status["attempts"] == 2


async def test_temporary_errors_stop_after_max_attempts(api, run_job, tmp_path):
    uploaded = await _upload(api, "inv.pdf", factories.text_pdf(tmp_path / "a.pdf", 1).read_bytes())
    llm = FakeLLMClient(LLMError("HTTP 503 after 4 attempts", retryable=True))

    for _ in range(2):
        with pytest.raises(Retry):
            await run_job(uploaded["job_id"], llm)
    assert await run_job(uploaded["job_id"], llm) == "failed"  # 3rd attempt = JOB_MAX_ATTEMPTS
    status = (await api.get(f"/documents/{uploaded['document_id']}/status")).json()
    assert status["status"] == "failed" and status["attempts"] == 3


async def test_corrupt_file_fails_without_retry(api, run_job, tmp_path):
    broken = factories.truncated_pdf(tmp_path / "broken.pdf").read_bytes()
    uploaded = await _upload(api, "broken.pdf", broken)  # magic bytes are fine: accepted

    assert await run_job(uploaded["job_id"], FakeLLMClient(GOOD_REPLY)) == "failed"  # no Retry
    status = (await api.get(f"/documents/{uploaded['document_id']}/status")).json()
    assert status["status"] == "failed" and status["attempts"] == 1
    assert status["error"] == "The PDF is damaged or unreadable."
    assert await run_job(uploaded["job_id"], FakeLLMClient(GOOD_REPLY)) == "skipped"


async def test_redelivered_running_job_is_taken_back(api, run_job, db_session, tmp_path):
    # The worker was restarted mid-job: the row still says "running" and arq redelivers.
    uploaded = await _upload(api, "inv.pdf", factories.text_pdf(tmp_path / "a.pdf", 1).read_bytes())
    job_id = uuid.UUID(uploaded["job_id"])
    await Repository(db_session).update_job(job_id, status=m.JobStatus.RUNNING, stage="extracting")
    await db_session.commit()

    # A first delivery of a running job is a duplicate: left alone.
    assert await run_job(uploaded["job_id"], FakeLLMClient(GOOD_REPLY)) == "skipped"
    # A redelivery (job_try > 1) means the attempt holding it died: run it again.
    assert await run_job(uploaded["job_id"], FakeLLMClient(GOOD_REPLY), job_try=2) == "processed"
    status = (await api.get(f"/documents/{uploaded['document_id']}/status")).json()
    assert status["status"] == "processed" and status["job_status"] == "succeeded"


async def test_llm_usage_is_saved_on_the_job_and_summed_over_attempts(api, run_job, tmp_path):
    uploaded = await _upload(api, "inv.pdf", factories.text_pdf(tmp_path / "a.pdf", 1).read_bytes())
    flaky = FakeLLMClient(LLMError("HTTP 503 after 4 attempts", retryable=True))
    flaky.usage.calls, flaky.usage.input_tokens = 1, 50  # billed before it failed
    with pytest.raises(Retry):
        await run_job(uploaded["job_id"], flaky)

    llm = FakeLLMClient(GOOD_REPLY)
    llm.usage.calls, llm.usage.input_tokens, llm.usage.output_tokens = 1, 900, 120
    llm.usage.cached_input_tokens = 800
    assert await run_job(uploaded["job_id"], llm) == "processed"

    job = (await api.get(f"/documents/{uploaded['document_id']}")).json()["latest_job"]
    assert (job["llm_calls"], job["llm_input_tokens"], job["llm_output_tokens"]) == (2, 950, 120)
    assert job["llm_cached_input_tokens"] == 800


async def test_embedding_failure_in_the_worker_finishes_the_job_with_a_warning(
    api, run_job, tmp_path
):
    """Regression: the pipeline rolls back after a failed embedding, which expired the
    worker's `job` object; reading job.id afterwards crashed with MissingGreenlet."""
    from app.search.embedder import FakeEmbedder

    uploaded = await _upload(api, "inv.pdf", factories.text_pdf(tmp_path / "a.pdf", 1).read_bytes())
    llm = FakeLLMClient(GOOD_REPLY)
    llm.usage.calls = 1  # so the usage is saved after the rollback
    outcome = await run_job(uploaded["job_id"], llm, embedder=FakeEmbedder(fail=True))
    assert outcome in ("processed", "needs_review")
    status = (await api.get(f"/documents/{uploaded['document_id']}/status")).json()
    assert status["job_status"] == "succeeded" and "keyword search still works" in status["error"]


async def test_stuck_running_job_is_requeued(db_session, queue):
    repo = Repository(db_session)
    doc = await repo.create_document(
        filename="a.pdf",
        sha256="b" * 64,
        mime_type="application/pdf",
        size_bytes=1,
        storage_path="/x",
    )
    stuck, fresh = await repo.create_job(doc.id), await repo.create_job(doc.id)
    long_ago = datetime.now(UTC) - timedelta(minutes=45)
    await repo.update_job(stuck.id, status=m.JobStatus.RUNNING, started_at=long_ago, stage="ocr")
    await repo.update_job(fresh.id, status=m.JobStatus.RUNNING, started_at=datetime.now(UTC))
    await db_session.commit()

    assert await requeue_stuck_jobs(repo, queue, minutes=30) == 1

    assert queue.job_ids == [stuck.id]
    job = await db_session.get(m.ProcessingJob, stuck.id, populate_existing=True)
    assert job.status == m.JobStatus.QUEUED and "requeued" in job.error


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract missing")
async def test_ocr_progress_is_recorded_page_by_page(db_session, tmp_path, settings, monkeypatch):
    from app.pipeline import process_document

    # OCR runs in separate worker processes (real Tesseract on tiny blank frames), so it
    # cannot be faked from here; progress is still reported once per finished page.
    seen: list[tuple[str, int]] = []
    real_update = Repository.update_job

    async def spy(self, job_id, **values):
        if "pages_done" in values:
            seen.append((values.get("stage"), values["pages_done"]))
        return await real_update(self, job_id, **values)

    monkeypatch.setattr(Repository, "update_job", spy)
    path = factories.image(tmp_path / "scan.tiff", "TIFF", frames=3)
    doc = await Repository(db_session).create_document(
        filename="scan.tiff",
        sha256="c" * 64,
        mime_type="image/tiff",
        size_bytes=1,
        storage_path=str(path),
    )
    await db_session.commit()

    settings.ocr_enabled = True
    await process_document(doc.id, db_session, FakeLLMClient(GOOD_REPLY), settings)

    assert [n for stage, n in seen if stage == "ocr"] == [1, 2, 3]
    job = await db_session.scalar(select(m.ProcessingJob))
    assert (job.pages_done, job.pages_total) == (3, 3)


async def test_correction_is_audited_revalidated_and_survives_reprocess(
    api, run_job, tmp_path, db_session
):
    uploaded = await _processed(api, run_job, tmp_path)
    doc_id = uploaded["document_id"]
    invoice_id = (await api.get(f"/documents/{doc_id}/result")).json()["invoices"][0]["invoice_id"]

    # Raise an issue: subtotal no longer matches the line items.
    response = await api.patch(
        f"/invoices/{invoice_id}", json={"corrected_by": "alice", "subtotal": "5.00"}
    )
    assert response.status_code == 200
    result = response.json()
    assert result["fields"]["subtotal"] | {"reasons": None} == {
        "value": "5.00",
        "raw_value": "5.00",
        "status": "corrected",
        "evidence": None,
        "page": None,
        "confidence": 1.0,
        "reasons": None,
    }
    assert "LINE_ITEMS_SUM_MISMATCH" in [i["code"] for i in result["issues"]]
    assert result["needs_review"] is True
    assert (await api.get(f"/documents/{doc_id}/status")).json()["status"] == "needs_review"

    # Clear it again by correcting the line total too.
    result = (
        await api.patch(
            f"/invoices/{invoice_id}",
            json={
                "corrected_by": "alice",
                "line_items": [{"position": 1, "line_total": "5.00"}],
            },
        )
    ).json()
    codes = [i["code"] for i in result["issues"]]
    assert "LINE_ITEMS_SUM_MISMATCH" not in codes and "LINE_ARITHMETIC" in codes  # 3 x 1.50
    assert result["needs_review"] is False

    audit = (
        await db_session.scalars(select(m.Correction).order_by(m.Correction.corrected_at))
    ).all()
    assert [(c.field, c.old_value, c.new_value, c.corrected_by) for c in audit] == [
        ("subtotal", "4.50", "5.00", "alice"),
        ("line_items[1].line_total", "4.50", "5.00", "alice"),
    ]

    # Re-processing (model says 4.50 again) must keep the human values.
    job = (await api.post(f"/documents/{doc_id}/reprocess")).json()
    await run_job(job["job_id"], FakeLLMClient(GOOD_REPLY))
    result = (await api.get(f"/documents/{doc_id}/result")).json()["invoices"][0]
    assert result["invoice_id"] == invoice_id
    assert result["fields"]["subtotal"]["value"] == "5.00"
    assert result["fields"]["subtotal"]["status"] == "corrected"
    assert result["line_items"][0]["line_total"]["value"] == "5.00"


async def test_patch_validates_types(api, run_job, tmp_path):
    uploaded = await _processed(api, run_job, tmp_path)
    invoice_id = (await api.get(f"/documents/{uploaded['document_id']}/result")).json()["invoices"][
        0
    ]["invoice_id"]

    for body in (
        {"corrected_by": "a", "total": "abc"},
        {"corrected_by": "a", "total": "1.234"},
        {"corrected_by": "a", "invoice_date": "15/03/2026"},
        {"corrected_by": "a", "currency": "taka"},
        {"total": "1.00"},
        {"corrected_by": "a", "colour": "red"},
    ):
        response = await api.patch(f"/invoices/{invoice_id}", json=body)
        assert response.status_code == 422, body
        assert response.json()["error"]["code"] == "validation_error"
    response = await api.patch(
        f"/invoices/{invoice_id}",
        json={"corrected_by": "a", "line_items": [{"position": 9, "line_total": "1.00"}]},
    )
    assert response.status_code == 422 and response.json()["error"]["code"] == "unknown_line_item"


async def test_reprocess_conflicts_while_job_active(api, run_job, tmp_path):
    uploaded = await _upload(api, "inv.pdf", factories.text_pdf(tmp_path / "a.pdf", 1).read_bytes())
    url = f"/documents/{uploaded['document_id']}/reprocess"

    conflict = await api.post(url)  # the upload's job is still queued
    assert conflict.status_code == 409
    assert conflict.json() == {
        "error": {
            "code": "job_in_progress",
            "message": "A job for this document is already queued or running.",
        }
    }

    await run_job(uploaded["job_id"], FakeLLMClient(GOOD_REPLY))
    accepted = await api.post(url)
    assert accepted.status_code == 202 and accepted.json()["status"] == "queued"


async def test_retry_failed_counts_documents_once(api, run_job, queue, tmp_path):
    for n in range(2):
        broken = factories.truncated_pdf(tmp_path / f"b{n}.pdf")
        broken.write_bytes(broken.read_bytes() + bytes([n]))  # different hashes
        uploaded = await _upload(api, f"b{n}.pdf", broken.read_bytes())
        await run_job(uploaded["job_id"], FakeLLMClient(GOOD_REPLY))
    await _processed(api, run_job, tmp_path, name="ok.pdf")
    queue.job_ids.clear()

    first = (await api.post("/jobs/retry-failed")).json()
    second = (await api.post("/jobs/retry-failed")).json()

    assert first["requeued"] == 2 and [str(j) for j in queue.job_ids] == first["job_ids"]
    assert second["requeued"] == 0  # their newest jobs are queued now, not failed


def _pdf_with_pages(path, pages: list[str]):
    c = canvas.Canvas(str(path))
    for text in pages:
        for i, line in enumerate(text.splitlines()):
            c.drawString(50, 800 - 20 * i, line)
        c.showPage()
    c.save()
    return path


async def test_search_finds_word_on_right_page_with_snippet_and_filters(api, run_job, tmp_path):
    page1 = "\n".join(factories.INVOICE_LINES)
    path = _pdf_with_pages(tmp_path / "s.pdf", [page1, "Shipping notes\nZebrawood desk <b>x</b>"])
    uploaded = await _upload(api, "s.pdf", path.read_bytes())
    await run_job(uploaded["job_id"], FakeLLMClient(GOOD_REPLY))

    page = (await api.get("/search", params={"q": "zebrawood"})).json()
    assert page["total"] == 1
    [hit] = page["items"]
    assert hit["document_id"] == uploaded["document_id"] and hit["page_number"] == 2
    assert "<mark>Zebrawood</mark>" in hit["snippet"]
    assert "<b>" not in hit["snippet"]  # document markup never reaches the snippet raw
    assert hit["supplier_name"] == "ACME Supplies Ltd" and hit["total"] == "4.50"

    async def total(**params):
        return (await api.get("/search", params={"q": "zebrawood", **params})).json()["total"]

    assert await total(supplier="acme") == 1
    assert await total(supplier="globex") == 0
    assert await total(status="failed") == 0
    assert await total(date_from="2024-01-01", date_to="2024-01-31") == 1
    assert await total(date_from="2025-01-01") == 0
    assert (await api.get("/search", params={"q": "nothing-matches-this"})).json()["total"] == 0


async def test_list_documents_filters_and_paginates(api, run_job, tmp_path):
    for n in range(3):
        await _processed(api, run_job, tmp_path, name=f"i{n}.pdf", pages=n + 1)
    await _upload(api, "new.pdf", factories.text_pdf(tmp_path / "n.pdf", pages=5).read_bytes())

    page = (await api.get("/documents", params={"limit": 2})).json()
    assert page["total"] == 4 and len(page["items"]) == 2 and page["limit"] == 2
    assert page["items"][0]["filename"] == "new.pdf"  # newest first
    assert (await api.get("/documents", params={"status": "uploaded"})).json()["total"] == 1
    review = (await api.get("/documents", params={"needs_review": True})).json()
    assert review["total"] == 2  # same supplier + number as the first one: flagged
    assert (await api.get("/documents", params={"offset": 3})).json()["items"][0][
        "filename"
    ] == "i0.pdf"


async def test_invoice_number_keys_match_across_formats(db_session):
    assert {
        normalise_invoice_number(n)
        for n in ("INV-001", "inv 001", "INV/001", "Inv.001", "INV – 001")
    } == {"INV001"}
    repo = Repository(db_session)
    doc = await repo.create_document(
        filename="a.pdf",
        sha256="d" * 64,
        mime_type="application/pdf",
        size_bytes=1,
        storage_path="/x",
    )
    db_session.add(
        m.Invoice(
            document_id=doc.id,
            supplier_key="acme",
            invoice_number="inv 001",
            invoice_number_key=normalise_invoice_number("inv 001"),
        )
    )
    await db_session.commit()
    for typed in ("INV-001", "INV/001"):
        assert await repo.find_invoices_by_supplier_and_number("acme", typed, uuid.uuid4()) == [
            doc.id
        ]


async def test_error_format_for_404_and_422(api):
    missing = await api.get(f"/documents/{uuid.uuid4()}")
    assert missing.status_code == 404
    assert missing.json() == {"error": {"code": "not_found", "message": "document not found"}}
    bad_id = await api.get("/documents/not-a-uuid/status")
    assert bad_id.status_code == 422
    body = bad_id.json()["error"]
    assert body["code"] == "validation_error" and body["details"][0]["loc"][-1] == "document_id"
    assert (await api.patch(f"/invoices/{uuid.uuid4()}", json={"corrected_by": "a"})).json()[
        "error"
    ]["code"] == "not_found"
    assert (await api.get(f"/documents/{uuid.uuid4()}/result")).status_code == 404


async def test_cors_allows_the_frontend_origin(api):
    response = await api.options(
        "/documents",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"},
    )
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    evil = await api.options(
        "/documents",
        headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in evil.headers


def test_snippet_is_html_escaped_except_marks():
    from app.search.service import _safe_snippet

    raw = "Total \x02due\x03 <script>alert(1)</script> & co"
    assert _safe_snippet(raw) == (
        "Total <mark>due</mark> &lt;script&gt;alert(1)&lt;/script&gt; &amp; co"
    )


async def test_unconfigured_llm_fails_once_without_retry(api, run_job, tmp_path):
    pdf = factories.text_pdf(tmp_path / "a.pdf", 1).read_bytes()
    uploaded = await _upload(api, "inv.pdf", pdf)

    outcome = await run_job(uploaded["job_id"], ValueError("LLM_PROVIDER is 'none'"))

    assert outcome == "failed"
    status = (await api.get(f"/documents/{uploaded['document_id']}/status")).json()
    assert status["status"] == "failed" and status["attempts"] == 1
    assert "LLM_PROVIDER" in status["error"]
