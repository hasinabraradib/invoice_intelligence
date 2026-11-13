"""Compare two sides and list findings. Pure functions: no database, no LLM.

Side A is always invoices (from a PDF/scan). Side B is either ledger rows (a spreadsheet
listing invoices) or invoices from another PDF (e.g. a purchase order or a second copy).

Matching, in order:
1. same normalised invoice number (INV-001 = inv 001), and the same supplier if both sides
   name one;
2. otherwise same supplier + same date + same total (within the money tolerance).
Values are only compared, never changed. Money is Decimal throughout.
"""

import re
import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Literal

from app.extraction.postprocess import normalise_supplier_key, normalise_text

Mode = Literal["invoices_vs_ledger", "invoices_vs_invoices"]
MONEY_FIELDS = ("subtotal", "tax", "total")
LINE_SIMILARITY = 0.6  # descriptions at least this similar are the same line


@dataclass(frozen=True)
class Line:
    position: int
    description: str | None
    quantity: Decimal | None
    unit_price: Decimal | None
    line_total: Decimal | None
    page: int | None = None


@dataclass(frozen=True)
class Entry:
    """One invoice (side A or B) or one ledger row (side B), with where it came from."""

    ref: uuid.UUID  # invoice id or ledger row id
    number: str | None = None
    number_key: str | None = None
    supplier: str | None = None
    supplier_key: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    customer: str | None = None
    currency: str | None = None
    subtotal: Decimal | None = None
    tax: Decimal | None = None
    total: Decimal | None = None
    lines: tuple[Line, ...] = ()
    pages: dict[str, int] = field(default_factory=dict)  # field -> evidence page
    page: int | None = None  # first page of the invoice
    segment: int | None = None
    sheet: str | None = None  # ledger rows
    row: int | None = None

    def page_of(self, name: str) -> int | None:
        return self.pages.get(name, self.page)


@dataclass
class Finding:
    kind: str
    severity: Literal["info", "warning", "error"]
    message: str
    a: Entry | None = None
    b: Entry | None = None
    field: str | None = None
    value_a: str | None = None
    value_b: str | None = None
    page_a: int | None = None
    page_b: int | None = None

    @property
    def is_mismatch(self) -> bool:
        return self.severity == "error" or self.kind.startswith("missing_in_")


def _text(value: object) -> str | None:
    return None if value is None else str(value)


def _same_supplier(a: Entry, b: Entry) -> bool:
    return not (a.supplier_key and b.supplier_key) or a.supplier_key == b.supplier_key


def match(a: list[Entry], b: list[Entry], tolerance: Decimal):
    """Returns (pairs, unmatched A, unmatched B)."""
    used: set[uuid.UUID] = set()
    pairs: dict[uuid.UUID, Entry] = {}
    for entry in a:  # 1. invoice number
        if not entry.number_key:
            continue
        for other in b:
            if (
                other.ref not in used
                and other.number_key == entry.number_key
                and _same_supplier(entry, other)
            ):
                pairs[entry.ref] = other
                used.add(other.ref)
                break
    for entry in a:  # 2. supplier + date + total
        if entry.ref in pairs or not (
            entry.supplier_key and entry.invoice_date and entry.total is not None
        ):
            continue
        for other in b:
            if (
                other.ref not in used
                and other.supplier_key == entry.supplier_key
                and other.invoice_date == entry.invoice_date
                and other.total is not None
                and abs(other.total - entry.total) <= tolerance
            ):
                pairs[entry.ref] = other
                used.add(other.ref)
                break
    return (
        [(e, pairs[e.ref]) for e in a if e.ref in pairs],
        [e for e in a if e.ref not in pairs],
        [o for o in b if o.ref not in used],
    )


def ledger_duplicates(rows: list[Entry]) -> tuple[list[Finding], set[uuid.UUID]]:
    """Rows repeating an earlier row's invoice number (and supplier)."""
    first: dict[tuple, Entry] = {}
    findings, extra = [], set()
    for row in rows:
        if not row.number_key:
            continue
        key = (row.number_key, row.supplier_key)
        if key in first:
            original = first[key]
            extra.add(row.ref)
            findings.append(
                Finding(
                    "duplicate_in_ledger",
                    "warning",
                    f"Invoice {row.number} appears again in the ledger "
                    f"(first on row {original.row}).",
                    b=row,
                    field="invoice_number",
                    value_b=row.number,
                    page_b=None,
                )
            )
        else:
            first[key] = row
    return findings, extra


def compare(a: list[Entry], b: list[Entry], *, mode: Mode, tolerance: Decimal) -> list[Finding]:
    findings: list[Finding] = []
    extra: set[uuid.UUID] = set()
    if mode == "invoices_vs_ledger":
        duplicates, extra = ledger_duplicates(b)
        findings += duplicates
    pairs, only_a, only_b = match(a, [e for e in b if e.ref not in extra], tolerance)

    for entry_a, entry_b in pairs:
        differences = _compare_pair(entry_a, entry_b, mode, tolerance)
        findings += differences or [
            Finding(
                "matched",
                "info",
                f"Invoice {entry_a.number or entry_b.number} matches.",
                a=entry_a,
                b=entry_b,
                field="total",
                value_a=_text(entry_a.total),
                value_b=_text(entry_b.total),
                page_a=entry_a.page_of("total"),
                page_b=entry_b.page_of("total"),
            )
        ]
    ledger = mode == "invoices_vs_ledger"
    for entry in only_a:
        findings.append(
            Finding(
                "missing_in_ledger" if ledger else "missing_in_b",
                "warning",
                f"Invoice {entry.number or '(no number)'} is not in the "
                f"{'ledger' if ledger else 'other document'}.",
                a=entry,
                field="invoice_number",
                value_a=entry.number,
                page_a=entry.page_of("invoice_number"),
            )
        )
    for entry in only_b:
        findings.append(
            Finding(
                "missing_in_pdf" if ledger else "missing_in_a",
                "warning",
                f"Invoice {entry.number or '(no number)'} is not in the "
                f"{'PDF' if ledger else 'first document'}.",
                b=entry,
                field="invoice_number",
                value_b=entry.number,
                page_b=entry.page_of("invoice_number"),
            )
        )
    return findings


def _compare_pair(a: Entry, b: Entry, mode: Mode, tolerance: Decimal) -> list[Finding]:
    out: list[Finding] = []

    def add(kind: str, name: str, value_a, value_b, what: str) -> None:
        out.append(
            Finding(
                kind,
                "error",
                f"{what} differs for invoice {a.number or b.number}.",
                a=a,
                b=b,
                field=name,
                value_a=_text(value_a),
                value_b=_text(value_b),
                page_a=a.page_of(name),
                page_b=b.page_of(name),
            )
        )

    money = ("tax", "total") if mode == "invoices_vs_ledger" else MONEY_FIELDS
    for name in money:
        va, vb = getattr(a, name), getattr(b, name)
        if va is not None and vb is not None and abs(va - vb) > tolerance:
            add("amount_mismatch", name, va, vb, name.capitalize())
    dates = ("invoice_date",) if mode == "invoices_vs_ledger" else ("invoice_date", "due_date")
    for name in dates:
        va, vb = getattr(a, name), getattr(b, name)
        if va and vb and va != vb:
            add("date_mismatch", name, va, vb, name.replace("_", " ").capitalize())
    if a.currency and b.currency and a.currency != b.currency:
        add("currency_mismatch", "currency", a.currency, b.currency, "Currency")
    if a.supplier_key and b.supplier_key and a.supplier_key != b.supplier_key:
        add("field_mismatch", "supplier_name", a.supplier, b.supplier, "Supplier")
    if mode == "invoices_vs_invoices":
        ca, cb = normalise_supplier_key(a.customer), normalise_supplier_key(b.customer)
        if ca and cb and ca != cb:
            add("field_mismatch", "customer_name", a.customer, b.customer, "Customer")
        out += compare_lines(a, b, tolerance)
    return out


def _description(text: str | None) -> str:
    return re.sub(r"[^\w ]+", " ", normalise_text(text or "")).strip()


def compare_lines(a: Entry, b: Entry, tolerance: Decimal) -> list[Finding]:
    """Pair line items by description similarity (and equal totals as a tie-breaker), then
    compare quantity, unit price and line total."""
    candidates = []
    for la in a.lines:
        for lb in b.lines:
            ratio = SequenceMatcher(
                None, _description(la.description), _description(lb.description)
            ).ratio()
            same_total = (
                la.line_total is not None
                and lb.line_total is not None
                and abs(la.line_total - lb.line_total) <= tolerance
            )
            if ratio >= LINE_SIMILARITY or (ratio >= 0.3 and same_total):
                candidates.append((ratio + (0.2 if same_total else 0), la, lb))
    used_a, used_b, out = set(), set(), []
    for _, la, lb in sorted(candidates, key=lambda c: -c[0]):
        if la.position in used_a or lb.position in used_b:
            continue
        used_a.add(la.position)
        used_b.add(lb.position)
        for name in ("quantity", "unit_price", "line_total"):
            va, vb = getattr(la, name), getattr(lb, name)
            if va is not None and vb is not None and abs(va - vb) > tolerance:
                out.append(
                    Finding(
                        "line_item_mismatch",
                        "error",
                        f'Line "{la.description}": {name.replace("_", " ")} differs.',
                        a=a,
                        b=b,
                        field=f"line_items[{la.position}].{name}",
                        value_a=_text(va),
                        value_b=_text(vb),
                        page_a=la.page,
                        page_b=lb.page,
                    )
                )
    for la in a.lines:
        if la.position not in used_a:
            out.append(
                Finding(
                    "line_missing_in_b",
                    "error",
                    f'Line "{la.description}" is not in the other document.',
                    a=a,
                    b=b,
                    field=f"line_items[{la.position}]",
                    value_a=la.description,
                    page_a=la.page,
                )
            )
    for lb in b.lines:
        if lb.position not in used_b:
            out.append(
                Finding(
                    "line_missing_in_a",
                    "error",
                    f'Line "{lb.description}" is not in the first document.',
                    a=a,
                    b=b,
                    field=f"line_items[{lb.position}]",
                    value_b=lb.description,
                    page_b=lb.page,
                )
            )
    return out
