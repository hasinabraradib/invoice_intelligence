"""Search over page texts. Keyword search now; semantic search (pgvector) plugs in later
as another function with the same signature, chosen by `mode`."""

import html
import uuid
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.filters import DocumentFilters
from app.models import Document, DocumentStatus, Invoice, PageText

# Rare control characters as highlight markers: the snippet is HTML-escaped afterwards and
# only then are the markers turned into <mark> tags, so document text can never inject HTML.
_START, _STOP = "\x02", "\x03"
_HEADLINE = f"StartSel={_START}, StopSel={_STOP}, MaxWords=25, MinWords=8, MaxFragments=2"
_CONFIG = literal_column("'simple'::regconfig")  # same config page_texts.search uses


class SearchHit(BaseModel):
    document_id: uuid.UUID
    filename: str
    page_number: int
    rank: float
    snippet: str  # HTML-safe; matches wrapped in <mark>...</mark>
    status: DocumentStatus
    supplier_name: str | None = None
    invoice_number: str | None = None
    invoice_date: date | None = None
    total: Decimal | None = None
    currency: str | None = None


async def search(
    session: AsyncSession,
    q: str,
    filters: DocumentFilters,
    *,
    limit: int,
    offset: int,
    mode: Literal["keyword"] = "keyword",
) -> tuple[list[SearchHit], int]:
    if mode == "keyword":
        return await keyword_search(session, q, filters, limit=limit, offset=offset)
    raise ValueError(f"unknown search mode: {mode}")


async def keyword_search(
    session: AsyncSession, q: str, filters: DocumentFilters, *, limit: int, offset: int
) -> tuple[list[SearchHit], int]:
    # websearch_to_tsquery accepts what people type ("acme -draft", "\\"office chairs\\"")
    # and never raises a syntax error. 'simple' matches how page_texts.search was built.
    query = func.websearch_to_tsquery(_CONFIG, q)
    base = (
        select(PageText.document_id, PageText.page_number)
        .join(Document, Document.id == PageText.document_id)
        .outerjoin(Invoice, Invoice.document_id == Document.id)
        .where(PageText.search.op("@@")(query))
    )
    base = filters.apply(base)
    total = await session.scalar(select(func.count()).select_from(base.subquery()))

    rank = func.ts_rank(PageText.search, query).label("rank")
    snippet = func.ts_headline(_CONFIG, PageText.text, query, _HEADLINE).label("snippet")
    rows = await session.execute(
        filters.apply(
            select(
                PageText.document_id,
                PageText.page_number,
                rank,
                snippet,
                Document.filename,
                Document.status,
                Invoice.supplier_name,
                Invoice.invoice_number,
                Invoice.invoice_date,
                Invoice.total,
                Invoice.currency,
            )
            .join(Document, Document.id == PageText.document_id)
            .outerjoin(Invoice, Invoice.document_id == Document.id)
            .where(PageText.search.op("@@")(query))
        )
        .order_by(rank.desc(), Document.created_at.desc(), PageText.page_number)
        .limit(limit)
        .offset(offset)
    )
    hits = [SearchHit(**{**row._asdict(), "snippet": _safe_snippet(row.snippet)}) for row in rows]
    return hits, total or 0


def _safe_snippet(raw: str) -> str:
    text = " ".join(raw.split())
    return html.escape(text).replace(_START, "<mark>").replace(_STOP, "</mark>")
