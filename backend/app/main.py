"""FastAPI application entry point (run with: uvicorn app.main:app)."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from redis.asyncio import Redis

from app.api import (
    assistant,
    crosschecks,
    documents,
    errors,
    evaluation,
    health,
    invoices,
    jobs,
    search,
)
from app.config import get_settings
from app.db import create_engine, create_sessionmaker
from app.jobs import ArqQueue

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

TAGS = [
    {
        "name": "assistant",
        "description": "Chat: ask questions, propose changes (confirmed by you).",
    },
    {"name": "documents", "description": "Upload invoices, follow processing, read results."},
    {"name": "invoices", "description": "Human corrections (audited)."},
    {"name": "search", "description": "Keyword search over page text."},
    {"name": "cross-checks", "description": "Compare invoices with a ledger or another PDF."},
    {"name": "jobs", "description": "Background job maintenance."},
    {"name": "health", "description": "Liveness of the app, database and Redis."},
]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Startup: create shared clients ONCE and keep them on app.state.
    # None of these open a connection yet; connections are made lazily on first use.
    settings = get_settings()
    app.state.engine = create_engine(settings.database_url)
    app.state.sessionmaker = create_sessionmaker(app.state.engine)
    app.state.redis = Redis.from_url(settings.redis_url)
    app.state.queue = ArqQueue(settings.redis_url)
    try:
        yield
    finally:
        # Shutdown: close pools cleanly so Postgres/Redis don't see dropped connections.
        await app.state.queue.close()
        await app.state.redis.aclose()
        await app.state.engine.dispose()


def create_app() -> FastAPI:
    # A factory makes it easy for tests to build a fresh app instance.
    app = FastAPI(
        title="Invoice Intelligence API",
        version="0.2.0",
        lifespan=lifespan,
        openapi_tags=TAGS,
        description="Upload invoices; get validated, evidence-backed fields.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().cors_origins,
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["*"],
    )
    errors.install(app)
    for module in (health, assistant, documents, invoices, crosschecks, search, jobs, evaluation):
        app.include_router(module.router)
    return app


app = create_app()
