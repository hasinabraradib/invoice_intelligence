"""Endpoints the review UI needs: files, page previews, corrections, approval, lists."""

import io

import pytest
from PIL import Image

from app.extraction.llm import FakeLLMClient
from tests import factories
from tests.test_api import _processed, _upload
from tests.test_pipeline import GOOD_REPLY

pytestmark = pytest.mark.anyio


async def _result(api, doc_id):
    return (await api.get(f"/documents/{doc_id}/result")).json()["invoices"][0]


async def test_original_file_is_served_inline_safely(api, run_job, tmp_path):
    pdf = factories.text_pdf(tmp_path / "a.pdf", 1).read_bytes()
    uploaded = await _upload(api, "../../evil.pdf", pdf)

    response = await api.get(f"/documents/{uploaded['document_id']}/file")

    assert response.status_code == 200 and response.content == pdf
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].startswith("inline")
    assert 'filename="evil.pdf"' in response.headers["content-disposition"]
    assert response.headers["x-content-type-options"] == "nosniff"


async def test_pdf_page_previews_are_rendered_and_cached(api, run_job, tmp_path, upload_dir):
    uploaded = await _processed(api, run_job, tmp_path, pages=2)
    doc_id = uploaded["document_id"]

    pages = (await api.get(f"/documents/{doc_id}/pages")).json()
    assert [(p["page_number"], p["source"], p["has_image"]) for p in pages] == [
        (1, "text_layer", True),
        (2, "text_layer", True),
    ]
    first = await api.get(f"/documents/{doc_id}/pages/2/image")
    assert first.status_code == 200 and first.headers["content-type"] == "image/png"
    assert Image.open(io.BytesIO(first.content)).width == 1200
    cached = list((upload_dir / "previews" / doc_id).iterdir())
    assert [p.name for p in cached] == ["2-1200.png"]
    second = await api.get(f"/documents/{doc_id}/pages/2/image")
    assert second.content == first.content  # served from the cache

    missing = await api.get(f"/documents/{doc_id}/pages/3/image")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "page_not_found"


async def test_tiff_frames_and_small_images(api, tmp_path):
    tiff = factories.image(tmp_path / "scan.tiff", "TIFF", frames=3).read_bytes()
    doc_id = (await _upload(api, "scan.tiff", tiff))["document_id"]

    frame = await api.get(f"/documents/{doc_id}/pages/3/image")

    assert frame.status_code == 200
    assert Image.open(io.BytesIO(frame.content)).size == (60, 40)  # small: not blown up
    assert (await api.get(f"/documents/{doc_id}/pages/4/image")).status_code == 404


async def test_spreadsheets_have_rows_but_no_page_images(api, run_job, tmp_path):
    xlsx = factories.invoice_xlsx(tmp_path / "inv.xlsx").read_bytes()
    uploaded = await _upload(api, "inv.xlsx", xlsx)
    await run_job(uploaded["job_id"], FakeLLMClient(GOOD_REPLY))

    pages = (await api.get(f"/documents/{uploaded['document_id']}/pages")).json()
    assert [(p["source"], p["has_image"]) for p in pages] == [("sheet", False), ("sheet", False)]
    assert "Description\tQty\tUnit price\tAmount" in pages[0]["text"]
    response = await api.get(f"/documents/{uploaded['document_id']}/pages/1/image")
    assert response.status_code == 404 and response.json()["error"]["code"] == "no_preview"


async def test_corrections_history(api, run_job, tmp_path):
    uploaded = await _processed(api, run_job, tmp_path)
    invoice_id = (await _result(api, uploaded["document_id"]))["invoice_id"]
    await api.patch(f"/invoices/{invoice_id}", json={"corrected_by": "bo", "total": "4.60"})

    history = (await api.get(f"/invoices/{invoice_id}/corrections")).json()

    assert [(h["field"], h["old_value"], h["new_value"], h["corrected_by"]) for h in history] == [
        ("total", "4.50", "4.60", "bo")
    ]
    assert (
        await api.get("/invoices/00000000-0000-0000-0000-000000000000/corrections")
    ).status_code == 404


async def test_approve_rules_and_reopening(api, run_job, tmp_path):
    uploaded = await _processed(api, run_job, tmp_path)
    doc_id = uploaded["document_id"]
    invoice_id = (await _result(api, doc_id))["invoice_id"]
    url = f"/invoices/{invoice_id}"

    # Make an error: subtotal no longer matches the line items.
    await api.patch(url, json={"corrected_by": "bo", "subtotal": "5.00"})
    refused = await api.post(f"{url}/approve", json={"reviewed_by": "bo"})
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "unresolved_errors"
    blank = await api.post(f"{url}/approve", json={"reviewed_by": "bo", "override_reason": " "})
    assert blank.status_code == 409  # whitespace is not a reason

    approved = await api.post(
        f"{url}/approve",
        json={"reviewed_by": "bo", "override_reason": "Supplier confirmed by phone"},
    )
    assert approved.status_code == 200
    body = approved.json()
    assert body["needs_review"] is False and body["reviewed_by"] == "bo"
    assert body["review_override_reason"] == "Supplier confirmed by phone"
    assert (await api.get(f"/documents/{doc_id}/status")).json()["status"] == "processed"

    # A correction that adds no NEW error keeps the approval...
    body = (await api.patch(url, json={"corrected_by": "bo", "customer_name": "Buyer"})).json()
    assert body["needs_review"] is False and body["reviewed_by"] == "bo"
    # ...and so does re-processing with the same values.
    job = (await api.post(f"/documents/{doc_id}/reprocess")).json()
    await run_job(job["job_id"], FakeLLMClient(GOOD_REPLY))
    assert (await _result(api, doc_id))["needs_review"] is False

    # A new error (required field cleared) re-opens the review and clears the approval.
    body = (await api.patch(url, json={"corrected_by": "bo", "invoice_number": None})).json()
    assert body["needs_review"] is True and body["reviewed_by"] is None
    assert (await api.get(f"/documents/{doc_id}/status")).json()["status"] == "needs_review"

    history = (await api.get(f"{url}/corrections")).json()
    assert any(h["field"] == "approval" and "override" in h["new_value"] for h in history)


async def test_clean_invoice_approves_without_reason(api, run_job, tmp_path):
    uploaded = await _processed(api, run_job, tmp_path)
    invoice_id = (await _result(api, uploaded["document_id"]))["invoice_id"]
    response = await api.post(f"/invoices/{invoice_id}/approve", json={"reviewed_by": "bo"})
    assert response.status_code == 200 and response.json()["review_override_reason"] is None


async def test_list_has_job_progress_and_oldest_first(api, run_job, tmp_path):
    await _processed(api, run_job, tmp_path, name="first.pdf")
    await _upload(api, "second.pdf", factories.text_pdf(tmp_path / "s.pdf", 3).read_bytes())

    newest = (await api.get("/documents")).json()["items"]
    oldest = (await api.get("/documents", params={"order": "oldest"})).json()["items"]

    assert [d["filename"] for d in newest] == ["second.pdf", "first.pdf"]
    assert [d["filename"] for d in oldest] == ["first.pdf", "second.pdf"]
    assert newest[0]["job"]["status"] == "queued" and newest[0]["job"]["pages_done"] == 0
    assert newest[1]["job"]["status"] == "succeeded" and newest[1]["job"]["stage"] == "embedding"
