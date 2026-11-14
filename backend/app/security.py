"""API key auth, rate limits and security headers.

- Every data route (all except /health and the schema at /docs and /openapi.json, which
  hold no data) needs an `X-API-Key` header matching one of the keys in the
  API_KEYS env var (comma-separated). Keys are compared in constant time, and every
  configured key is checked, so timing reveals neither which key nor how much matched.
  With no keys configured, every protected request is refused (fail closed).
- Uploads, assistant messages and search are rate-limited per client in Redis (fixed
  one-minute windows). Over the limit: 429 with Retry-After. The client is the caller's
  key plus the end user's address that the trusted frontend proxy forwards.
- Every response carries security headers (nosniff, no framing, no referrer, a CSP that
  allows nothing to run, since this API serves JSON, images and files, not pages).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Annotated, Protocol

from fastapi import Depends, Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.errors import ApiError
from app.config import Settings, get_settings

log = logging.getLogger(__name__)

API_KEY_HEADER = "X-API-Key"


# ---------------------------------------------------------------- API keys


def configured_keys(settings: Settings) -> list[bytes]:
    raw = settings.api_keys.get_secret_value() if settings.api_keys else ""
    return [k.strip().encode() for k in raw.split(",") if k.strip()]


def key_matches(presented: str | None, keys: list[bytes]) -> bool:
    """Constant-time check of `presented` against every key (no early exit)."""
    if not presented or not keys:
        return False
    candidate = presented.encode()
    found = False
    for key in keys:
        found |= hmac.compare_digest(candidate, key)
    return found


def key_id(presented: str) -> str:
    """A short, non-reversible label for a key: safe to use in Redis keys and logs."""
    return hashlib.sha256(presented.encode()).hexdigest()[:12]


async def require_api_key(
    request: Request, settings: Annotated[Settings, Depends(get_settings)]
) -> str:
    keys = configured_keys(settings)
    presented = request.headers.get(API_KEY_HEADER)
    if not keys:
        log.error("API_KEYS is not set: refusing %s %s", request.method, request.url.path)
        raise ApiError(503, "auth_not_configured", "The API has no keys configured (set API_KEYS).")
    if not key_matches(presented, keys):
        raise ApiError(401, "unauthorized", f"Missing or invalid {API_KEY_HEADER} header.")
    request.state.key_id = key_id(presented or "")
    return request.state.key_id


# ---------------------------------------------------------------- rate limits


class RateLimiter(Protocol):
    async def hit(self, bucket: str, limit: int, window_seconds: int) -> tuple[bool, int]:
        """Count one request. Returns (allowed, seconds until the window resets)."""
        ...


class RedisRateLimiter:
    """Fixed window: INCR a per-window counter, expire it with the window."""

    def __init__(self, redis) -> None:
        self.redis = redis

    async def hit(self, bucket: str, limit: int, window_seconds: int) -> tuple[bool, int]:
        now = int(time.time())
        window = now // window_seconds
        key = f"ratelimit:{bucket}:{window}"
        try:
            pipe = self.redis.pipeline()
            pipe.incr(key)
            pipe.expire(key, window_seconds + 1)
            count, _ = await pipe.execute()
        except Exception:  # noqa: BLE001
            # Fail open: a Redis outage should not take the API down; /health reports Redis.
            log.warning("rate limiter unavailable; allowing request", exc_info=True)
            return True, 0
        retry_after = (window + 1) * window_seconds - now
        return int(count) <= limit, retry_after


class MemoryRateLimiter:
    """In-process limiter for tests (same semantics as the Redis one)."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    async def hit(self, bucket: str, limit: int, window_seconds: int) -> tuple[bool, int]:
        window = int(time.time()) // window_seconds
        key = f"{bucket}:{window}"
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key] <= limit, window_seconds


def get_rate_limiter(request: Request) -> RateLimiter:
    return request.app.state.rate_limiter


def client_id(request: Request) -> str:
    """Key holder + the end user's address. Only an authenticated caller (the frontend
    proxy) can set X-Forwarded-For, so it is trusted only after the key check."""
    forwarded = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
    address = forwarded or (request.client.host if request.client else "unknown")
    return f"{getattr(request.state, 'key_id', 'anon')}:{address[:64]}"


LIMITS: dict[str, Callable[[Settings], int]] = {
    "upload": lambda s: s.rate_limit_uploads_per_minute,
    "assistant": lambda s: s.rate_limit_assistant_per_minute,
    "search": lambda s: s.rate_limit_search_per_minute,
}

LABELS = {"upload": "uploads", "assistant": "assistant messages", "search": "searches"}


def rate_limit(group: str) -> Callable[..., Awaitable[None]]:
    """Dependency factory: `dependencies=[Depends(rate_limit("search"))]`."""

    async def check(
        request: Request,
        settings: Annotated[Settings, Depends(get_settings)],
        limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
    ) -> None:
        limit = LIMITS[group](settings)
        if limit <= 0:  # 0 turns the limit off
            return
        allowed, retry_after = await limiter.hit(f"{group}:{client_id(request)}", limit, 60)
        if not allowed:
            raise RateLimited(group, limit, retry_after)

    return check


class RateLimited(ApiError):
    def __init__(self, group: str, limit: int, retry_after: int) -> None:
        super().__init__(
            429,
            "rate_limited",
            f"Too many {LABELS[group]}: the limit is {limit} per minute. "
            f"Try again in {retry_after} s.",
        )
        self.retry_after = max(1, retry_after)


# ---------------------------------------------------------------- headers

API_HEADERS = {
    b"x-content-type-options": b"nosniff",
    b"x-frame-options": b"DENY",
    b"referrer-policy": b"no-referrer",
    b"cross-origin-resource-policy": b"same-origin",
    b"permissions-policy": b"camera=(), microphone=(), geolocation=()",
    b"content-security-policy": b"default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
}
# Swagger UI and ReDoc are pages with scripts from a CDN: give them a policy that allows
# exactly that. They show only the schema, never data, and the API port is not published
# unless docker-compose.dev.yml is used.
DOCS_CSP = (
    b"default-src 'none'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    b"style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    b"img-src 'self' data: https://fastapi.tiangolo.com; "
    b"connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
)


class SecurityHeadersMiddleware:
    """Pure ASGI (not BaseHTTPMiddleware) so streamed SSE responses are not buffered."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        docs = scope.get("path", "") in ("/docs", "/redoc")

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = [
                    (k, v) for k, v in message.get("headers", []) if k.lower() not in API_HEADERS
                ]
                for name, value in API_HEADERS.items():
                    headers.append(
                        (name, DOCS_CSP if docs and name == b"content-security-policy" else value)
                    )
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)
