"""Ledger detection and column mapping (pure)."""

from datetime import date
from decimal import Decimal

from app.ledger.mapping import is_ledger, map_columns, normalise_header, row_values


def test_synonyms_map_real_world_headers():
    columns = ["Sl", "Inv #", "Bill Date", "Vendor", "Amount (BDT)", "VAT", "Remarks"]
    assert map_columns(columns) == {
        "invoice_number": "Inv #",
        "invoice_date": "Bill Date",
        "supplier_name": "Vendor",
        "total": "Amount (BDT)",
        "tax": "VAT",
    }


def test_more_specific_synonym_wins_and_columns_are_used_once():
    columns = ["Date", "Invoice Date", "Invoice No.", "Total Amount", "Net Payable"]
    mapping = map_columns(columns)
    assert mapping["invoice_date"] == "Invoice Date"  # not the generic "Date"
    assert mapping["invoice_number"] == "Invoice No."
    assert mapping["total"] == "Total Amount"
    assert len(set(mapping.values())) == len(mapping)


def test_normalise_header():
    assert normalise_header(" Invoice No. (Ref) ") == "invoice no"
    assert normalise_header("Inv #") == "inv #"


def test_ledger_detection():
    ledger = map_columns(["Invoice No", "Supplier", "Total"])
    assert is_ledger(ledger, row_count=5)
    assert not is_ledger(ledger, row_count=1)  # one row is not a list
    line_items = map_columns(["Description", "Qty", "Unit price", "Amount"])
    assert not is_ledger(line_items, row_count=20)  # an invoice's lines, not a ledger
    assert not is_ledger(map_columns(["Invoice No", "Total"]), row_count=5)  # no date/supplier


def test_row_values_are_parsed_without_guessing():
    mapping = {
        "invoice_number": "No",
        "invoice_date": "Date",
        "supplier_name": "Vendor",
        "total": "Amount",
        "tax": "VAT",
        "currency": "Cur",
    }
    row = row_values(
        {
            "No": " inv-001 ",
            "Date": "2026-03-15 00:00:00",
            "Vendor": "ACME Ltd.",
            "Amount": "1,320.00",
            "VAT": "0",
            "Cur": "Tk",
        },
        mapping,
    )
    assert (row.invoice_number, row.invoice_number_key) == ("inv-001", "INV001")
    assert row.invoice_date == date(2026, 3, 15) and row.supplier_key == "acme"
    assert row.total == Decimal("1320.00") and row.tax == Decimal("0") and row.currency == "BDT"
    assert row.problems == []

    unclear = row_values({"No": "7", "Date": "03/04/2026", "Amount": "1.320"}, mapping)
    assert unclear.invoice_date is None and unclear.total is None
    assert unclear.problems == [
        "date '03/04/2026' is ambiguous",
        "total '1.320' is not a clear amount",
    ]
