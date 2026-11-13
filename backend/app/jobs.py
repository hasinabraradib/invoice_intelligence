"""Job queue helpers: enqueue, retry timing, per-job timeout, and stuck-job recovery.

The database `processing_jobs` row is the source of truth; Redis (arq) only carries
"please run job X". If a Redis message is lost or delivered twice, nothing breaks: the
worker re-reads the job row and skips jobs that are not queued.
"""

import logging
import random
import uuid
from typing import Protocol

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.config import Settings
from app.models import JobStatus
from app.repository import Repository

logger = logging.getLogger(__name__)

TASK_NAME = "process_job"
CROSS_CHECK_TASK = "run_cross_check_job"


class JobQueue(Protocol):
    async def enqueue(self, job_id: uuid.UUID, defer_seconds: float = 0) -> None: ...

    async def enqueue_cross_check(self, cross_check_id: uuid.UUID) -> None: ...


class ArqQueue:
    """Sends jobs to the arq worker through Redis. Connects lazily on first use, so the
    API still starts (and /health can report it) while Redis is down."""

    def __init__(self, redis_url: str) -> None:
        self._settings = RedisSettings.from_dsn(redis_url)
        self._pool: ArqRedis | None = None

    async def enqueue(self, job_id: uuid.UUID, defer_seconds: float = 0) -> None:
        if self._pool is None:
            self._pool = await create_pool(self._settings)
        await self._pool.enqueue_job(TASK_NAME, str(job_id), _defer_by=defer_seconds or None)

    async def enqueue_cross_check(self, cross_check_id: uuid.UUID) -> None:
        if self._pool is None:
            self._pool = await create_pool(self._settings)
        await self._pool.enqueue_job(CROSS_CHECK_TASK, str(cross_check_id))

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.aclose()


def retry_delay(attempt: int, settings: Settings) -> float:
    """Exponential backoff with jitter: ~10s, ~20s, ~40s... capped at 10 minutes."""
    delay = min(600.0, settings.job_retry_base_seconds * 2 ** (attempt - 1))
    return delay / 2 + random.uniform(0, delay / 2)  # noqa: S311 (jitter, not crypto)


def job_timeout(page_count: int, settings: Settings) -> float:
    """More pages, more time (OCR is per page), but never more than the upper limit."""
    seconds = settings.job_timeout_base_seconds + settings.job_timeout_per_page_seconds * max(
        page_count, 1
    )
    return float(min(seconds, settings.job_timeout_max_seconds))


async def requeue_stuck_jobs(repo: Repository, queue: JobQueue, minutes: int) -> int:
    """Jobs left "running" by a crashed worker go back to the queue. Safe because
    processing is idempotent (re-running replaces results instead of adding rows)."""
    stuck = await repo.stuck_jobs(minutes)
    for job in stuck:
        await repo.update_job(
            job.id,
            status=JobStatus.QUEUED,
            stage=None,
            error=f"requeued: stuck in running for over {minutes} min",
        )
    await repo.session.commit()
    for job in stuck:
        await queue.enqueue(job.id)
    if stuck:
        logger.warning("requeued %d stuck job(s)", len(stuck))
    return len(stuck)
