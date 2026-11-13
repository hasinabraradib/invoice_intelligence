"""Scoring for the evaluation (eval/run_eval.py). Pure functions: labels and API results in,
numbers out. No database, no LLM, no network.

A field's outcome is one of:
  correct       truth has a value and the system gave the same (normalised) value
  correct_null  truth is null (not printed) and the system gave nothing
  abstained     truth has a value, the system gave nothing but FLAGGED it as ambiguous
                (e.g. 04/05/2026): the right behaviour, counted as correct, not missing
  wrong         both have values and they differ
  missing       truth has a value, the system gave nothing
  false_fill    truth is null, the system gave a value anyway (an invented value)
"""

import re
import statistics
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from typing import Any

from app.extraction.postprocess import normalise_invoice_number

FIELDS = (
    "invoice_number",
    "invoice_date",
    "due_date",
    "supplier_name",
    "customer_name",
    "currency",
    "subtotal",
    "tax",
    "total",
)
MONEY = {"subtotal", "tax", "total"}
DATES = {"invoice_date", "due_date"}
# Flags scored for precision/recall. LOW_CONFIDENCE and LINE_TOTALS_INCOMPLETE depend on the
# model's confidence, not on the document; duplicates and cross-checks are scored separately.
SCORED_FLAGS = {
    "MISSING_REQUIRED",
    "MISSING_RECOMMENDED",
    "TOTAL_MISMATCH",
    "LINE_ITEMS_SUM_MISMATCH",
    "LINE_ARITHMETIC",
    "AMBIGUOUS_DATE",
    "DUE_BEFORE_INVOICE_DATE",
    "INVOICE_DATE_IN_FUTURE",
    "NON_POSITIVE_TOTAL",
    "TAX_RATE_OUT_OF_RANGE",
}
DUPLICATE_FLAGS = {"DUPLICATE_INVOICE", "POSSIBLE_DUPLICATE"}
LINE_MATCH = 0.8  # description similarity for two line items to be the same line
SUCCESS_STATUSES = {"processed", "needs_review"}


# ---------------------------------------------------------------- normalisation


def _text(value: str) -> str:
    s = unicodedata.normalize("NFKC", value).casefold().replace("&", " and ")
    return re.sub(r"\s+", " ", s).strip(" .,;:")


def _money(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _number(value: str) -> str:
    key = normalise_invoice_number(value) or ""
    return re.sub(r"^(NO|#)(?=\d)", "", key)  # "No. 247" and "247" are the same number


def normalise(name: str, value: Any) -> Any:
    """The comparable form of a field value (None stays None)."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if name in MONEY:
        return _money(value)
    if name in DATES:
        return str(value)[:10]
    if name == "currency":
        return str(value).strip().upper()
    if name == "invoice_number":
        return _number(str(value))
    return _text(str(value))


def same(name: str, truth: Any, predicted: Any) -> bool:
    a, b = normalise(name, truth), normalise(name, predicted)
    return a is not None and a == b


# ---------------------------------------------------------------- one invoice


@dataclass
class FieldOutcome:
    field: str
    outcome: str
    truth: Any
    predicted: Any
    evidence: str | None = None


@dataclass
class InvoiceScore:
    id: str
    file: str
    file_type: str
    split: str
    found: bool  # the system produced an invoice for this label
    fields: list[FieldOutcome] = field(default_factory=list)
    line_tp: int = 0
    line_fp: int = 0
    line_fn: int = 0
    flags_expected: list[list[str]] = field(default_factory=list)
    flags_predicted: list[list[str]] = field(default_factory=list)
    duplicate_expected: bool = False
    duplicate_flagged: bool = False


def _value(invoice: dict | None, name: str) -> tuple[Any, str | None]:
    f = ((invoice or {}).get("fields") or {}).get(name) or {}
    return f.get("value"), f.get("evidence")


def _flags(invoice: dict | None) -> list[tuple[str, str | None]]:
    return [(i["code"], i.get("field")) for i in (invoice or {}).get("issues") or []]


def score_fields(label: dict, invoice: dict | None) -> list[FieldOutcome]:
    flagged = {f for code, f in _flags(invoice) if code == "AMBIGUOUS_DATE"}
    out = []
    for name in FIELDS:
        truth = label["fields"].get(name)
        predicted, evidence = _value(invoice, name)
        t, p = normalise(name, truth), normalise(name, predicted)
        if t is None:
            outcome = "correct_null" if p is None else "false_fill"
        elif p is None:
            ambiguous = name in label.get("ambiguous_fields", []) and name in flagged
            outcome = "abstained" if ambiguous else "missing"
        else:
            outcome = "correct" if t == p else "wrong"
        out.append(FieldOutcome(name, outcome, truth, predicted, evidence))
    return out


def match_lines(truth: list[dict], predicted: list[dict]) -> tuple[int, int, int]:
    """(true positives, false positives, false negatives). A predicted line matches a true
    one when its description is similar enough AND its line total is exactly the same."""
    pairs = []
    for i, t in enumerate(truth):
        for j, p in enumerate(predicted):
            desc = ((p.get("description") or {}).get("value")) or ""
            total = _money((p.get("line_total") or {}).get("value"))
            if total is None or total != _money(t["line_total"]):
                continue
            ratio = SequenceMatcher(None, _text(t["description"]), _text(desc)).ratio()
            if ratio >= LINE_MATCH:
                pairs.append((ratio, i, j))
    used_t, used_p = set(), set()
    for _, i, j in sorted(pairs, reverse=True):
        if i not in used_t and j not in used_p:
            used_t.add(i)
            used_p.add(j)
    tp = len(used_t)
    return tp, len(predicted) - tp, len(truth) - tp


def score_invoice(label: dict, invoice: dict | None) -> InvoiceScore:
    expected = [(f["code"], f["field"]) for f in label.get("expected_flags", [])]
    predicted = _flags(invoice)
    tp, fp, fn = match_lines(label.get("line_items", []), (invoice or {}).get("line_items") or [])
    return InvoiceScore(
        id=label["id"],
        file=label["file"],
        file_type=label["file_type"],
        split=label["split"],
        found=invoice is not None,
        fields=score_fields(label, invoice),
        line_tp=tp,
        line_fp=fp,
        line_fn=fn,
        flags_expected=sorted([c, f] for c, f in expected if c in SCORED_FLAGS),
        flags_predicted=sorted([c, f or ""] for c, f in predicted if c in SCORED_FLAGS),
        duplicate_expected=any(c in DUPLICATE_FLAGS for c, _ in expected),
        duplicate_flagged=any(c in DUPLICATE_FLAGS for c, _ in predicted),
    )


def pick_invoice(label: dict, invoices: list[dict]) -> dict | None:
    """The system's invoice for this label: by expected page range in a batch, else the
    first (or only) one."""
    if not invoices:
        return None
    segment = label.get("segment")
    if not segment:
        return invoices[0]
    exact = [i for i in invoices if [i.get("page_start"), i.get("page_end")] == segment]
    if exact:
        return exact[0]
    start = [i for i in invoices if i.get("page_start") == segment[0]]
    return start[0] if start else None


# ---------------------------------------------------------------- aggregates


def _rate(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole else None


def _prf(tp: int, fp: int, fn: int) -> dict[str, Any]:
    precision, recall = _rate(tp, tp + fp), _rate(tp, tp + fn)
    f1 = (
        round(2 * precision * recall / (precision + recall), 4)
        if precision and recall
        else (0.0 if precision is not None and recall is not None else None)
    )
    return {"precision": precision, "recall": recall, "f1": f1, "tp": tp, "fp": fp, "fn": fn}


def field_metrics(scores: Iterable[InvoiceScore]) -> dict[str, Any]:
    counts: dict[str, Counter] = defaultdict(Counter)
    for s in scores:
        for f in s.fields:
            counts[f.field][f.outcome] += 1
            counts["_all"][f.outcome] += 1

    def summary(c: Counter) -> dict[str, Any]:
        total = sum(c.values())
        has_value = c["correct"] + c["abstained"] + c["wrong"] + c["missing"]
        null_truth = c["correct_null"] + c["false_fill"]
        return {
            "n": total,
            "accuracy": _rate(c["correct"] + c["correct_null"] + c["abstained"], total),
            "false_fill_rate": _rate(c["false_fill"], null_truth),
            "missing_rate": _rate(c["missing"], has_value),
            "wrong_rate": _rate(c["wrong"], has_value),
            "outcomes": dict(c),
        }

    overall = summary(counts.pop("_all", Counter()))
    return {"overall": overall, "per_field": {k: summary(v) for k, v in sorted(counts.items())}}


def line_metrics(scores: Iterable[InvoiceScore]) -> dict[str, Any]:
    scores = list(scores)
    return _prf(
        sum(s.line_tp for s in scores),
        sum(s.line_fp for s in scores),
        sum(s.line_fn for s in scores),
    )


def flag_metrics(scores: Iterable[InvoiceScore]) -> dict[str, Any]:
    tp = fp = fn = 0
    per_code: dict[str, Counter] = defaultdict(Counter)
    for s in scores:
        expected = Counter(tuple(x) for x in s.flags_expected)
        predicted = Counter(tuple(x) for x in s.flags_predicted)
        for key in expected | predicted:
            hit = min(expected[key], predicted[key])
            tp += hit
            fp += predicted[key] - hit
            fn += expected[key] - hit
            per_code[key[0]]["tp"] += hit
            per_code[key[0]]["fp"] += predicted[key] - hit
            per_code[key[0]]["fn"] += expected[key] - hit
    return {
        **_prf(tp, fp, fn),
        "per_code": {code: _prf(c["tp"], c["fp"], c["fn"]) for code, c in sorted(per_code.items())},
    }


def duplicate_metrics(scores: Iterable[InvoiceScore]) -> dict[str, Any]:
    scores = list(scores)
    expected = [s for s in scores if s.duplicate_expected]
    return {
        "pairs": len(expected),
        "detected": sum(s.duplicate_flagged for s in expected),
        "false_alarms": sum(s.duplicate_flagged for s in scores if not s.duplicate_expected),
    }


def segment_metrics(expected: list[list[int]], predicted: list[list[int]]) -> dict[str, Any]:
    exact = len({tuple(x) for x in expected} & {tuple(x) for x in predicted})
    return {
        "expected": len(expected),
        "predicted": len(predicted),
        "exact": exact,
        "precision": _rate(exact, len(predicted)),
        "recall": _rate(exact, len(expected)),
        "count_right": len(expected) == len(predicted),
    }


def crosscheck_metrics(expected: list[dict], findings: list[dict]) -> dict[str, Any]:
    """Recall of planted findings; extra = non-"matched" findings nobody planted."""
    issues = [f for f in findings if f.get("kind") != "matched"]
    used: set[int] = set()
    found = []
    for e in expected:
        key = _number(e["invoice_number"])
        hit = None
        for n, f in enumerate(issues):
            text = " ".join(str(f.get(k) or "") for k in ("message", "value_a", "value_b"))
            if (
                n not in used
                and f.get("kind") == e["kind"]
                and (e.get("field") is None or f.get("field") == e["field"])
                and key in _number(text.replace(" ", ""))
            ):
                hit = n
                break
        if hit is not None:
            used.add(hit)
        found.append({**e, "found": hit is not None})
    return {
        "expected": len(expected),
        "found": sum(x["found"] for x in found),
        "recall": _rate(sum(x["found"] for x in found), len(expected)),
        "extra_findings": len(issues) - len(used),
        "details": found,
    }


def percentile(values: list[float], q: float) -> float | None:
    """Linear-interpolated percentile (q in 0..100); None for no values."""
    if not values:
        return None
    if len(values) == 1:
        return round(values[0], 3)
    return round(statistics.quantiles(sorted(values), n=100, method="inclusive")[int(q) - 1], 3)


def as_dict(score: InvoiceScore) -> dict[str, Any]:
    return asdict(score)
