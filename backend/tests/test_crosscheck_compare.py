"""Cross-check comparison logic (pure)."""

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.crosscheck.compare import Entry, Line, compare, match
from app.extraction.postprocess import normalise_invoice_number

TOL = Decimal("0.01")
DAY = date(2026, 3, 15)


def inv(number, total, supplier="acme", **kw):
    return Entry(
        ref=uuid.uuid4(),
        number=number,
        number_key=normalise_invoice_number(number),
        supplier=supplier.title(),
        supplier_key=supplier,
        invoice_date=DAY,
        total=Decimal(total),
        currency="BDT",
        page=1,
        **kw,
    )


def row(number, total, supplier="acme", line=4, **kw):
    return Entry(
        ref=uuid.uuid4(),
        number=number,
        number_key=normalise_invoice_number(number),
        supplier=supplier.title(),
        supplier_key=supplier,
        invoice_date=DAY,
        total=Decimal(total),
        currency="BDT",
        sheet="Purchases",
        row=line,
        **kw,
    )


def kinds(findings):
    return sorted(f.kind for f in findings)


def ledger(a, b):
    return compare(a, b, mode="invoices_vs_ledger", tolerance=TOL)


def test_exact_match():
    [finding] = ledger([inv("A-1", "10.00")], [row("a 1", "10.00")])
    assert finding.kind == "matched" and finding.severity == "info"
    assert (finding.page_a, finding.b.row) == (1, 4)


@pytest.mark.parametrize(
    ("ledger_total", "kind"),
    [
        ("10.004", "matched"),
        ("9.996", "matched"),
        ("10.01", "matched"),
        ("10.02", "amount_mismatch"),
        ("9.98", "amount_mismatch"),
    ],
)
def test_money_tolerance(ledger_total, kind):
    [finding] = ledger([inv("A-1", "10.00")], [row("A-1", ledger_total)])
    assert finding.kind == kind
    if kind == "amount_mismatch":
        assert (finding.field, finding.value_a, finding.value_b) == ("total", "10.00", ledger_total)
        assert finding.is_mismatch


def test_date_and_currency_mismatch():
    a = inv("A-1", "10.00")
    b = Entry(
        ref=uuid.uuid4(),
        number="A-1",
        number_key="A1",
        supplier_key="acme",
        invoice_date=date(2026, 3, 16),
        total=Decimal("10.00"),
        currency="USD",
        row=9,
    )
    assert kinds(ledger([a], [b])) == ["currency_mismatch", "date_mismatch"]


def test_missing_on_each_side():
    findings = ledger(
        [inv("A-1", "10.00"), inv("C-3", "5.00")], [row("A-1", "10.00"), row("D-9", "7.00", line=6)]
    )
    assert kinds(findings) == ["matched", "missing_in_ledger", "missing_in_pdf"]
    missing_pdf = next(f for f in findings if f.kind == "missing_in_pdf")
    assert missing_pdf.value_b == "D-9" and missing_pdf.b.row == 6 and missing_pdf.is_mismatch


def test_ledger_duplicates_are_reported_once_and_not_as_missing():
    findings = ledger(
        [inv("A-1", "10.00")], [row("A-1", "10.00", line=4), row("A1", "10.00", line=7)]
    )
    assert kinds(findings) == ["duplicate_in_ledger", "matched"]
    duplicate = findings[0]
    assert duplicate.b.row == 7 and "first on row 4" in duplicate.message


def test_fallback_match_by_supplier_date_total():
    a = inv("A-1", "10.00")
    b = row(None, "10.00")  # the ledger row has no invoice number
    pairs, only_a, only_b = match([a], [b], TOL)
    assert pairs == [(a, b)] and not only_a and not only_b


def test_same_number_different_supplier_does_not_match():
    findings = ledger(
        [inv("100", "10.00", supplier="acme")], [row("100", "10.00", supplier="globex")]
    )
    assert kinds(findings) == ["missing_in_ledger", "missing_in_pdf"]


def test_pdf_vs_pdf_compares_headers_and_line_items():
    lines_a = (
        Line(1, "Office chairs, black", Decimal(4), Decimal("300.00"), Decimal("1200.00"), 2),
        Line(2, "Delivery", Decimal(1), Decimal("120.00"), Decimal("120.00"), 2),
    )
    lines_b = (
        Line(1, "Delivery charge", Decimal(1), Decimal("120.00"), Decimal("120.00"), 1),
        Line(2, "Office chair black", Decimal(4), Decimal("310.00"), Decimal("1240.00"), 1),
        Line(3, "Assembly", Decimal(1), Decimal("50.00"), Decimal("50.00"), 1),
    )
    a = inv("PO-7", "1320.00", lines=lines_a, customer="Buyer Ltd")
    b = inv("po 7", "1410.00", lines=lines_b, customer="Buyer Limited")

    findings = compare([a], [b], mode="invoices_vs_invoices", tolerance=TOL)

    assert kinds(findings) == [
        "amount_mismatch",
        "line_item_mismatch",
        "line_item_mismatch",
        "line_missing_in_a",
    ]  # customer "Ltd"/"Limited" is the same
    unit = next(f for f in findings if f.field == "line_items[1].unit_price")
    assert (unit.value_a, unit.value_b, unit.page_a, unit.page_b) == ("300.00", "310.00", 2, 1)
    assert next(f for f in findings if f.kind == "line_missing_in_a").value_b == "Assembly"
