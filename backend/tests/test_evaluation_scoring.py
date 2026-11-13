"""Evaluation scoring (app/evaluation/scoring.py): pure functions, no services needed."""

from decimal import Decimal

from app.evaluation import scoring as s

LABEL = {
    "id": "inv-01",
    "file": "inv-01.pdf",
    "file_type": "pdf_digital",
    "split": "test",
    "segment": None,
    "fields": {
        "invoice_number": "No. 247",
        "invoice_date": "2026-05-04",
        "due_date": None,
        "supplier_name": "Meghna Traders Ltd",
        "customer_name": None,
        "currency": "BDT",
        "subtotal": "1000.00",
        "tax": "150.00",
        "total": "1150.00",
    },
    "line_items": [
        {
            "description": "Steel bolts M8",
            "quantity": "200",
            "unit_price": "5.00",
            "line_total": "1000.00",
        }
    ],
    "ambiguous_fields": ["invoice_date"],
    "expected_flags": [
        {"code": "AMBIGUOUS_DATE", "field": "invoice_date"},
        {"code": "MISSING_REQUIRED", "field": "invoice_date"},
        {"code": "MISSING_RECOMMENDED", "field": "customer_name"},
    ],
}


def _f(value, evidence="quoted text"):
    return {"value": value, "evidence": evidence}


def _invoice(**overrides):
    fields = {
        "invoice_number": _f("247"),
        "invoice_date": _f(None),
        "due_date": _f(None),
        "supplier_name": _f("MEGHNA  traders ltd."),
        "customer_name": _f("Invented Customer"),
        "currency": _f("bdt"),
        "subtotal": _f("1000.0"),
        "tax": _f("150.00"),
        "total": _f("1105.00"),
    }
    fields.update(overrides)
    return {
        "fields": fields,
        "line_items": [
            {"description": _f("Steel bolts M-8"), "line_total": _f("1000.00")},
            {"description": _f("Phantom line"), "line_total": _f("5.00")},
        ],
        "issues": [
            {"code": "AMBIGUOUS_DATE", "field": "invoice_date"},
            {"code": "MISSING_REQUIRED", "field": "invoice_date"},
            {"code": "LOW_CONFIDENCE", "field": "invoice_date"},  # not scored
            {"code": "TOTAL_MISMATCH", "field": "total"},  # not expected: a false positive
        ],
    }


def test_field_outcomes_cover_every_case():
    outcomes = {f.field: f.outcome for f in s.score_fields(LABEL, _invoice())}
    assert outcomes == {
        "invoice_number": "correct",  # "No. 247" == "247"
        "invoice_date": "abstained",  # null + AMBIGUOUS_DATE flag: right behaviour
        "due_date": "correct_null",
        "supplier_name": "correct",  # case, spaces and trailing dot ignored
        "customer_name": "false_fill",  # not printed, but a value was given
        "currency": "correct",
        "subtotal": "correct",  # Decimal: 1000.0 == 1000.00
        "tax": "correct",
        "total": "wrong",
    }


def test_null_without_the_ambiguity_flag_is_missing():
    invoice = _invoice()
    invoice["issues"] = []
    outcomes = {f.field: f.outcome for f in s.score_fields(LABEL, invoice)}
    assert outcomes["invoice_date"] == "missing"


def test_no_invoice_found_counts_every_value_as_missing():
    score = s.score_invoice(LABEL, None)
    assert not score.found
    assert {f.outcome for f in score.fields} == {"missing", "correct_null"}
    assert (score.line_tp, score.line_fn) == (0, 1)


def test_line_items_need_similar_description_and_exact_total():
    assert s.match_lines(LABEL["line_items"], _invoice()["line_items"]) == (1, 1, 0)
    wrong_total = [{"description": _f("Steel bolts M8"), "line_total": _f("999.99")}]
    assert s.match_lines(LABEL["line_items"], wrong_total) == (0, 1, 1)


def test_flag_metrics_ignore_unscored_codes():
    score = s.score_invoice(LABEL, _invoice())
    metrics = s.flag_metrics([score])
    assert (metrics["tp"], metrics["fp"], metrics["fn"]) == (2, 1, 1)
    assert metrics["per_code"]["TOTAL_MISMATCH"]["fp"] == 1
    assert metrics["per_code"]["MISSING_RECOMMENDED"]["fn"] == 1
    assert "LOW_CONFIDENCE" not in metrics["per_code"]


def test_field_metrics_rates():
    metrics = s.field_metrics([s.score_invoice(LABEL, _invoice())])
    overall = metrics["overall"]
    assert overall["n"] == 9
    assert overall["accuracy"] == round(7 / 9, 4)
    assert overall["false_fill_rate"] == 0.5  # 1 of 2 null truths was filled
    assert overall["missing_rate"] == 0.0  # the abstained date is not missing
    assert metrics["per_field"]["total"]["wrong_rate"] == 1.0


def test_pick_invoice_by_segment():
    invoices = [{"page_start": 1, "page_end": 1}, {"page_start": 2, "page_end": 3}]
    assert s.pick_invoice({**LABEL, "segment": [2, 3]}, invoices) is invoices[1]
    assert s.pick_invoice({**LABEL, "segment": [2, 4]}, invoices) is invoices[1]  # same start
    assert s.pick_invoice({**LABEL, "segment": [5, 5]}, invoices) is None
    assert s.pick_invoice(LABEL, invoices) is invoices[0]


def test_segment_metrics():
    m = s.segment_metrics([[1, 1], [2, 3], [4, 4]], [[1, 1], [2, 2], [3, 4]])
    assert (m["exact"], m["precision"], m["recall"], m["count_right"]) == (1, 0.3333, 0.3333, True)


def test_crosscheck_recall_matches_kind_field_and_number():
    expected = [
        {"kind": "amount_mismatch", "field": "total", "invoice_number": "PE/0247"},
        {"kind": "missing_in_pdf", "field": None, "invoice_number": "INV-2026-9999"},
        {"kind": "date_mismatch", "field": "invoice_date", "invoice_number": "KF-254"},
    ]
    findings = [
        {"kind": "matched", "field": None, "message": "ok"},
        {
            "kind": "amount_mismatch",
            "field": "total",
            "message": "Total differs for invoice PE/0247.",
        },
        {
            "kind": "missing_in_pdf",
            "field": None,
            "message": "In ledger only.",
            "value_b": "INV-2026-9999",
        },
        {
            "kind": "currency_mismatch",
            "field": "currency",
            "message": "Currency differs for invoice X-1.",
        },
    ]
    m = s.crosscheck_metrics(expected, findings)
    assert (m["found"], m["recall"], m["extra_findings"]) == (2, 0.6667, 1)


def test_duplicates_and_percentiles():
    dup = s.InvoiceScore(
        "c", "c.jpg", "image_scan", "test", True, duplicate_expected=True, duplicate_flagged=True
    )
    other = s.InvoiceScore("o", "o.pdf", "pdf_digital", "test", True, duplicate_flagged=True)
    assert s.duplicate_metrics([dup, other]) == {"pairs": 1, "detected": 1, "false_alarms": 1}
    assert s.percentile([1.0, 2.0, 3.0, 4.0, 5.0], 50) == 3.0
    assert s.percentile([], 95) is None
    assert s.normalise("total", "1000.0") == Decimal("1000.00")  # API values are canonical
