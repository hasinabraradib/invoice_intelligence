"""FastAPI application entry point (run with: uvicorn app.main:app)."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.asyncio import Redis

from app.api import documents, health
from app.config import get_settings
from app.db import create_engine, create_sessionmaker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Startup: create shared clients ONCE and keep them on app.state.
    # Neither call opens a connection yet; connections are made lazily on first use.
    settings = get_settings()
    app.state.engine = create_engine(settings.database_url)
    app.state.sessionmaker = create_sessionmaker(app.state.engine)
    app.state.redis = Redis.from_url(settings.redis_url)
    try:
        yield
    finally:
        # Shutdown: close pools cleanly so Postgres/Redis don't see dropped connections.
        await app.state.redis.aclose()
        await app.state.engine.dispose()


def create_app() -> FastAPI:
    # A factory makes it easy for tests to build a fresh app instance.
    app = FastAPI(title="Invoice Intelligence API", version="0.1.0", lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(documents.router)
    return app


app = create_app()
