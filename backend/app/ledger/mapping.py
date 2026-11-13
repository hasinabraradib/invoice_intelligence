"""Recognise a ledger (a sheet listing many invoices) and map its columns. Pure functions.

Columns are mapped by their header text using synonyms, in plain code (no LLM): "Inv #",
"Bill No." -> invoice_number; "Vendor", "Supplier name" -> supplier_name; ...
Values are parsed by the same tested helpers as extracted invoices (Decimal, ISO dates).
"""

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from app.extraction.postprocess import (
    normalise_currency,
    normalise_invoice_number,
    normalise_supplier_key,
    parse_amount,
    parse_date,
)

LEDGER_FIELDS = ("invoice_number", "invoice_date", "supplier_name", "total", "tax", "currency")

# Header words for each field, most specific first. Headers are compared after lowercasing
# and removing punctuation and bracketed units, e.g. "Amount (BDT)" -> "amount".
SYNONYMS: dict[str, list[str]] = {
    "invoice_number": [
        "invoice number",
        "invoice no",
        "invoice #",
        "inv no",
        "inv #",
        "inv number",
        "bill no",
        "bill number",
        "invoice id",
        "voucher no",
        "document no",
        "doc no",
        "invoice ref",
        "invoice",
    ],
    "invoice_date": ["invoice date", "inv date", "bill date", "document date", "date"],
    "supplier_name": [
        "supplier name",
        "vendor name",
        "supplier",
        "vendor",
        "seller",
        "payee",
        "creditor",
        "party name",
        "party",
    ],
    "total": [
        "invoice total",
        "total amount",
        "grand total",
        "gross amount",
        "amount due",
        "net payable",
        "total",
        "amount",
        "value",
    ],
    "tax": ["tax amount", "vat amount", "gst amount", "tax", "vat", "gst"],
    "currency": ["currency", "curr", "ccy"],
}
MIN_ROWS = 2  # a "list of invoices" has more than one


def normalise_header(header: str) -> str:
    text = re.sub(r"\([^)]*\)", " ", header.lower())  # "Amount (BDT)" -> "amount"
    text = text.replace("no.", "no").replace("nr", "no")
    text = re.sub(r"[^\w#]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _score(header: str, synonym: str) -> int:
    """Higher = better. Exact header 100+, header containing the synonym as words 50+;
    longer synonyms beat shorter ones ("invoice date" beats "date")."""
    if header == synonym:
        return 100 + len(synonym)
    if re.search(rf"(?<!\w){re.escape(synonym)}(?!\w)", header):
        return 50 + len(synonym)
    return 0


def map_columns(columns: list[str]) -> dict[str, str]:
    """{field: column} using synonyms. Each column is used for at most one field; the best
    scoring (field, column) pairs are taken first."""
    pairs = []
    for column in columns:
        header = normalise_header(column)
        for name, synonyms in SYNONYMS.items():
            best = max((_score(header, s) for s in synonyms), default=0)
            if best:
                pairs.append((best, name, column))
    mapping: dict[str, str] = {}
    used: set[str] = set()
    for _, name, column in sorted(pairs, key=lambda p: -p[0]):
        if name not in mapping and column not in used:
            mapping[name] = column
            used.add(column)
    return mapping


def is_ledger(mapping: dict[str, str | None], row_count: int) -> bool:
    """Invoice number + an amount + (date or supplier), over at least two rows."""
    has = {name for name, column in mapping.items() if column}
    return (
        row_count >= MIN_ROWS
        and {"invoice_number", "total"} <= has
        and bool(has & {"invoice_date", "supplier_name"})
    )


@dataclass
class LedgerValues:
    invoice_number: str | None = None
    invoice_number_key: str | None = None
    invoice_date: date | None = None
    supplier_name: str | None = None
    supplier_key: str | None = None
    total: Decimal | None = None
    tax: Decimal | None = None
    currency: str | None = None
    problems: list[str] = field(default_factory=list)  # e.g. "ambiguous date" (never guessed)

    @property
    def is_empty(self) -> bool:
        return self.invoice_number is None and self.total is None


def row_values(cells: dict[str, str], mapping: dict[str, str | None]) -> LedgerValues:
    """Parse one row with the mapping. Unclear values stay None with a problem noted."""

    def raw(name: str) -> str | None:
        column = mapping.get(name)
        value = (cells.get(column) or "").strip() if column else ""
        return value or None

    out = LedgerValues()
    out.invoice_number = raw("invoice_number")
    out.invoice_number_key = normalise_invoice_number(out.invoice_number)
    out.supplier_name = raw("supplier_name")
    out.supplier_key = normalise_supplier_key(out.supplier_name)
    if text := raw("invoice_date"):
        iso, ambiguous = parse_date(text[:10] if re.match(r"\d{4}-\d{2}-\d{2} ", text) else text)
        out.invoice_date = date.fromisoformat(iso) if iso else None
        if iso is None:
            out.problems.append(f"date {text!r} is {'ambiguous' if ambiguous else 'unreadable'}")
    for name in ("total", "tax"):
        if text := raw(name):
            value = parse_amount(text)
            setattr(out, name, value)
            if value is None:
                out.problems.append(f"{name} {text!r} is not a clear amount")
    if text := raw("currency"):
        out.currency = normalise_currency(text)
        if out.currency is None:
            out.problems.append(f"currency {text!r} is unknown")
    return out
