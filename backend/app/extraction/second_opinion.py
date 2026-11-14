"""Vision as a SECOND OPINION on OCR'd pages: catch confident OCR misreads.

OCR can be sure and wrong ("HD-2026-3tt" for HD-2026-311). For image-based pages (scans
and photos, i.e. pages that went through OCR), the page images are read a second time by
a vision model when something looks off (`vision_triggers`). The two readings are then
compared field by field (`compare_readings`):

- they agree: the OCR value is kept (it is grounded in OCR text) and its confidence rises
  slightly;
- they disagree: the OCR value is still kept, never silently replaced. The field becomes
  "uncertain", both readings go into its reasons, an OCR_VISION_DISAGREE issue is raised
  and the invoice goes to review.

Pure functions: no I/O, no LLM calls.
"""

import re
from datetime import date
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Any

from app.config import Settings
from app.extraction import postprocess as pp
from app.extraction.prompt import SourcePage
from app.extraction.service import ExtractionResult, FieldResult
from app.validation.rules import REQUIRED, ValidationIssue, validate_invoice

OCR_VISION_DISAGREE = "OCR_VISION_DISAGREE"
AGREE_BONUS = 0.05
# Letters OCR mixes up with digits: O/0, l/1, I/1, t/1, S/5, B/8, Z/2 (and lower-case o, i).
CONFUSABLE = set("OolIitSBZ")
# Validation results that mean "the reading may be wrong". Not MISSING_RECOMMENDED (an
# optional field that is simply not printed) or AMBIGUOUS_DATE (a correct reading of an
# ambiguous print, which a second reading cannot settle).
FAILING_CHECKS = {
    "MISSING_REQUIRED",
    "TOTAL_MISMATCH",
    "LINE_ITEMS_SUM_MISMATCH",
    "LINE_ARITHMETIC",
    "LINE_TOTALS_INCOMPLETE",
    "NON_POSITIVE_TOTAL",
    "TAX_RATE_OUT_OF_RANGE",
    "DUE_BEFORE_INVOICE_DATE",
    "INVOICE_DATE_IN_FUTURE",
}
ID_FIELDS = ("invoice_number",)
NUMERIC_FIELDS = ("subtotal", "tax", "total", "invoice_date", "due_date")
LINE_NUMERIC = ("quantity", "unit_price", "line_total")
# Currency codes glued to an amount ("115.00BDT") are not misreads: strip them first.
_CURRENCY_WORDS = re.compile(r"(?i)(?<![a-z])(bdt|usd|eur|tk\.?|taka)(?![a-z])|[$€৳]")


def confusable_next_to_digit(text: str | None) -> bool:
    """True if a digit-like letter sits right next to a digit ("3tt", "1O5", "S0")."""
    s = text or ""
    return any(
        ch in CONFUSABLE
        and ((i > 0 and s[i - 1].isdigit()) or (i + 1 < len(s) and s[i + 1].isdigit()))
        for i, ch in enumerate(s)
    )


def _numeric_text(field: FieldResult | None) -> str:
    raw = (field.raw_value or "") if field else ""
    return _CURRENCY_WORDS.sub(" ", raw)


def vision_triggers(
    pages: list[SourcePage], result: ExtractionResult, settings: Settings
) -> list[str]:
    """Why this invoice should get a second reading from the page images ([] = no).
    Only OCR'd pages qualify: a text-layer PDF's text is exact, not a reading."""
    if not any(p.ocr_confidence is not None for p in pages):
        return []
    reasons = []
    low = [
        p.number
        for p in pages
        if p.ocr_confidence is not None and p.ocr_confidence < settings.ocr_min_confidence
    ]
    if low:
        reasons.append(f"low OCR confidence on page(s) {sorted(set(low))}")
    if not result.ok:
        return reasons + ["OCR-text extraction failed"]
    suspicious = [n for n in ID_FIELDS if confusable_next_to_digit(_raw(result.fields.get(n)))]
    suspicious += [
        n for n in NUMERIC_FIELDS if confusable_next_to_digit(_numeric_text(result.fields.get(n)))
    ]
    suspicious += [
        f"line {i}.{n}"
        for i, item in enumerate(result.line_items, start=1)
        for n in LINE_NUMERIC
        if confusable_next_to_digit(_numeric_text(getattr(item, n)))
    ]
    if suspicious:
        reasons.append(f"letters that look like digits in {', '.join(suspicious)}")
    report = validate_invoice(
        result, tolerance=settings.money_tolerance, review_confidence=settings.review_confidence
    )
    failing = sorted({i.code for i in report.issues if i.code in FAILING_CHECKS})
    if failing:
        reasons.append(f"failed checks: {', '.join(failing)}")
    shaky = [
        name
        for name in REQUIRED
        if (f := result.fields.get(name))
        and f.status != "missing"
        and (f.status == "uncertain" or f.confidence < settings.review_confidence)
    ]
    if shaky:
        reasons.append(f"uncertain required field(s): {', '.join(shaky)}")
    return reasons


def _raw(field: FieldResult | None) -> str:
    return (field.raw_value or "") if field else ""


def _key(name: str, field: FieldResult | None) -> Any:
    """The comparable meaning of a value (None when there is none)."""
    if field is None or field.status == "missing" or field.value is None:
        return None
    value = field.value
    if name == "invoice_number":
        return pp.normalise_invoice_number(str(value))
    if isinstance(value, Decimal | date):
        return value
    return " ".join(str(value).casefold().split())


def compare_readings(ocr: ExtractionResult, vision: ExtractionResult) -> list[ValidationIssue]:
    """Merge the vision reading into the OCR result as a second opinion (see module doc).
    Mutates `ocr`; returns one OCR_VISION_DISAGREE issue per disagreement."""
    issues = []
    for name, field in ocr.fields.items():
        other = vision.fields.get(name)
        mine, theirs = _key(name, field), _key(name, other)
        if mine is None and theirs is None:
            continue
        if mine == theirs:
            field.confidence = min(1.0, round(field.confidence + AGREE_BONUS, 2))
            field.reasons.append("confirmed by a second reading of the page image")
            continue
        issues.append(_disagree(name, field, other))
    issues += _compare_lines(ocr, vision)
    return issues


def _disagree(name: str, field: FieldResult, other: FieldResult | None) -> ValidationIssue:
    ocr_text = field.raw_value if field.status != "missing" else "(nothing)"
    vision_text = other.raw_value if other and other.status != "missing" else "(nothing)"
    field.status = "uncertain" if field.status != "missing" else field.status
    field.reasons.append(f"OCR read {ocr_text!r}; the page image reads {vision_text!r}")
    return ValidationIssue(
        code=OCR_VISION_DISAGREE,
        severity="warning",
        field=name,
        message=f"OCR and a second reading of the page image disagree on {name}; "
        "the OCR value is kept, please check it.",
        expected=vision_text,
        actual=ocr_text,
    )


def _compare_lines(ocr: ExtractionResult, vision: ExtractionResult) -> list[ValidationIssue]:
    issues = []
    if len(ocr.line_items) != len(vision.line_items):
        issues.append(
            ValidationIssue(
                code=OCR_VISION_DISAGREE,
                severity="warning",
                field="line_items",
                message="OCR and the page image show a different number of line items.",
                expected=str(len(vision.line_items)),
                actual=str(len(ocr.line_items)),
            )
        )
        return issues
    for position, (mine, theirs) in enumerate(
        zip(ocr.line_items, vision.line_items, strict=True), start=1
    ):
        for name in LINE_NUMERIC:
            a, b = getattr(mine, name), getattr(theirs, name)
            if _key(name, a) != _key(name, b) and not (
                _key(name, a) is None and _key(name, b) is None
            ):
                issues.append(_disagree(f"line_items[{position}].{name}", a, b))
        da, db = str(mine.description.value or ""), str(theirs.description.value or "")
        if SequenceMatcher(None, da.casefold(), db.casefold()).ratio() < 0.8:
            issues.append(
                _disagree(
                    f"line_items[{position}].description", mine.description, theirs.description
                )
            )
    return issues
