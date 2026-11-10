"""Tests for GET /health.

We replace the real dependency checks with fakes so the test is fast and does not need
Postgres or Redis. The real checks are exercised by `make up` + curl /health.
"""

from app.api import health


async def _ok(_client) -> None:
    return None


async def _fail(_client) -> None:
    raise ConnectionError("simulated outage")


def test_health_ok_when_all_dependencies_up(client, monkeypatch):
    monkeypatch.setattr(health, "check_database", _ok)
    monkeypatch.setattr(health, "check_redis", _ok)

    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"]["status"] == "ok"
    assert body["redis"]["status"] == "ok"


def test_health_degraded_when_database_down(client, monkeypatch):
    monkeypatch.setattr(health, "check_database", _fail)
    monkeypatch.setattr(health, "check_redis", _ok)

    response = client.get("/health")

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["database"] == {"status": "error", "latency_ms": None, "error": "ConnectionError"}
    # The error message itself must not leak to the caller.
    assert "simulated outage" not in response.text
