"""API key auth, rate limits, security headers and CORS."""

import pytest
from pydantic import SecretStr

from app.security import key_id, key_matches

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------- API keys


def test_key_matches_checks_every_key_exactly():
    keys = [b"first-key", b"second-key"]
    assert key_matches("second-key", keys)
    assert not key_matches("second", keys)  # a prefix is not a match
    assert not key_matches("second-key ", keys)
    assert not key_matches(None, keys)
    assert not key_matches("anything", [])
    assert key_id("second-key") != "second-key" and len(key_id("second-key")) == 12


async def test_health_is_open_but_everything_else_needs_a_key(api):
    assert (await api.get("/health", headers={"X-API-Key": ""})).status_code in (200, 503)

    for path in ("/documents", "/stats", "/search?q=x", "/cross-checks", "/evaluation/latest"):
        missing = await api.get(path, headers={"X-API-Key": ""})
        assert missing.status_code == 401, path
        assert missing.json()["error"]["code"] == "unauthorized"
        wrong = await api.get(path, headers={"X-API-Key": "not-the-key"})
        assert wrong.status_code == 401, path

    assert (await api.get("/documents")).status_code == 200  # the fixture sends the key


async def test_writes_need_a_key_too(api):
    response = await api.post("/assistant/conversations", json={}, headers={"X-API-Key": "nope"})
    assert response.status_code == 401
    response = await api.post(
        "/documents", files={"files": ("a.pdf", b"%PDF-1.4")}, headers={"X-API-Key": ""}
    )
    assert response.status_code == 401


async def test_fails_closed_without_configured_keys(api, settings):
    settings.api_keys = None
    response = await api.get("/documents")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "auth_not_configured"


async def test_several_keys_can_be_configured(api, settings):
    settings.api_keys = SecretStr("old-key, new-key")
    assert (await api.get("/documents", headers={"X-API-Key": "new-key"})).status_code == 200
    assert (await api.get("/documents", headers={"X-API-Key": "old-key"})).status_code == 200


# ---------------------------------------------------------------- rate limits


async def test_search_is_rate_limited_with_a_clear_429(api, settings):
    settings.rate_limit_search_per_minute = 2
    headers = {"X-Forwarded-For": "203.0.113.7"}
    for _ in range(2):
        assert (await api.get("/search?q=invoice", headers=headers)).status_code == 200
    limited = await api.get("/search?q=invoice", headers=headers)
    assert limited.status_code == 429
    body = limited.json()["error"]
    assert body["code"] == "rate_limited"
    assert "2 per minute" in body["message"]
    assert int(limited.headers["Retry-After"]) >= 1

    # Another end user behind the same frontend has their own budget.
    other = await api.get("/search?q=invoice", headers={"X-Forwarded-For": "198.51.100.9"})
    assert other.status_code == 200


async def test_uploads_are_rate_limited(api, settings):
    settings.rate_limit_uploads_per_minute = 1
    first = await api.post("/documents", files={"files": ("a.txt", b"not a pdf")})
    assert first.status_code != 429
    second = await api.post("/documents", files={"files": ("a.txt", b"not a pdf")})
    assert second.status_code == 429
    assert "uploads" in second.json()["error"]["message"]


async def test_assistant_messages_are_rate_limited(api, settings):
    settings.rate_limit_assistant_per_minute = 1
    conversation = (await api.post("/assistant/conversations", json={})).json()
    url = f"/assistant/conversations/{conversation['id']}/messages"
    first = await api.post(url, json={"content": "hello"})
    assert first.status_code == 200
    second = await api.post(url, json={"content": "hello again"})
    assert second.status_code == 429


async def test_zero_turns_a_limit_off(api, settings):
    settings.rate_limit_search_per_minute = 0
    for _ in range(5):
        assert (await api.get("/search?q=invoice")).status_code == 200


# ---------------------------------------------------------------- headers and CORS


async def test_security_headers_on_every_response(api):
    for response in (
        await api.get("/health"),
        await api.get("/documents"),
        await api.get("/documents", headers={"X-API-Key": ""}),
    ):
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["referrer-policy"] == "no-referrer"
        csp = response.headers["content-security-policy"]
        assert "default-src 'none'" in csp and "frame-ancestors 'none'" in csp
    docs = await api.get("/docs")
    assert "cdn.jsdelivr.net" in docs.headers["content-security-policy"]


async def test_cors_allows_no_browser_origin_by_default(api):
    response = await api.options(
        "/documents",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert "access-control-allow-origin" not in response.headers
