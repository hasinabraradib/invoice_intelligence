"""Ledger spreadsheets: detected during processing, stored by row, mapping can be fixed."""

import pytest
from openpyxl import Workbook

from app.extraction.llm import FakeLLMClient

pytestmark = pytest.mark.anyio

HEADER = ["Sl", "Inv #", "Bill Date", "Vendor", "Amount (BDT)", "VAT", "Currency"]


def ledger_xlsx(path, rows, header=HEADER):
    """A purchase ledger: title row, blank row, header on Excel row 3, data from row 4."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Purchases"
    ws.append(["Purchase ledger March 2026"])
    ws.append([])
    ws.cell(row=3, column=1)  # keep row 2 blank
    for column, value in enumerate(header, start=1):
        ws.cell(row=3, column=column, value=value)
    for r, values in enumerate(rows, start=4):
        for column, value in enumerate(values, start=1):
            ws.cell(row=r, column=column, value=value)
    notes = wb.create_sheet("Notes")
    notes.append(["Prepared by", "Accounts"])
    wb.save(path)
    return path


ROWS = [
    [1, "A-100", "2026-03-15", "ACME Ltd", "10.00", "0", "BDT"],
    [2, "B-200", "2026-03-15", "Globex Corp", "20.00", "0", "BDT"],
    [3, "D-999", "15/03/2026", "Hooli", "5.00", "0", "BDT"],
]


class NoLLM(FakeLLMClient):
    """Fails the test if the LLM is called: ledgers must not be sent to it."""

    def __init__(self):
        super().__init__(AssertionError("the LLM must not be called for a ledger"))


async def upload_ledger(api, run_job, tmp_path, rows=ROWS, name="ledger.xlsx"):
    data = ledger_xlsx(tmp_path / name, rows).read_bytes()
    uploaded = (await api.post("/documents", files=[("files", (name, data))])).json()["results"][0]
    assert await run_job(uploaded["job_id"], NoLLM()) == "processed"
    return uploaded["document_id"]


async def test_ledger_is_detected_mapped_and_stored_by_row(api, run_job, tmp_path):
    doc_id = await upload_ledger(api, run_job, tmp_path)

    ledger = (await api.get(f"/documents/{doc_id}/ledger")).json()
    purchases, notes = ledger["sheets"]
    assert purchases["is_ledger"] and purchases["source"] == "auto" and not notes["is_ledger"]
    assert purchases["columns"] == {
        "invoice_number": "Inv #",
        "invoice_date": "Bill Date",
        "supplier_name": "Vendor",
        "total": "Amount (BDT)",
        "tax": "VAT",
        "currency": "Currency",
    }
    rows = ledger["rows"]["items"]
    assert ledger["rows"]["total"] == 3  # the Notes sheet is not a ledger
    assert [(r["row_number"], r["invoice_number"], r["total"]) for r in rows] == [
        (4, "A-100", "10.00"),
        (5, "B-200", "20.00"),
        (6, "D-999", "5.00"),
    ]
    assert rows[2]["invoice_date"] == "2026-03-15"  # 15/03 is unambiguous (no month 15)
    # A ledger is a list of invoices, not one invoice: nothing was extracted by the LLM.
    assert (await api.get(f"/documents/{doc_id}/result")).status_code == 404


async def test_manual_mapping_override_remaps_and_survives_reprocessing(api, run_job, tmp_path):
    doc_id = await upload_ledger(api, run_job, tmp_path)

    response = await api.put(
        f"/documents/{doc_id}/column-mapping",
        json={"sheet": "Purchases", "columns": {"total": "Sl"}},
    )
    assert response.status_code == 200
    assert response.json()["source"] == "manual" and response.json()["columns"]["total"] == "Sl"
    rows = (await api.get(f"/documents/{doc_id}/ledger")).json()["rows"]["items"]
    assert [r["total"] for r in rows] == ["1.00", "2.00", "3.00"]  # re-mapped from stored cells

    job = (await api.post(f"/documents/{doc_id}/reprocess")).json()
    await run_job(job["job_id"], NoLLM())
    rows = (await api.get(f"/documents/{doc_id}/ledger")).json()["rows"]["items"]
    assert [r["total"] for r in rows] == ["1.00", "2.00", "3.00"]  # manual mapping kept


async def test_mapping_errors(api, run_job, tmp_path):
    doc_id = await upload_ledger(api, run_job, tmp_path)
    bad_column = await api.put(
        f"/documents/{doc_id}/column-mapping",
        json={"sheet": "Purchases", "columns": {"total": "Nope"}},
    )
    assert bad_column.status_code == 422 and bad_column.json()["error"]["code"] == "invalid_mapping"
    bad_sheet = await api.put(
        f"/documents/{doc_id}/column-mapping", json={"sheet": "Missing", "columns": {}}
    )
    assert bad_sheet.status_code == 422
    bad_field = await api.put(
        f"/documents/{doc_id}/column-mapping",
        json={"sheet": "Purchases", "columns": {"colour": "Sl"}},
    )
    assert bad_field.status_code == 422 and bad_field.json()["error"]["code"] == "validation_error"


async def test_unmapping_the_invoice_number_turns_the_ledger_off(api, run_job, tmp_path):
    doc_id = await upload_ledger(api, run_job, tmp_path)
    entry = (
        await api.put(
            f"/documents/{doc_id}/column-mapping",
            json={"sheet": "Purchases", "columns": {"invoice_number": None}},
        )
    ).json()
    assert entry["is_ledger"] is False
    assert (await api.get(f"/documents/{doc_id}/ledger")).json()["rows"]["total"] == 0
