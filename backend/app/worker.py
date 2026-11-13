"""arq worker: runs process_document for queued jobs. Start with:
arq app.worker.WorkerSettings
"""

import logging
import uuid
from pathlib import Path
from typing import Any

import anyio
from arq import Retry
from arq.connections import RedisSettings
from sqlalchemy import select

from app.config import get_settings
from app.crosscheck.service import run_cross_check
from app.db import create_engine, create_sessionmaker
from app.extraction.llm import UnconfiguredLLM, get_llm_client
from app.jobs import ArqQueue, job_timeout, requeue_stuck_jobs, retry_delay
from app.models import JobStatus, ProcessingJob
from app.parsing.dispatch import count_pages
from app.pipeline import TEMPORARY_ERRORS, process_document, record_failure
from app.repository import Repository
from app.search.embedder import get_embedder

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("worker")


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    ctx["settings"] = settings
    ctx["engine"] = create_engine(settings.database_url)
    ctx["sessionmaker"] = create_sessionmaker(ctx["engine"])
    ctx["queue"] = ArqQueue(settings.redis_url)
    ctx["llm_factory"] = lambda: get_llm_client(settings)
    ctx["embedder"] = get_embedder(settings)  # model loads on first use, then stays loaded
    async with ctx["sessionmaker"]() as session:
        await requeue_stuck_jobs(Repository(session), ctx["queue"], settings.stuck_job_minutes)
    logger.info("worker ready (concurrency=%d)", settings.worker_concurrency)


async def shutdown(ctx: dict[str, Any]) -> None:
    await ctx["queue"].close()
    await ctx["engine"].dispose()


async def process_job(ctx: dict[str, Any], job_id: str) -> str:
    """Run one job. Raises arq.Retry(defer=...) when a temporary error should be retried."""
    settings = ctx["settings"]
    async with ctx["sessionmaker"]() as session:
        repo = Repository(session)
        job = await repo.get_job(uuid.UUID(job_id))
        if job is not None and job.status == JobStatus.RUNNING and ctx.get("job_try", 1) > 1:
            # arq redelivers a message only when the attempt holding it died (worker
            # restart, crash). The job row still says "running"; take it back now instead
            # of waiting STUCK_JOB_MINUTES. Safe: processing is idempotent.
            await repo.update_job(
                job.id, status=JobStatus.QUEUED, stage=None, error="requeued: worker stopped"
            )
            await session.commit()
            logger.warning("job %s was interrupted; running it again", job.id)
            job = await repo.get_job(job.id)
        if job is None or job.status != JobStatus.QUEUED:
            return "skipped"  # duplicate message, or already handled: nothing to do
        document = await repo.get_document(job.document_id)
        try:
            llm = ctx["llm_factory"]()
        except ValueError as exc:
            # No LLM configured: ledgers still process; PDFs fail where the LLM is needed.
            llm = UnconfiguredLLM(str(exc))

        pages = await anyio.to_thread.run_sync(count_pages, Path(document.storage_path))
        try:
            with anyio.fail_after(job_timeout(pages, settings)):
                outcome = await process_document(
                    document.id, session, llm, settings, job_id=job.id, embedder=ctx.get("embedder")
                )
        except TimeoutError:
            # The run was cancelled mid-way: reset the session, then read attempts afresh.
            await session.rollback()
            attempts = await session.scalar(
                select(ProcessingJob.attempts).where(ProcessingJob.id == job.id)
            )
            outcome = await record_failure(
                session,
                document.id,
                job.id,
                "job timed out",
                temporary=True,
                attempts=attempts,
                settings=settings,
            )
        except TEMPORARY_ERRORS as exc:  # e.g. the database went away mid-job
            raise Retry(defer=retry_delay(job.attempts + 1, settings)) from exc

    if outcome.retry:
        raise Retry(defer=retry_delay(outcome.attempts, settings))
    return outcome.status


async def run_cross_check_job(ctx: dict[str, Any], cross_check_id: str) -> str:
    """Compare two documents (see app.crosscheck). Idempotent: re-running replaces results."""
    async with ctx["sessionmaker"]() as session:
        check = await run_cross_check(session, uuid.UUID(cross_check_id), ctx["settings"])
        return check.status


class WorkerSettings:
    """Read by the `arq` command."""

    _settings = get_settings()
    functions = [process_job, run_cross_check_job]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(_settings.redis_url)
    max_jobs = _settings.worker_concurrency  # jobs run at the same time in this worker
    # arq's own timeout is only a safety net; the per-job timeout above is the real one.
    job_timeout = _settings.job_timeout_max_seconds + 60
    # Our attempt counter lives in the database; arq only needs a generous upper bound.
    max_tries = _settings.job_max_attempts + 2
    keep_result = 3600
