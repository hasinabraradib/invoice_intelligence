"""Validation rules and duplicate detection (pure; no database)."""

import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.extraction.service import ExtractionResult, FieldResult, LineItemResult
from app.validation.duplicates import find_duplicate_issues, find_exact_duplicate
from app.validation.rules import validate_invoice

TODAY = date(2026, 11, 13)


def F(value, status="found", confidence=0.95, reasons=None, raw=None):  # noqa: N802
    return FieldResult(
        value=value,
        raw_value=raw or (str(value) if value is not None else None),
        status=status if value is not None or status != "found" else "missing",
        confidence=confidence if value is not None else 0.0,
        reasons=reasons or [],
    )


def item(qty, unit, total):
    return LineItemResult(
        description=F("Item"),
        quantity=F(Decimal(qty)),
        unit_price=F(Decimal(unit)),
        line_total=F(Decimal(total)),
    )


def result(items=None, **overrides) -> ExtractionResult:
    fields = {
        "invoice_number": F("INV-1"),
        "invoice_date": F(date(2026, 11, 1)),
        "due_date": F(date(2026, 12, 1)),
        "supplier_name": F("ACME Ltd"),
        "customer_name": F("Buyer"),
        "currency": F("BDT"),
        "subtotal": F(Decimal("100.00")),
        "tax": F(Decimal("15.00")),
        "total": F(Decimal("115.00")),
    }
    fields.update(overrides)
    return ExtractionResult(
        ok=True,
        provider="fake",
        fields=fields,
        line_items=items if items is not None else [item("2", "50.00", "100.00")],
    )


def codes(report):
    return [i.code for i in report.issues]


def validate(r, **kw):
    return validate_invoice(r, today=TODAY, **kw)


def test_clean_invoice_has_no_issues():
    report = validate(result())
    assert report.issues == [] and not report.needs_review


@pytest.mark.parametrize("name", ["invoice_number", "invoice_date", "supplier_name", "total"])
def test_missing_required_is_error_and_needs_review(name):
    report = validate(result(**{name: F(None, status="missing")}))
    issue = next(i for i in report.issues if i.field == name and i.code == "MISSING_REQUIRED")
    assert issue.severity == "error" and report.needs_review


@pytest.mark.parametrize("name", ["customer_name", "currency"])
def test_missing_recommended_is_warning_only(name):
    report = validate(result(**{name: F(None, status="missing")}))
    assert codes(report) == ["MISSING_RECOMMENDED"] and not report.needs_review


@pytest.mark.parametrize(
    ("total", "ok"),
    [("115.00", True), ("115.004", True), ("114.996", True), ("115.02", False), ("114.98", False)],
)
def test_subtotal_plus_tax_tolerance(total, ok):
    items = [item("1", total, total)]  # keep the line-item check out of the way
    report = validate(result(items=items, subtotal=F(Decimal("100.00")), total=F(Decimal(total))))
    report_codes = [c for c in codes(report) if c != "LINE_ITEMS_SUM_MISMATCH"]
    assert ("TOTAL_MISMATCH" not in report_codes) is ok
    if not ok:
        issue = next(i for i in report.issues if i.code == "TOTAL_MISMATCH")
        assert issue.expected == "115.00" and issue.actual == total and issue.severity == "error"


def test_custom_tolerance():
    r = result(total=F(Decimal("115.05")), items=[])
    assert "TOTAL_MISMATCH" in codes(validate(r))
    assert "TOTAL_MISMATCH" not in codes(validate(r, tolerance=Decimal("0.10")))


def test_line_items_sum_against_subtotal():
    report = validate(result(items=[item("1", "60.00", "60.00"), item("1", "40.02", "40.02")]))
    assert codes(report) == ["LINE_ITEMS_SUM_MISMATCH"] and report.needs_review
    assert report.issues[0].field == "subtotal" and report.issues[0].actual == "100.02"
    within = validate(result(items=[item("1", "60.00", "60.00"), item("1", "40.004", "40.004")]))
    assert within.issues == []


def test_line_items_sum_against_total_when_no_subtotal():
    r = result(
        subtotal=F(None, status="missing"),
        tax=F(None, status="missing"),
        items=[item("1", "100.00", "100.00")],
    )
    issue = next(i for i in validate(r).issues if i.code == "LINE_ITEMS_SUM_MISMATCH")
    assert issue.field == "total" and issue.expected == "115.00"
    # No subtotal and no tax: lines equal to the total are fine.
    ok = result(
        subtotal=F(None, status="missing"),
        tax=F(None, status="missing"),
        items=[item("1", "115.00", "115.00")],
    )
    assert "LINE_ITEMS_SUM_MISMATCH" not in codes(validate(ok))


def test_no_subtotal_with_tax_compares_lines_with_total_minus_tax():
    # Lines 100 + tax 15 = total 115: a correct invoice, not flagged (the old rule compared
    # the lines with the total and raised a false LINE_ITEMS_SUM_MISMATCH).
    correct = result(subtotal=F(None, status="missing"), items=[item("1", "100.00", "100.00")])
    assert "LINE_ITEMS_SUM_MISMATCH" not in codes(validate(correct))
    within = result(subtotal=F(None, status="missing"), items=[item("1", "100.004", "100.004")])
    assert "LINE_ITEMS_SUM_MISMATCH" not in codes(validate(within))  # MONEY_TOLERANCE
    wrong = result(subtotal=F(None, status="missing"), items=[item("1", "90.00", "90.00")])
    issue = next(i for i in validate(wrong).issues if i.code == "LINE_ITEMS_SUM_MISMATCH")
    assert issue.field == "total" and issue.expected == "100.00" and issue.actual == "90.00"


def test_unreadable_line_total_skips_sum_with_warning():
    broken = item("2", "50.00", "100.00")
    broken.line_total = F(None, status="missing")
    assert "LINE_TOTALS_INCOMPLETE" in codes(validate(result(items=[broken])))


def test_line_arithmetic_warning():
    report = validate(result(items=[item("3", "33.32", "100.00")]))  # 99.96 vs 100.00
    assert codes(report) == ["LINE_ARITHMETIC"]
    assert report.issues[0].severity == "warning" and report.issues[0].expected == "99.96"
    assert validate(result(items=[item("3", "33.33", "100.00")])).issues == []  # 0.01: within


def test_non_positive_total_is_warning():
    r = result(
        subtotal=F(Decimal("-100.00")),
        tax=F(Decimal("0.00")),
        total=F(Decimal("-100.00")),
        items=[item("1", "-100.00", "-100.00")],
    )
    assert codes(validate(r)) == ["NON_POSITIVE_TOTAL"]


@pytest.mark.parametrize(
    ("tax", "flagged"), [("0.00", False), ("50.00", False), ("50.01", True), ("-1.00", True)]
)
def test_tax_rate_range(tax, flagged):
    subtotal = Decimal("100.00")
    total = subtotal + Decimal(tax)
    r = result(tax=F(Decimal(tax)), total=F(total), items=[item("1", "100.00", "100.00")])
    assert ("TAX_RATE_OUT_OF_RANGE" in codes(validate(r))) is flagged


def test_due_before_invoice_date():
    assert codes(validate(result(due_date=F(date(2026, 10, 1))))) == ["DUE_BEFORE_INVOICE_DATE"]


@pytest.mark.parametrize(("days", "flagged"), [(30, False), (31, True)])
def test_invoice_date_in_future(days, flagged):
    from datetime import timedelta

    invoice_date = TODAY + timedelta(days=days)
    r = result(invoice_date=F(invoice_date), due_date=F(invoice_date + timedelta(days=30)))
    assert ("INVOICE_DATE_IN_FUTURE" in codes(validate(r))) is flagged


def test_ambiguous_date_is_flagged_and_needs_review():
    ambiguous = F(
        None,
        status="uncertain",
        confidence=0.25,
        raw="03/04/2026",
        reasons=["ambiguous date: day and month could be swapped"],
    )
    ambiguous.raw_value = "03/04/2026"
    report = validate(result(invoice_date=ambiguous))
    issue = next(i for i in report.issues if i.code == "AMBIGUOUS_DATE")
    assert issue.actual == "03/04/2026" and report.needs_review


def test_low_confidence_or_uncertain_fields():
    report = validate(result(customer_name=F("Buyer", confidence=0.5)))
    assert codes(report) == ["LOW_CONFIDENCE"] and not report.needs_review  # not required
    report = validate(result(total=F(Decimal("115.00"), status="uncertain", confidence=0.7)))
    assert "LOW_CONFIDENCE" in codes(report) and report.needs_review  # required field
    assert validate(result(customer_name=F("B", confidence=0.6))).issues == []  # boundary


def test_validation_never_changes_values():
    r = result(total=F(Decimal("999.00")))
    before = r.model_dump()
    validate(r)
    assert r.model_dump() == before


# ---------------------------------------------------------------- duplicates


class FakeRepo:
    def __init__(self, sha=None, by_number=(), by_date_total=()):
        self.sha, self.by_number, self.by_date_total = sha, list(by_number), list(by_date_total)

    async def find_document_id_by_sha256(self, sha256):
        return self.sha

    async def find_invoices_by_supplier_and_number(self, key, number, exclude_document_id):
        return self.by_number

    async def find_invoices_by_supplier_date_total(self, key, day, total, exclude_document_id):
        return self.by_date_total


ME, OTHER, THIRD = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
ARGS = {
    "invoice_id": ME,
    "supplier_key": "acme",
    "invoice_number": "INV-1",
    "invoice_date": date(2026, 11, 1),
    "total": Decimal("115.00"),
}


@pytest.mark.anyio
async def test_exact_duplicate_by_sha256():
    assert await find_exact_duplicate(FakeRepo(sha=OTHER), "ab" * 32) == OTHER
    assert await find_exact_duplicate(FakeRepo(), "ab" * 32) is None


@pytest.mark.anyio
async def test_same_supplier_and_number_is_duplicate_error():
    issues = await find_duplicate_issues(FakeRepo(by_number=[OTHER], by_date_total=[OTHER]), **ARGS)
    assert [(i.code, i.severity) for i in issues] == [("DUPLICATE_INVOICE", "error")]
    assert issues[0].actual == str(OTHER)  # points at the other document


@pytest.mark.anyio
async def test_same_supplier_date_total_is_possible_duplicate():
    issues = await find_duplicate_issues(FakeRepo(by_date_total=[THIRD]), **ARGS)
    assert [(i.code, i.severity) for i in issues] == [("POSSIBLE_DUPLICATE", "warning")]
    assert issues[0].actual == str(THIRD)


@pytest.mark.anyio
async def test_no_supplier_key_means_no_duplicate_check():
    assert (
        await find_duplicate_issues(FakeRepo(by_number=[OTHER]), **{**ARGS, "supplier_key": None})
        == []
    )
