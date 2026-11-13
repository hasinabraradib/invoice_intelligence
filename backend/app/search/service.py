"""Search over documents in three modes, all returning one hit per document:

- keyword:  Postgres full-text search on page_texts (exact words; ranked by ts_rank)
- semantic: nearest text_chunks by cosine distance of embeddings (meaning, not spelling)
- hybrid:   both lists merged with reciprocal rank fusion (default)

All modes apply the same DocumentFilters. Pagination happens after grouping by document,
over the best CANDIDATES matches of each kind.
"""

import html
import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

import anyio
from pydantic import BaseModel
from sqlalchemy import and_, func, literal_column, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.filters import DocumentFilters
from app.models import Document, DocumentStatus, Invoice, PageText, TextChunk
from app.search.embedder import Embedder
from app.search.fusion import rrf

SearchMode = Literal["keyword", "semantic", "hybrid"]
CANDIDATES = 200  # matches fetched per kind before grouping, fusing and paginating
RRF_K = 60
SNIPPET_CHARS = 240

# Rare control characters as highlight markers: the snippet is HTML-escaped afterwards and
# only then are the markers turned into <mark> tags, so document text can never inject HTML.
_START, _STOP = "\x02", "\x03"
_HEADLINE = f"StartSel={_START}, StopSel={_STOP}, MaxWords=25, MinWords=8, MaxFragments=2"
_CONFIG = literal_column("'simple'::regconfig")  # same config page_texts.search uses


class SemanticUnavailable(Exception):
    """Semantic search was requested but embeddings are disabled or failing."""


class SearchHit(BaseModel):
    document_id: uuid.UUID
    filename: str
    page_number: int
    snippet: str  # HTML-safe. Keyword matches are wrapped in <mark>...</mark>
    score: float  # keyword: ts_rank, semantic: cosine similarity, hybrid: RRF score
    matched_by: Literal["keyword", "semantic", "both"]
    status: DocumentStatus
    # The invoice on the matching page (a batch PDF holds many).
    invoice_id: uuid.UUID | None = None
    segment_index: int | None = None
    invoice_count: int = 1
    supplier_name: str | None = None
    invoice_number: str | None = None
    invoice_date: date | None = None
    total: Decimal | None = None
    currency: str | None = None


@dataclass
class _Match:
    document_id: uuid.UUID
    page_number: int
    snippet: str
    score: float
    info: dict  # filename, status, supplier... for the response


_INFO_COLUMNS = (
    Document.filename,
    Document.status,
    Invoice.id.label("invoice_id"),
    Invoice.segment_index,
    Invoice.supplier_name,
    Invoice.invoice_number,
    Invoice.invoice_date,
    Invoice.total,
    Invoice.currency,
)


async def search(
    session: AsyncSession,
    q: str,
    filters: DocumentFilters,
    *,
    limit: int,
    offset: int,
    mode: SearchMode = "hybrid",
    embedder: Embedder | None = None,
    min_similarity: float = 0.20,
) -> tuple[list[SearchHit], int]:
    keyword: list[_Match] = []
    semantic: list[_Match] = []
    if mode in ("keyword", "hybrid"):
        keyword = _best_per_document(await _keyword_matches(session, q, filters))
    if mode in ("semantic", "hybrid"):
        try:
            if embedder is None:
                raise SemanticUnavailable("embeddings are disabled")
            semantic = _best_per_document(
                await _semantic_matches(session, q, filters, embedder, min_similarity)
            )
        except SemanticUnavailable:
            if mode == "semantic":
                raise
        except Exception as exc:  # model failed to load/run
            if mode == "semantic":
                raise SemanticUnavailable("the embedding model is not available") from exc
            # hybrid degrades to keyword-only rather than failing the request

    if mode == "keyword":
        ranked = [(m, m.score, "keyword") for m in keyword]
    elif mode == "semantic":
        ranked = [(m, m.score, "semantic") for m in semantic]
    else:
        by_keyword = {m.document_id: m for m in keyword}
        by_semantic = {m.document_id: m for m in semantic}
        fused = rrf([[m.document_id for m in keyword], [m.document_id for m in semantic]], RRF_K)
        ranked = []
        for doc_id, score in fused:
            both = doc_id in by_keyword and doc_id in by_semantic
            # Prefer the keyword match for page and snippet: it shows the exact words.
            match = by_keyword.get(doc_id) or by_semantic[doc_id]
            source = "both" if both else ("keyword" if doc_id in by_keyword else "semantic")
            ranked.append((match, score, source))

    page_slice = ranked[offset : offset + limit]
    counts = await _invoice_counts(session, [m.document_id for m, _, _ in page_slice])
    hits = [
        SearchHit(
            document_id=m.document_id,
            page_number=m.page_number,
            snippet=m.snippet,
            score=round(score, 6),
            matched_by=source,
            invoice_count=counts.get(m.document_id, 0),
            **m.info,
        )
        for m, score, source in page_slice
    ]
    return hits, len(ranked)


def _best_per_document(matches: list[_Match]) -> list[_Match]:
    """Matches arrive best-first; keep the first (best) one of each document."""
    seen: set[uuid.UUID] = set()
    best = []
    for match in matches:
        if match.document_id not in seen:
            seen.add(match.document_id)
            best.append(match)
    return best


def _info(row) -> dict:
    return {c.key: getattr(row, c.key) for c in _INFO_COLUMNS}


def _invoice_on_page(page_column):
    """Join the invoice whose page range contains this page."""
    return and_(
        Invoice.document_id == Document.id,
        page_column.between(Invoice.page_start, Invoice.page_end),
    )


async def _keyword_matches(session, q: str, filters: DocumentFilters) -> list[_Match]:
    # websearch_to_tsquery accepts what people type ("acme -draft", '"office chairs"')
    # and never raises a syntax error.
    query = func.websearch_to_tsquery(_CONFIG, q)
    rank = func.ts_rank(PageText.search, query).label("rank")
    snippet = func.ts_headline(_CONFIG, PageText.text, query, _HEADLINE).label("snippet")
    statement = (
        filters.apply_joined(
            select(PageText.document_id, PageText.page_number, rank, snippet, *_INFO_COLUMNS)
            .join(Document, Document.id == PageText.document_id)
            .outerjoin(Invoice, _invoice_on_page(PageText.page_number))
            .where(PageText.search.op("@@")(query))
        )
        .order_by(rank.desc(), Document.created_at.desc(), PageText.page_number)
        .limit(CANDIDATES)
    )
    return [
        _Match(r.document_id, r.page_number, _safe_snippet(r.snippet), float(r.rank), _info(r))
        for r in await session.execute(statement)
    ]


async def _semantic_matches(
    session, q: str, filters: DocumentFilters, embedder: Embedder, min_similarity: float
) -> list[_Match]:
    [vector] = await anyio.to_thread.run_sync(embedder.embed, [q])
    distance = TextChunk.embedding.cosine_distance(vector).label("distance")
    # The HNSW index returns ef_search candidates before our filters run; raise it so
    # filtered searches still find enough matches.
    await session.execute(text("SET LOCAL hnsw.ef_search = 200"))
    statement = (
        filters.apply_joined(
            select(
                TextChunk.document_id,
                TextChunk.page_number,
                TextChunk.text,
                distance,
                *_INFO_COLUMNS,
            )
            .join(Document, Document.id == TextChunk.document_id)
            .outerjoin(Invoice, _invoice_on_page(TextChunk.page_number))
        )
        .order_by(distance)
        .limit(CANDIDATES)
    )
    matches = []
    for r in await session.execute(statement):
        similarity = 1.0 - float(r.distance)
        if similarity >= min_similarity:  # nearest is not always near: drop unrelated text
            matches.append(
                _Match(r.document_id, r.page_number, _plain_snippet(r.text), similarity, _info(r))
            )
    return matches


def _safe_snippet(raw: str) -> str:
    text_ = " ".join(raw.split())
    return html.escape(text_).replace(_START, "<mark>").replace(_STOP, "</mark>")


def _plain_snippet(raw: str) -> str:
    text_ = " ".join(raw.split())
    if len(text_) > SNIPPET_CHARS:
        text_ = text_[:SNIPPET_CHARS].rsplit(" ", 1)[0] + "…"
    return html.escape(text_)


async def _invoice_counts(session, document_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not document_ids:
        return {}
    rows = await session.execute(
        select(Invoice.document_id, func.count())
        .where(Invoice.document_id.in_(document_ids))
        .group_by(Invoice.document_id)
    )
    return {doc_id: count for doc_id, count in rows}
