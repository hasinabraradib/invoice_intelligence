"""Validation rules: pure functions over an ExtractionResult. No DB, no LLM.

Rules only REPORT problems (issues); they never change a value. A human reviews anything
with an error or a shaky required field (needs_review).
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.extraction.service import ExtractionResult, FieldResult

REQUIRED = ("invoice_number", "invoice_date", "supplier_name", "total")  # missing -> error
RECOMMENDED = ("customer_name", "currency", "subtotal")  # missing -> warning
MAX_TAX_RATE = Decimal("0.5")
MAX_DAYS_IN_FUTURE = 30


class ValidationIssue(BaseModel):
    code: str
    severity: Literal["error", "warning"]
    field: str | None = None
    message: str
    expected: str | None = None
    actual: str | None = None


class ValidationReport(BaseModel):
    issues: list[ValidationIssue] = Field(default_factory=list)
    needs_review: bool = False


def validate_invoice(
    result: ExtractionResult,
    *,
    tolerance: Decimal = Decimal("0.01"),
    review_confidence: float = 0.6,
    today: date | None = None,
) -> ValidationReport:
    issues = [
        *check_required(result),
        *check_totals(result, tolerance),
        *check_line_items(result, tolerance),
        *check_amount_sanity(result),
        *check_dates(result, today or date.today()),
        *check_confidence(result, review_confidence),
    ]
    low_required = any(
        i.code in ("LOW_CONFIDENCE", "AMBIGUOUS_DATE") and i.field in REQUIRED for i in issues
    )
    needs_review = low_required or any(i.severity == "error" for i in issues)
    return ValidationReport(issues=issues, needs_review=needs_review)


def _amount(result: ExtractionResult, name: str) -> Decimal | None:
    field = result.fields.get(name)
    return field.value if field and isinstance(field.value, Decimal) else None


def _date(result: ExtractionResult, name: str) -> date | None:
    field = result.fields.get(name)
    return field.value if field and isinstance(field.value, date) else None


def _differs(a: Decimal, b: Decimal, tolerance: Decimal) -> bool:
    return abs(a - b) > tolerance


def check_required(result: ExtractionResult) -> list[ValidationIssue]:
    issues = []
    for names, severity in ((REQUIRED, "error"), (RECOMMENDED, "warning")):
        for name in names:
            field = result.fields.get(name)
            if field is None or field.status == "missing" or field.value is None:
                issues.append(
                    ValidationIssue(
                        code="MISSING_REQUIRED" if severity == "error" else "MISSING_RECOMMENDED",
                        severity=severity,
                        field=name,
                        message=f"{name} was not found",
                    )
                )
    return issues


def check_totals(result: ExtractionResult, tolerance: Decimal) -> list[ValidationIssue]:
    """subtotal + tax must equal total (checked only when all three were read)."""
    subtotal, tax, total = (_amount(result, n) for n in ("subtotal", "tax", "total"))
    if None in (subtotal, tax, total) or not _differs(subtotal + tax, total, tolerance):
        return []
    return [
        ValidationIssue(
            code="TOTAL_MISMATCH",
            severity="error",
            field="total",
            message="subtotal + tax does not equal total",
            expected=str(subtotal + tax),
            actual=str(total),
        )
    ]


def check_line_items(result: ExtractionResult, tolerance: Decimal) -> list[ValidationIssue]:
    issues = []
    totals = [item.line_total.value for item in result.line_items]
    if result.line_items and all(isinstance(v, Decimal) for v in totals):
        # Line items add up to the subtotal. Without a subtotal they add up to the total
        # before tax: total - tax when a tax is printed, else the total itself.
        subtotal, total, tax = (_amount(result, n) for n in ("subtotal", "total", "tax"))
        if subtotal is not None:
            target_name, target = "subtotal", subtotal
        else:
            target_name = "total"
            target = None if total is None else total - (tax or Decimal(0))
        if target is not None and _differs(sum(totals, Decimal(0)), target, tolerance):
            issues.append(
                ValidationIssue(
                    code="LINE_ITEMS_SUM_MISMATCH",
                    severity="error",
                    field=target_name,
                    message=f"sum of line totals does not equal {target_name}",
                    expected=str(target),
                    actual=str(sum(totals, Decimal(0))),
                )
            )
    elif result.line_items:
        issues.append(
            ValidationIssue(
                code="LINE_TOTALS_INCOMPLETE",
                severity="warning",
                field="line_items",
                message="some line totals could not be read; their sum was not checked",
            )
        )

    for position, item in enumerate(result.line_items, start=1):
        qty, unit, line = item.quantity.value, item.unit_price.value, item.line_total.value
        if all(isinstance(v, Decimal) for v in (qty, unit, line)) and _differs(
            qty * unit, line, tolerance
        ):
            issues.append(
                ValidationIssue(
                    code="LINE_ARITHMETIC",
                    severity="warning",
                    field=f"line_items[{position}].line_total",
                    message="quantity x unit price does not equal line total",
                    expected=str(qty * unit),
                    actual=str(line),
                )
            )
    return issues


def check_amount_sanity(result: ExtractionResult) -> list[ValidationIssue]:
    issues = []
    total = _amount(result, "total")
    if total is not None and total <= 0:
        issues.append(
            ValidationIssue(
                code="NON_POSITIVE_TOTAL",
                severity="warning",
                field="total",
                message="total is zero or negative (fine for a credit note; please confirm)",
                actual=str(total),
            )
        )
    subtotal, tax = _amount(result, "subtotal"), _amount(result, "tax")
    if subtotal and tax is not None and subtotal > 0:
        rate = tax / subtotal
        if not Decimal(0) <= rate <= MAX_TAX_RATE:
            issues.append(
                ValidationIssue(
                    code="TAX_RATE_OUT_OF_RANGE",
                    severity="warning",
                    field="tax",
                    message="tax is outside 0-50% of the subtotal",
                    expected="0% to 50%",
                    actual=f"{rate * 100:.2f}%",
                )
            )
    return issues


def check_dates(result: ExtractionResult, today: date) -> list[ValidationIssue]:
    issues = []
    invoice_date, due_date = _date(result, "invoice_date"), _date(result, "due_date")
    if invoice_date and due_date and due_date < invoice_date:
        issues.append(
            ValidationIssue(
                code="DUE_BEFORE_INVOICE_DATE",
                severity="warning",
                field="due_date",
                message="due date is before the invoice date",
                expected=f">= {invoice_date}",
                actual=str(due_date),
            )
        )
    if invoice_date and invoice_date > today + timedelta(days=MAX_DAYS_IN_FUTURE):
        issues.append(
            ValidationIssue(
                code="INVOICE_DATE_IN_FUTURE",
                severity="warning",
                field="invoice_date",
                message=f"invoice date is more than {MAX_DAYS_IN_FUTURE} days in the future",
                actual=str(invoice_date),
            )
        )
    for name in ("invoice_date", "due_date"):
        field = result.fields.get(name)
        if field and any(r.startswith("ambiguous date") for r in field.reasons):
            issues.append(
                ValidationIssue(
                    code="AMBIGUOUS_DATE",
                    severity="warning",
                    field=name,
                    message="day and month could be swapped; please confirm",
                    actual=field.raw_value,
                )
            )
    return issues


def check_confidence(result: ExtractionResult, threshold: float) -> list[ValidationIssue]:
    """Flag every read value that is uncertain or below the review threshold."""
    named: list[tuple[str, FieldResult]] = list(result.fields.items())
    for position, item in enumerate(result.line_items, start=1):
        named += [(f"line_items[{position}].{n}", f) for n, f in item]
    return [
        ValidationIssue(
            code="LOW_CONFIDENCE",
            severity="warning",
            field=name,
            message="; ".join(field.reasons) or "low confidence",
            expected=f">= {threshold}",
            actual=f"{field.confidence} ({field.status})",
        )
        for name, field in named
        if field.status != "missing"
        and (field.status == "uncertain" or field.confidence < threshold)
    ]
