"""extract_invoice: chunk -> LLM (with one repair try) -> merge -> ground, parse, score.

The LLM's answer is never trusted as-is: every value must be quoted from the document,
and all numbers/dates/currencies are parsed by tested code in postprocess.py.
"""

import logging
import time
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from app.extraction import postprocess as pp
from app.extraction.llm import LLMClient, LLMError, LLMInvalidJSON
from app.extraction.prompt import (
    REPAIR_INSTRUCTION,
    SourcePage,
    build_prompts,
    chunk_pages,
    source_pages,
)
from app.extraction.schema import (
    AMOUNT_FIELDS,
    LINE_ITEM_FIELDS,
    ExtractedField,
    InvoiceExtraction,
    LineItem,
    llm_json_schema,
)
from app.parsing.models import ParsedDocument

logger = logging.getLogger(__name__)

Kind = Literal["text", "amount", "date", "currency"]
FIELD_KINDS: dict[str, Kind] = {
    "invoice_number": "text",
    "supplier_name": "text",
    "customer_name": "text",
    "invoice_date": "date",
    "due_date": "date",
    "currency": "currency",
    "subtotal": "amount",
    "tax": "amount",
    "total": "amount",
    "description": "text",
    "quantity": "amount",
    "unit_price": "amount",
    "line_total": "amount",
}


class FieldResult(BaseModel):
    raw_value: str | None = None  # exactly what the LLM quoted
    value: Decimal | date | str | None = None  # typed: Decimal (amounts), date, ISO code, text
    status: Literal["found", "missing", "uncertain", "corrected"] = "missing"
    evidence: str | None = None
    page: int | None = None
    confidence: float = 0.0
    reasons: list[str] = Field(default_factory=list)  # why the status/confidence was lowered


class LineItemResult(BaseModel):
    description: FieldResult
    quantity: FieldResult
    unit_price: FieldResult
    line_total: FieldResult


class ExtractionResult(BaseModel):
    ok: bool
    failure_reason: str | None = None
    retryable: bool = False  # True if the failure was temporary (rate limit, outage)
    provider: str
    fields: dict[str, FieldResult] = Field(default_factory=dict)
    line_items: list[LineItemResult] = Field(default_factory=list)
    supplier_key: str | None = None  # for duplicate detection later
    warnings: list[str] = Field(default_factory=list)
    chunk_count: int = 0
    llm_calls: int = 0
    duration_seconds: float = 0.0
    raw_outputs: list[dict[str, Any]] = Field(default_factory=list)  # for debugging only


def extract_invoice(
    parsed: ParsedDocument, llm: LLMClient, *, chunk_tokens: int = 6000
) -> ExtractionResult:
    """Extract ONE invoice from a whole document (see extract_from_pages)."""
    return extract_from_pages(source_pages(parsed), llm, chunk_tokens=chunk_tokens)


def extract_from_pages(
    pages: list[SourcePage], llm: LLMClient, *, chunk_tokens: int = 6000
) -> ExtractionResult:
    """Extract one invoice from the given pages (e.g. one segment of a batch PDF). Page
    numbers stay the document's own, so evidence points at the right page.

    Never raises for LLM problems: returns ok=False with a reason instead."""
    started = time.perf_counter()
    result = ExtractionResult(ok=False, provider=llm.name)
    if not pages:
        result.failure_reason = "document has no text to extract from"
        return result

    chunks = chunk_pages(pages, max_tokens=chunk_tokens)
    result.chunk_count = len(chunks)
    schema = llm_json_schema()
    extractions: list[tuple[InvoiceExtraction, set[int]]] = []
    for index, chunk in enumerate(chunks, start=1):
        system, user = build_prompts(chunk, index, len(chunks))
        try:
            extraction, error = _call_with_repair(llm, system, user, schema, result)
        except LLMError as exc:
            extraction, error = None, f"LLM request failed: {exc}"
            result.retryable = exc.retryable
        if extraction is None:
            result.failure_reason = f"chunk {index}/{len(chunks)}: {error}"
            result.duration_seconds = round(time.perf_counter() - started, 2)
            logger.warning(
                "extraction failed: provider=%s chunk=%d reason=%s", llm.name, index, error
            )
            return result
        extractions.append((extraction, {p.number for p in chunk}))

    merged = merge_extractions(extractions)
    _postprocess(merged, pages, result)
    result.ok = True
    result.duration_seconds = round(time.perf_counter() - started, 2)
    logger.info(
        "extraction done: provider=%s chunks=%d calls=%d found=%d uncertain=%d items=%d %.2fs",
        llm.name,
        result.chunk_count,
        result.llm_calls,
        sum(f.status == "found" for f in result.fields.values()),
        sum(f.status == "uncertain" for f in result.fields.values()),
        len(result.line_items),
        result.duration_seconds,
    )
    return result


def _call_with_repair(
    llm: LLMClient, system: str, user: str, schema: dict, result: ExtractionResult
) -> tuple[InvoiceExtraction | None, str | None]:
    """One normal call; if the reply is not valid JSON for the schema, one repair call."""
    error = ""
    for attempt in (1, 2):
        prompt = user if attempt == 1 else user + REPAIR_INSTRUCTION.format(error=error)
        result.llm_calls += 1
        try:
            raw = llm.complete_json(system, prompt, schema)
        except LLMInvalidJSON as exc:
            error = str(exc)
            result.raw_outputs.append({"_invalid_reply": error})
            continue
        result.raw_outputs.append(raw)
        try:
            return InvoiceExtraction.model_validate(raw), None
        except ValidationError as exc:
            # Only field locations and messages; never echo document values into logs.
            error = "; ".join(
                f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:3]
            )
    return None, f"invalid JSON after one repair attempt ({error})"


# ---------------------------------------------------------------- merging chunks


def merge_extractions(chunks: list[tuple[InvoiceExtraction, set[int]]]) -> InvoiceExtraction:
    """Header fields come from the FIRST chunk that has them (they are printed at the top);
    subtotal/tax/total from the LAST (printed at the end). Line items come from every
    chunk; an item on an overlap page that the previous chunk already returned is dropped.
    """
    merged = InvoiceExtraction()
    for name in InvoiceExtraction.model_fields:
        if name == "line_items":
            continue
        ordered = [c for c, _ in chunks]
        if name in AMOUNT_FIELDS:
            ordered.reverse()
        candidates = [getattr(c, name) for c in ordered]
        for wanted in ("found", "uncertain"):
            match = next((f for f in candidates if f.status == wanted), None)
            if match:
                setattr(merged, name, match)
                break

    previous_keys: set[tuple] = set()
    previous_pages: set[int] = set()
    for extraction, pages in chunks:
        overlap = pages & previous_pages
        keys = set()
        for item in extraction.line_items:
            key = _item_key(item)
            keys.add(key)
            if item.description.page in overlap and key in previous_keys:
                continue  # seen in the previous chunk's copy of this page
            merged.line_items.append(item)
        previous_keys, previous_pages = keys, pages
    return merged


def _item_key(item: LineItem) -> tuple:
    values = tuple(pp.normalise_text(getattr(item, f).value or "") for f in LINE_ITEM_FIELDS)
    return (item.description.page, *values)


# ---------------------------------------------------------------- checking fields


def _postprocess(merged: InvoiceExtraction, pages: list[SourcePage], result: ExtractionResult):
    context = _Context(pages)
    if context.suspicious:
        result.warnings.append(
            f"Document contains {len(context.suspicious)} line(s) that look like instructions "
            "to an AI (possible prompt injection); fields quoting them are marked uncertain."
        )
    for name in InvoiceExtraction.model_fields:
        if name != "line_items":
            result.fields[name] = check_field(getattr(merged, name), FIELD_KINDS[name], context)
    for item in merged.line_items:
        result.line_items.append(
            LineItemResult(
                **{
                    f: check_field(getattr(item, f), FIELD_KINDS[f], context)
                    for f in LINE_ITEM_FIELDS
                }
            )
        )
    _currency_from_amounts(result)
    supplier = result.fields["supplier_name"]
    if supplier.status != "missing":
        result.supplier_key = pp.normalise_supplier_key(str(supplier.value))


class _Context:
    """Normalised page texts, computed once instead of once per field."""

    def __init__(self, pages: list[SourcePage]) -> None:
        self.pages: dict[int, str] = {}
        self.ocr: dict[int, float | None] = {}
        for page in pages:  # split pages share a number: join their text
            self.pages[page.number] = self.pages.get(page.number, "") + " " + page.text
            self.ocr[page.number] = page.ocr_confidence
        self.pages = {n: pp.normalise_text(t) for n, t in self.pages.items()}
        self.all_text = " ".join(self.pages.values())
        self.suspicious = [
            pp.normalise_text(line) for p in pages for line in pp.suspicious_lines(p.text)
        ]


def check_field(field: ExtractedField, kind: Kind, ctx: _Context) -> FieldResult:
    """Ground the quote, parse the value, and score it. Never invents a value."""
    out = FieldResult(
        raw_value=field.value, status=field.status, evidence=field.evidence, page=field.page
    )
    value = (field.value or "").strip()
    if field.status == "missing" or not value:
        if value or field.status != "missing":
            out.reasons.append("model gave no usable value")
        return FieldResult(reasons=out.reasons)  # null value, status missing

    # 1. Grounding: the quote must really be in the document (ideally on the cited page).
    evidence = pp.normalise_text(field.evidence or "")
    grounded = False
    if not evidence:
        out.reasons.append("no evidence quote given")
    elif field.page in ctx.pages and evidence in ctx.pages[field.page]:
        grounded = True
    elif hits := [n for n, text in ctx.pages.items() if evidence in text]:
        grounded = True
        out.reasons.append(f"page corrected from {field.page} to {hits[0]}")
        out.page = hits[0]
    else:
        out.reasons.append("evidence quote not found in the document")

    # 2. The value must be inside its own quote (catches "calculated" values).
    norm_value = pp.normalise_text(value)
    in_evidence = bool(evidence) and (
        norm_value in evidence
        or (kind == "amount" and pp.parse_amount(value) in pp.amounts_in(field.evidence or ""))
    )
    if not grounded and not in_evidence and norm_value not in ctx.all_text:
        out.reasons.append("value not found in the document; dropped")
        return FieldResult(reasons=out.reasons)
    if not grounded or not in_evidence:
        if grounded:
            out.reasons.append("value does not match its evidence quote")
        out.status = "uncertain"
    if evidence and any(evidence in line or line in evidence for line in ctx.suspicious):
        out.reasons.append("evidence comes from text that looks like an instruction to the AI")
        out.status = "uncertain"

    # 3. Parse into a typed value (plain code, never the LLM).
    parsed_ok = _parse_into(out, value, kind)
    out.confidence = pp.field_confidence(
        grounded=grounded,
        status=out.status,
        parsed_ok=parsed_ok,
        ocr_confidence=ctx.ocr.get(out.page) if out.page else None,
    )
    return out


def _parse_into(out: FieldResult, value: str, kind: Kind) -> bool | None:
    if kind == "text":
        out.value = value
        return None
    if kind == "amount":
        out.value = pp.parse_amount(value)
        problem = None if out.value is not None else "could not read the amount unambiguously"
    elif kind == "date":
        iso, ambiguous = pp.parse_date(value)
        out.value = date.fromisoformat(iso) if iso else None
        problem = (
            "ambiguous date: day and month could be swapped"
            if ambiguous
            else None
            if iso
            else "could not read the date"
        )
    else:
        out.value = pp.normalise_currency(value)
        problem = None if out.value else "unknown currency"
    if problem:
        out.reasons.append(problem)
        out.status = "uncertain"
        return False
    return True


def _currency_from_amounts(result: ExtractionResult) -> None:
    """If no currency field was printed, read it from the total's own quote ("Tk 5,000").
    This is plain text parsing of a quote, not a guess."""
    currency = result.fields["currency"]
    if currency.status != "missing":
        return
    for name in ("total", "subtotal"):
        source = result.fields[name]
        code = pp.normalise_currency(source.evidence) if source.status == "found" else None
        if code:
            result.fields["currency"] = FieldResult(
                raw_value=None,
                value=code,
                status="found",
                evidence=source.evidence,
                page=source.page,
                confidence=round(source.confidence * 0.9, 2),
                reasons=[f"read from the {name} quote"],
            )
            return
