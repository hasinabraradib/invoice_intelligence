"""Cross-checks end to end: invoices vs ledger and invoices vs invoices."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.models as m
from tests.test_batch_documents import BATCH, BatchLLM, invoice_pages, make_pdf
from tests.test_ledger_api import upload_ledger

pytestmark = pytest.mark.anyio

LEDGER = [
    [1, "A-100", "2026-03-15", "ACME Ltd", "10.00", "0", "BDT"],  # exact match
    [2, "B-200", "2026-03-15", "Globex Corp", "20.02", "0", "BDT"],  # off by 0.02
    [3, "D-999", "2026-03-15", "Hooli", "5.00", "0", "BDT"],  # not in the PDF
    [4, "A-100", "2026-03-15", "ACME Ltd", "10.00", "0", "BDT"],  # duplicate row
]  # C-300 from the PDF is missing in the ledger


@pytest.fixture
async def run_check(settings, database_url):
    from app.worker import run_cross_check_job

    engine = create_async_engine(database_url, poolclass=NullPool)

    async def run(check_id):
        ctx = {
            "settings": settings,
            "sessionmaker": async_sessionmaker(engine, expire_on_commit=False),
        }
        return await run_cross_check_job(ctx, str(check_id))

    yield run
    await engine.dispose()


async def upload_pdf(api, run_job, tmp_path, pages=BATCH, name="batch.pdf"):
    data = make_pdf(tmp_path / name, pages).read_bytes()
    uploaded = (await api.post("/documents", files=[("files", (name, data))])).json()["results"][0]
    await run_job(uploaded["job_id"], BatchLLM())
    return uploaded["document_id"]


async def checked(api, run_check, a, b):
    response = await api.post("/cross-checks", json={"document_a_id": a, "document_b_id": b})
    assert response.status_code == 202, response.json()
    assert await run_check(response.json()["id"]) == "succeeded"
    return (await api.get(f"/cross-checks/{response.json()['id']}")).json()


async def test_invoices_vs_ledger(api, run_job, run_check, queue, tmp_path):
    pdf = await upload_pdf(api, run_job, tmp_path)
    ledger = await upload_ledger(api, run_job, tmp_path, rows=LEDGER)

    check = await checked(api, run_check, ledger, pdf)  # order given: ledger first

    assert check["document_a"]["id"] == pdf and check["document_b"]["id"] == ledger  # swapped
    assert check["mode"] == "invoices_vs_ledger" and len(queue.cross_check_ids) == 1
    assert check["summary"] == {
        "matched": 1,
        "mismatched": 1,
        "missing_in_a": 1,
        "missing_in_b": 1,
        "duplicates_in_b": 1,
        "findings": 5,
    }
    by_kind = {f["kind"]: f for f in check["findings"]}
    amount = by_kind["amount_mismatch"]
    assert (amount["field"], amount["value_a"], amount["value_b"]) == ("total", "20.00", "20.02")
    assert (amount["segment_a"], amount["page_a"], amount["sheet_b"], amount["row_b"]) == (
        1,
        3,
        "Purchases",
        5,
    )  # invoice 2's total is on page 3; ledger row 5
    assert (
        by_kind["missing_in_pdf"]["value_b"] == "D-999" and by_kind["missing_in_pdf"]["row_b"] == 6
    )
    assert by_kind["missing_in_ledger"]["value_a"] == "C-300"
    assert by_kind["duplicate_in_ledger"]["row_b"] == 7
    assert by_kind["matched"]["value_a"] == "10.00"


async def test_mismatch_sets_needs_review_and_survives_revalidation(
    api, run_job, run_check, tmp_path, db_session
):
    pdf = await upload_pdf(api, run_job, tmp_path)
    ledger = await upload_ledger(api, run_job, tmp_path, rows=LEDGER)
    invoices = (await api.get(f"/documents/{pdf}/result")).json()["invoices"]
    assert not any(i["needs_review"] for i in invoices)

    await checked(api, run_check, pdf, ledger)

    invoices = {
        i["fields"]["invoice_number"]["value"]: i
        for i in (await api.get(f"/documents/{pdf}/result")).json()["invoices"]
    }
    flagged = {n for n, i in invoices.items() if i["needs_review"]}
    assert flagged == {"B-200", "C-300"}  # amount mismatch, missing in ledger
    issue = next(i for i in invoices["B-200"]["issues"] if i["code"] == "CROSS_CHECK_MISMATCH")
    assert issue["severity"] == "error" and (issue["expected"], issue["actual"]) == (
        "20.02",
        "20.00",
    )
    assert str(invoices["B-200"]["fields"]["total"]["value"]) == "20.00"  # value never changed
    assert (await api.get(f"/documents/{pdf}/status")).json()["status"] == "needs_review"

    # A correction re-runs validation; the cross-check issue stays.
    body = (
        await api.patch(
            f"/invoices/{invoices['B-200']['invoice_id']}",
            json={"corrected_by": "bo", "customer_name": "Buyer"},
        )
    ).json()
    assert any(i["code"] == "CROSS_CHECK_MISMATCH" for i in body["issues"])
    assert body["needs_review"] is True


async def test_rerun_is_idempotent_and_clears_resolved_issues(
    api, run_job, run_check, tmp_path, db_session
):
    pdf = await upload_pdf(api, run_job, tmp_path)
    ledger = await upload_ledger(api, run_job, tmp_path, rows=LEDGER[:2])  # A-100, B-200

    first = await checked(api, run_check, pdf, ledger)
    again = await checked(api, run_check, pdf, ledger)

    assert again["id"] == first["id"] and again["findings"] == first["findings"]
    issues = (
        await db_session.scalars(
            select(m.ValidationIssue).where(m.ValidationIssue.cross_check_id == first["id"])
        )
    ).all()
    assert sorted(i.field for i in issues) == ["invoice_number", "total"]  # not doubled

    # Fix the ledger's mapping so its totals no longer differ... by correcting the invoice.
    b200 = (await api.get(f"/documents/{pdf}/result")).json()["invoices"][1]
    await api.patch(
        f"/invoices/{b200['invoice_id']}", json={"corrected_by": "bo", "total": "20.02"}
    )
    final = await checked(api, run_check, pdf, ledger)
    assert "amount_mismatch" not in {f["kind"] for f in final["findings"]}
    b200 = (await api.get(f"/documents/{pdf}/result")).json()["invoices"][1]
    assert not any(i["code"] == "CROSS_CHECK_MISMATCH" for i in b200["issues"])


async def test_invoices_vs_invoices(api, run_job, run_check, tmp_path):
    first = await upload_pdf(api, run_job, tmp_path)
    changed = (
        invoice_pages("A-100", "ACME Ltd", "10.00")
        + invoice_pages("B-200", "Globex Corp", "20.00", pages=2)
        + invoice_pages("C-300", "Initech", "12.00")
    )
    second = await upload_pdf(api, run_job, tmp_path, pages=changed, name="copy.pdf")

    check = await checked(api, run_check, first, second)

    assert check["mode"] == "invoices_vs_invoices"
    assert check["summary"]["matched"] == 2 and check["summary"]["mismatched"] == 1
    [diff] = [f for f in check["findings"] if f["severity"] == "error"]
    assert (diff["value_a"], diff["value_b"], diff["page_a"], diff["page_b"]) == (
        "10.00",
        "12.00",
        4,
        4,
    )
    for doc in (first, second):  # both invoices involved are flagged
        c300 = (await api.get(f"/documents/{doc}/result")).json()["invoices"][2]
        assert c300["needs_review"]


async def test_errors_and_listing(api, run_job, run_check, tmp_path):
    pdf = await upload_pdf(api, run_job, tmp_path)
    ledger = await upload_ledger(api, run_job, tmp_path, rows=LEDGER)
    other = await upload_ledger(api, run_job, tmp_path, rows=LEDGER[:2], name="b.xlsx")

    async def post(a, b):
        return await api.post("/cross-checks", json={"document_a_id": a, "document_b_id": b})

    assert (await post(pdf, pdf)).json()["error"]["code"] == "same_document"
    assert (await post(ledger, other)).json()["error"]["code"] == "nothing_to_compare"
    missing = await post(pdf, "00000000-0000-0000-0000-000000000000")
    assert missing.status_code == 404
    queued = (
        await api.post(
            "/documents",
            files=[("files", ("new.pdf", make_pdf(tmp_path / "n.pdf", BATCH[:1]).read_bytes()))],
        )
    ).json()["results"][0]
    not_ready = await post(pdf, queued["document_id"])
    assert not_ready.status_code == 409 and not_ready.json()["error"]["code"] == "not_ready"

    first = (await post(pdf, ledger)).json()
    second = (await post(pdf, ledger)).json()  # still queued: same check, not queued twice
    assert first["id"] == second["id"] and first["status"] == "queued"
    listed = (await api.get("/cross-checks", params={"document_id": ledger})).json()
    assert listed["total"] == 1 and listed["items"][0]["id"] == first["id"]
    assert (await api.get("/cross-checks/00000000-0000-0000-0000-000000000000")).status_code == 404
