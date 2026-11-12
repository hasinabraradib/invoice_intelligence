"""Shared pytest fixtures.

Unit tests must not need a running Postgres/Redis, so we provide dummy URLs here.
setdefault() keeps real values if they are already set (e.g. inside docker compose).
"""

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import pytest  # noqa: E402  (env vars must be set before the app is imported)
from fastapi.testclient import TestClient  # noqa: E402

from app.config import Settings, get_settings  # noqa: E402
from app.main import create_app  # noqa: E402


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
    return Settings(upload_dir=upload_dir, max_upload_mb=1, max_files_per_request=10)


@pytest.fixture
def client(settings):
    app = create_app()
    # Endpoints receive settings via Depends(get_settings); tests swap in their own.
    app.dependency_overrides[get_settings] = lambda: settings
    # Using TestClient as a context manager runs the app's startup/shutdown (lifespan).
    with TestClient(app) as test_client:
        yield test_client
