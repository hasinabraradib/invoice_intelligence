"""GET /search: keyword search over all page texts, with the same filters as the list."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import Page
from app.db import get_session
from app.filters import DocumentFilters
from app.models import DocumentStatus
from app.search import SearchHit, search

router = APIRouter(tags=["search"])


@router.get("/search", response_model=Page[SearchHit], summary="Search document text")
async def search_documents(
    session: Annotated[AsyncSession, Depends(get_session)],
    q: Annotated[str, Query(min_length=1, max_length=200, examples=["office chairs"])],
    status_: Annotated[DocumentStatus | None, Query(alias="status")] = None,
    needs_review: bool | None = None,
    supplier: Annotated[str | None, Query(max_length=200)] = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[SearchHit]:
    """Ranked matches per page, with a short snippet where matches are wrapped in
    `<mark>` (the rest of the snippet is HTML-escaped, so it is safe to render)."""
    filters = DocumentFilters(status_, needs_review, supplier, date_from, date_to)
    hits, total = await search(session, q, filters, limit=limit, offset=offset)
    return Page[SearchHit](items=hits, total=total, limit=limit, offset=offset)
