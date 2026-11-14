"""Vision as a second opinion on OCR'd pages (pure functions)."""

from decimal import Decimal

from app.config import Settings
from app.extraction.prompt import SourcePage
from app.extraction.second_opinion import (
    OCR_VISION_DISAGREE,
    compare_readings,
    confusable_next_to_digit,
    vision_triggers,
)
from app.extraction.service import ExtractionResult, FieldResult, LineItemResult

SETTINGS = Settings(ocr_min_confidence=60, review_confidence=0.6)


def F(value, raw=None, status="found", confidence=0.95):  # noqa: N802
    return FieldResult(
        value=value,
        raw_value=raw or str(value),
        status=status,
        confidence=confidence,
        evidence=raw or str(value),
        page=1,
    )


def result(**overrides) -> ExtractionResult:
    fields = {
        "invoice_number": F("HD-2026-311"),
        "invoice_date": F(__import__("datetime").date(2026, 9, 29), "2026-09-29"),
        "due_date": F(None, status="missing"),
        "supplier_name": F("Jamuna Paper Mills"),
        "customer_name": F("Sample Retail Ltd"),
        "currency": F("BDT"),
        "subtotal": F(Decimal("100.00"), "100.00"),
        "tax": F(Decimal("15.00"), "15.00"),
        "total": F(Decimal("115.00"), "BDT 115.00"),
    }
    fields.update(overrides)
    line = LineItemResult(
        description=F("Toner cartridge"),
        quantity=F(Decimal("2"), "2"),
        unit_price=F(Decimal("50.00"), "50.00"),
        line_total=F(Decimal("100.00"), "100.00"),
    )
    return ExtractionResult(ok=True, provider="fake", fields=fields, line_items=[line])


OCR_PAGE = [SourcePage(1, "text", ocr_confidence=88.0)]
TEXT_LAYER = [SourcePage(1, "text", ocr_confidence=None)]


def test_confusable_letters_next_to_digits():
    assert confusable_next_to_digit("HD-2026-3tt")
    assert confusable_next_to_digit("1O5.00") and confusable_next_to_digit("S0")
    assert not confusable_next_to_digit("HD-2026-311")
    assert not confusable_next_to_digit("2026/BD/0193")  # letters, but not touching digits
    assert not confusable_next_to_digit("No. 247")


def test_no_second_reading_for_text_layer_pages_or_clean_ocr():
    assert vision_triggers(TEXT_LAYER, result(invoice_number=F("HD-2026-3tt")), SETTINGS) == []
    assert vision_triggers(OCR_PAGE, result(), SETTINGS) == []  # nothing looks off


def test_each_trigger():
    assert (
        "low OCR confidence"
        in vision_triggers([SourcePage(1, "t", ocr_confidence=41.0)], result(), SETTINGS)[0]
    )
    reasons = vision_triggers(OCR_PAGE, result(invoice_number=F("HD-2026-3tt")), SETTINGS)
    assert reasons == ["letters that look like digits in invoice_number"]
    reasons = vision_triggers(OCR_PAGE, result(total=F(Decimal("1150.00"), "1150.00")), SETTINGS)
    assert any("TOTAL_MISMATCH" in r for r in reasons)
    reasons = vision_triggers(
        OCR_PAGE, result(supplier_name=F("Jamuna Paper Mi1ls", status="uncertain")), SETTINGS
    )
    assert reasons == ["uncertain required field(s): supplier_name"]
    # A currency code next to the amount is not a misread.
    assert (
        vision_triggers(OCR_PAGE, result(total=F(Decimal("115.00"), "115.00BDT")), SETTINGS) == []
    )


def test_agreement_keeps_the_ocr_value_and_raises_confidence():
    ocr, vision = result(), result()
    assert compare_readings(ocr, vision) == []
    total = ocr.fields["total"]
    assert total.value == Decimal("115.00") and total.confidence == 1.0
    assert "confirmed by a second reading of the page image" in total.reasons


def test_disagreement_keeps_both_readings_and_never_replaces_the_value():
    ocr = result(invoice_number=F("HD-2026-3tt"))
    vision = result(invoice_number=F("HD-2026-311"))

    issues = compare_readings(ocr, vision)

    field = ocr.fields["invoice_number"]
    assert field.value == "HD-2026-3tt"  # the OCR value is kept, not silently replaced
    assert field.status == "uncertain"
    assert any("'HD-2026-3tt'" in r and "'HD-2026-311'" in r for r in field.reasons)
    assert [(i.code, i.field, i.actual, i.expected) for i in issues] == [
        (OCR_VISION_DISAGREE, "invoice_number", "HD-2026-3tt", "HD-2026-311")
    ]


def test_a_different_number_of_line_items_is_a_disagreement():
    ocr, vision = result(), result()
    vision.line_items = vision.line_items * 2
    issues = compare_readings(ocr, vision)
    assert [(i.code, i.field) for i in issues] == [(OCR_VISION_DISAGREE, "line_items")]
