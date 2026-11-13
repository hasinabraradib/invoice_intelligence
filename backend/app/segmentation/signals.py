"""Clues on a single page that tell whether it starts a new invoice. Pure functions."""

import re
from dataclasses import dataclass

from app.extraction.postprocess import normalise_invoice_number

HEADER_CHARS = 400  # "near the top": invoice headings and numbers live in the first lines

# "INVOICE", "Tax Invoice", "Commercial invoice" at the START of a line.
_HEADING = re.compile(
    r"^\W*(?:tax\s+|commercial\s+|proforma\s+|sales\s+|vat\s+)?invoice\b",
    re.IGNORECASE | re.MULTILINE,
)
# "Invoice No: INV-2041", "Invoice #: 77", "Inv. Number 2041/A", "Bill No. 5"
_NUMBER = re.compile(
    r"\b(?:invoice|inv|bill)\.?\s*(?:no\.?|number|num\.?|#)\s*[:#.]?\s*"
    r"([A-Za-z0-9][A-Za-z0-9\-/.]{0,30}[A-Za-z0-9]|[A-Za-z0-9])",
    re.IGNORECASE,
)
# "Page 2 of 3", "page 2/3" (anywhere: often a footer)
_PAGE_OF = re.compile(r"\bpage\s+(\d{1,4})\s*(?:of|/)\s*(\d{1,4})\b", re.IGNORECASE)


@dataclass(frozen=True)
class PageSignals:
    number: int
    header: str  # first HEADER_CHARS characters (for the LLM fallback)
    empty: bool
    heading: bool
    invoice_number: str | None  # normalised ("INV2041"), for comparing pages
    page_of: tuple[int, int] | None  # (k, N) from "Page k of N"


def page_signals(number: int, text: str) -> PageSignals:
    header = text.strip()[:HEADER_CHARS]
    found = _NUMBER.search(header)
    page_of = None
    for match in _PAGE_OF.finditer(text):
        k, n = int(match[1]), int(match[2])
        if 1 <= k <= n:
            page_of = (k, n)
            break
    return PageSignals(
        number=number,
        header=header,
        empty=not header,
        heading=bool(_HEADING.search(header)),
        invoice_number=normalise_invoice_number(found[1]) if found else None,
        page_of=page_of,
    )
