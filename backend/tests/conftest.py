"""Shared pytest fixtures.

Unit tests must not need a running Postgres/Redis, so we provide dummy URLs here.
setdefault() keeps real values if they are already set (e.g. inside docker compose).
"""

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

import pytest  # noqa: E402  (env vars must be set before the app is imported)
from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402


@pytest.fixture
def client():
    # Using TestClient as a context manager runs the app's startup/shutdown (lifespan).
    with TestClient(create_app()) as test_client:
        yield test_client
