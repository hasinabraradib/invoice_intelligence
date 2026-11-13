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


def get_sessionmaker(request: Request):
    """Session factory, for work that outlives the request handler (SSE streams)."""
    return request.app.state.sessionmaker


def get_assistant_llm(settings: Annotated[Settings, Depends(get_settings)]):
    """The configured LLM, or a stand-in that explains it is not configured."""
    from app.extraction.llm import UnconfiguredLLM, get_llm_client

    try:
        return get_llm_client(settings)
    except ValueError as exc:
        return UnconfiguredLLM(str(exc))
