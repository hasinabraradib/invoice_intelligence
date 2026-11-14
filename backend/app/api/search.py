"""GET /search: keyword search over all page texts, with the same filters as the list."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_embedder
from app.api.errors import ApiError
from app.api.schemas import Page
from app.config import Settings, get_settings
from app.db import get_session
from app.filters import DocumentFilters
from app.models import DocumentStatus
from app.search import SearchHit, SearchMode, SemanticUnavailable, search
from app.search.embedder import Embedder
from app.security import rate_limit

router = APIRouter(tags=["search"])


@router.get(
    "/search",
    response_model=Page[SearchHit],
    summary="Search document text",
    dependencies=[Depends(rate_limit("search"))],
)
async def search_documents(
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    embedder: Annotated[Embedder | None, Depends(get_embedder)],
    q: Annotated[str, Query(min_length=1, max_length=200, examples=["office chairs"])],
    mode: Annotated[SearchMode, Query(description="keyword, semantic or hybrid")] = "hybrid",
    status_: Annotated[DocumentStatus | None, Query(alias="status")] = None,
    needs_review: bool | None = None,
    supplier: Annotated[str | None, Query(max_length=200)] = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[SearchHit]:
    """One hit per document (its best page or chunk), best first.

    - **keyword**: exact words (Postgres full text). Snippet has `<mark>` highlights.
    - **semantic**: similar meaning ("power charges" finds "electricity bill").
    - **hybrid** (default): both, merged with reciprocal rank fusion; `matched_by` says
      which side found it. Falls back to keyword-only if embeddings are unavailable.

    Snippets are HTML-escaped, so they are safe to render.
    """
    filters = DocumentFilters(status_, needs_review, supplier, date_from, date_to)
    try:
        hits, total = await search(
            session,
            q,
            filters,
            limit=limit,
            offset=offset,
            mode=mode,
            embedder=embedder,
            min_similarity=settings.semantic_min_similarity,
        )
    except SemanticUnavailable as exc:
        raise ApiError(503, "semantic_unavailable", f"Semantic search unavailable: {exc}.") from exc
    return Page[SearchHit](items=hits, total=total, limit=limit, offset=offset)
