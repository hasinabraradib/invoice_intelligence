"""Shared FastAPI dependencies (tests override these)."""

from typing import Annotated

from fastapi import Depends, Request

from app.config import Settings, get_settings
from app.jobs import JobQueue
from app.search.embedder import Embedder
from app.search.embedder import get_embedder as _configured_embedder


def get_queue(request: Request) -> JobQueue:
    return request.app.state.queue


def get_embedder(settings: Annotated[Settings, Depends(get_settings)]) -> Embedder | None:
    """Embedder for query vectors (loaded lazily, once per process); None if disabled."""
    return _configured_embedder(settings)
