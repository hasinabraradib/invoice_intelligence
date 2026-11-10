"""GET /health: reports whether the app, PostgreSQL and Redis are reachable.

The checks are real (a `SELECT 1` and a Redis `PING`), each with a timeout so a hung
dependency cannot hang the health endpoint itself. If any check fails we return HTTP 503,
which is what Docker / load balancers look at to decide whether the service is healthy.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])

CHECK_TIMEOUT_SECONDS = 2.0


class CheckResult(BaseModel):
    status: Literal["ok", "error"]
    latency_ms: float | None = None
    # Only the exception class name is returned; full details go to the server log.
    # This avoids leaking hostnames or connection strings to API callers.
    error: str | None = None


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    app: CheckResult
    database: CheckResult
    redis: CheckResult


async def check_database(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def check_redis(client: Redis) -> None:
    await client.ping()


async def _run_check(name: str, check: Callable[[], Awaitable[None]]) -> CheckResult:
    started = time.perf_counter()
    try:
        await asyncio.wait_for(check(), timeout=CHECK_TIMEOUT_SECONDS)
    except Exception as exc:  # any failure means "unhealthy", never a 500 from /health
        logger.warning("health check %s failed: %r", name, exc)
        return CheckResult(status="error", error=type(exc).__name__)
    latency = (time.perf_counter() - started) * 1000
    return CheckResult(status="ok", latency_ms=round(latency, 1))


@router.get("/health", response_model=HealthResponse)
async def health(request: Request, response: Response) -> HealthResponse:
    state = request.app.state
    # Run both checks concurrently so the worst case is one timeout, not two.
    # The lambdas look up check_* at call time, which lets tests swap them out.
    database, redis = await asyncio.gather(
        _run_check("database", lambda: check_database(state.engine)),
        _run_check("redis", lambda: check_redis(state.redis)),
    )
    healthy = database.status == "ok" and redis.status == "ok"
    response.status_code = 200 if healthy else 503
    return HealthResponse(
        status="ok" if healthy else "degraded",
        app=CheckResult(status="ok"),  # if this code runs, the app process is up
        database=database,
        redis=redis,
    )
