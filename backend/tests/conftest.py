"""Shared pytest fixtures.

Unit tests must not need a running Postgres/Redis, so we provide dummy URLs here.
setdefault() keeps real values if they are already set (e.g. inside docker compose).
Database tests use a throw-away database created for the test session and dropped after.
"""

import asyncio
import os
import uuid

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import httpx  # noqa: E402
import pytest  # noqa: E402  (env vars must be set before the app is imported)
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool  # noqa: E402

from alembic import command  # noqa: E402
from app.api.deps import get_queue  # noqa: E402
from app.config import Settings, get_settings  # noqa: E402
from app.db import get_session  # noqa: E402
from app.main import create_app  # noqa: E402

TABLES = (
    "documents, processing_jobs, invoices, line_items, validation_issues, corrections, page_texts"
)
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def anyio_backend():
    # Lets `@pytest.mark.anyio` tests run on asyncio (anyio ships with FastAPI).
    return "asyncio"


@pytest.fixture
def upload_dir(tmp_path):
    return tmp_path / "uploads"


@pytest.fixture
def settings(upload_dir):
    # Small limits so the "too large" test only needs a ~1 MB file.
    # OCR off: API tests check upload/parse behaviour; OCR has its own tests.
    return Settings(
        upload_dir=upload_dir, max_upload_mb=1, max_files_per_request=10, ocr_enabled=False
    )


# ---------------------------------------------------------------- database


async def _admin(sql: str) -> None:
    # CREATE/DROP DATABASE cannot run inside a transaction: use AUTOCOMMIT.
    engine = create_async_engine(
        os.environ["DATABASE_URL"], poolclass=NullPool, isolation_level="AUTOCOMMIT"
    )
    try:
        async with engine.connect() as conn:
            await conn.execute(text(sql))
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
def database_url():
    """Create a fresh database, apply the real Alembic migrations, drop it afterwards."""
    name = f"test_{uuid.uuid4().hex[:10]}"
    try:
        asyncio.run(_admin(f'CREATE DATABASE "{name}"'))
    except Exception as exc:  # no Postgres here (e.g. running tests on the host)
        pytest.skip(f"Postgres not reachable: {type(exc).__name__}")
    url = os.environ["DATABASE_URL"].rsplit("/", 1)[0] + f"/{name}"
    config = Config(os.path.join(BACKEND_DIR, "alembic.ini"))
    config.set_main_option("script_location", os.path.join(BACKEND_DIR, "alembic"))
    config.attributes["database_url"] = url  # read by alembic/env.py
    command.upgrade(config, "head")
    yield url
    asyncio.run(_admin(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


async def _truncate(url: str) -> None:
    engine = create_async_engine(url, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {TABLES} CASCADE"))
    await engine.dispose()


@pytest.fixture
async def db_session(database_url):
    """A session on the test database; all tables are emptied after each test."""
    engine = create_async_engine(database_url, poolclass=NullPool)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session
    await engine.dispose()
    await _truncate(database_url)


@pytest.fixture
def client(settings, database_url):
    app = create_app()
    # Endpoints receive settings and sessions via Depends(); tests swap in their own.
    app.dependency_overrides[get_settings] = lambda: settings
    # NullPool: TestClient runs the app on its own event loop, so never reuse connections.
    engine = create_async_engine(database_url, poolclass=NullPool)

    async def test_session():
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_session] = test_session
    # Using TestClient as a context manager runs the app's startup/shutdown (lifespan).
    with TestClient(app) as test_client:
        yield test_client
    asyncio.run(_truncate(database_url))


# ---------------------------------------------------------------- API + worker


class FakeQueue:
    """Stands in for Redis/arq: records which jobs were enqueued."""

    def __init__(self) -> None:
        self.job_ids: list = []

    async def enqueue(self, job_id, defer_seconds: float = 0) -> None:
        self.job_ids.append(job_id)


@pytest.fixture
def queue():
    return FakeQueue()


@pytest.fixture
async def api(settings, database_url, queue):
    """httpx AsyncClient talking to the app in-process, on the test database."""
    app = create_app()
    engine = create_async_engine(database_url, poolclass=NullPool)

    async def test_session():
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_session] = test_session
    app.dependency_overrides[get_queue] = lambda: queue
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    await engine.dispose()
    await _truncate(database_url)


@pytest.fixture
async def run_job(settings, database_url):
    """Run the worker's job function directly (no Redis, no arq process)."""
    from app.worker import process_job

    engine = create_async_engine(database_url, poolclass=NullPool)

    async def run(job_id, llm):
        def factory():
            if isinstance(llm, Exception):
                raise llm  # e.g. ValueError: LLM not configured
            return llm

        ctx = {
            "settings": settings,
            "llm_factory": factory,
            "sessionmaker": async_sessionmaker(engine, expire_on_commit=False),
        }
        return await process_job(ctx, str(job_id))

    yield run
    await engine.dispose()
