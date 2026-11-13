"""Decide where each invoice starts. Plain rules first; the LLM only for unclear pages.

For every page the rules answer "starts a new invoice?" with True, False or None (unclear):
- "Page 1 of N" -> True; "Page k of N" with k > 1 -> False
- an invoice number different from the current invoice's -> True; the same -> False
- the previous page said "Page k of N" with pages still to come -> False
- an "INVOICE" heading at the top (with no other clue) -> True
- an empty page -> False (blank backs of scanned sheets)
- clues that disagree, or no clue at all -> None
Unclear pages go to the classifier (an LLM) in batches. Without an answer they are joined to
the previous invoice: merging two invoices gets flagged in review, while splitting one would
silently scatter its fields.
"""

import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

from app.extraction.prompt import SourcePage
from app.segmentation.signals import PageSignals, page_signals

logger = logging.getLogger(__name__)

# (page_number, header text, previous page's header) -> {page_number: starts_new_invoice}
Classifier = Callable[[list[tuple[int, str, str]]], dict[int, bool]]


@dataclass
class Segment:
    index: int  # 0-based position in the document
    page_start: int
    page_end: int
    method: str = "rules"  # rules | llm | default (unclear page joined to previous)
    notes: list[str] = field(default_factory=list)
    recheck: bool = False  # set later if moved corrections need a human look

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def pages(self) -> range:
        return range(self.page_start, self.page_end + 1)


def decide(signals: list[PageSignals]) -> list[bool | None]:
    """True = starts a new invoice, False = continues the current one, None = unclear."""
    decisions: list[bool | None] = []
    current_number: str | None = None
    pages_to_come = 0  # from the last "Page k of N"
    for i, s in enumerate(signals):
        votes: list[bool] = []
        if s.page_of:
            votes.append(s.page_of[0] == 1)
        if s.invoice_number and current_number:
            votes.append(s.invoice_number != current_number)

        if i == 0:
            decision: bool | None = True
        elif votes:
            decision = votes[0] if len(set(votes)) == 1 else None  # clues disagree: unclear
        elif pages_to_come > 0 or s.empty:
            decision = False
        elif s.heading:
            decision = True
        else:
            decision = None
        decisions.append(decision)

        # Track the "current invoice" (an unclear page is treated as a continuation here).
        if decision or s.invoice_number and not current_number:
            current_number = s.invoice_number
        if s.page_of:
            pages_to_come = s.page_of[1] - s.page_of[0]
        elif decision:
            pages_to_come = 0
        else:
            pages_to_come = max(pages_to_come - 1, 0)
    return decisions


def segment_document(
    pages: list[SourcePage], page_count: int, classifier: Classifier | None = None
) -> list[Segment]:
    """Group pages into invoice segments covering pages 1..page_count without gaps."""
    if not pages:
        return []
    if any(p.source == "sheet" for p in pages):
        # Spreadsheets: one segment per sheet (each sheet has its own page number).
        return [Segment(i, p.number, p.number, method="sheet") for i, p in enumerate(pages)]

    merged: dict[int, str] = {}
    for page in pages:  # very long pages may have been split into several pieces
        merged[page.number] = merged.get(page.number, "") + page.text
    numbers = sorted(merged)
    signals = [page_signals(n, merged[n]) for n in numbers]
    decisions = decide(signals)

    unclear = [i for i, d in enumerate(decisions) if d is None]
    answers: dict[int, bool] = {}
    if unclear and classifier is not None:
        items = [(signals[i].number, signals[i].header, signals[i - 1].header) for i in unclear]
        try:
            answers = classifier(items)
        except Exception as exc:  # the classifier is a helper; never fail the document on it
            logger.warning("page classifier failed: %s", type(exc).__name__)

    segments: list[Segment] = []
    for s, d in zip(signals, decisions, strict=True):
        method = "rules"
        if d is None:
            if s.number in answers:
                d, method = answers[s.number], "llm"
            else:
                d, method = False, "default"
        if d or not segments:
            segments.append(Segment(len(segments), s.number, s.number, method=method))
        else:
            segments[-1].page_end = s.number
            if method != "rules":
                segments[-1].notes.append(
                    f"page {s.number} joined by {'the LLM' if method == 'llm' else 'default'}"
                )
        if method != "rules" and d:
            segments[-1].notes.append(f"start page {s.number} decided by the LLM")
    # Cover every page: the first segment starts at 1; each ends where the next begins.
    segments[0].page_start = 1
    for current, following in zip(segments, segments[1:], strict=False):
        current.page_end = following.page_start - 1
    segments[-1].page_end = max(page_count, segments[-1].page_end)
    return segments
