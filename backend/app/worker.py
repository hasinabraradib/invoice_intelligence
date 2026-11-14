"""arq worker: runs process_document for queued jobs. Start with:
arq app.worker.WorkerSettings
"""

import asyncio
import logging
import uuid
from typing import Any

import anyio
from arq import Retry, cron
from arq.connections import RedisSettings
from sqlalchemy import select, update

from app.config import get_settings
from app.crosscheck.service import run_cross_check
from app.db import create_engine, create_sessionmaker
from app.extraction.llm import UnconfiguredLLM, get_llm_client
from app.jobs import ArqQueue, job_timeout, recover_stuck_jobs, retry_delay
from app.models import JobStatus, ProcessingJob
from app.pipeline import TEMPORARY_ERRORS, process_document, record_failure
from app.repository import Repository
from app.search.embedder import get_embedder
from app.steps import StepPools, count_pages_task, default_pools
from app.watchdog import Liveness, job_heartbeat

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
    # Parsing, OCR and embedding run in killable processes, kept for the worker's life.
    ctx["pools"] = StepPools.create(settings)
    # Exits the process (Docker restarts it) if the event loop blocks or a job stalls.
    ctx["liveness"] = Liveness(
        loop_stall_seconds=settings.worker_loop_stall_seconds,
        file=settings.worker_liveness_file,
    ).start()
    await watchdog(ctx)  # jobs a crashed worker left behind
    logger.info("worker ready (concurrency=%d)", settings.worker_concurrency)


async def watchdog(ctx: dict[str, Any]) -> None:
    """Every minute (arq cron), in every worker: retry or fail jobs without a heartbeat."""
    async with ctx["sessionmaker"]() as session:
        await recover_stuck_jobs(Repository(session), ctx["queue"], ctx["settings"])


async def shutdown(ctx: dict[str, Any]) -> None:
    if ctx.get("liveness"):
        ctx["liveness"].stop()
    if ctx.get("pools"):
        ctx["pools"].close()
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
        # Plain copies: a rollback inside the pipeline expires `job`, and reloading it later
        # would need database I/O at a point where SQLAlchemy cannot do it (MissingGreenlet).
        job_uuid, attempts_before = job.id, job.attempts
        document = await repo.get_document(job.document_id)
        try:
            llm = ctx["llm_factory"]()
        except ValueError as exc:
            # No LLM configured: ledgers still process; PDFs fail where the LLM is needed.
            llm = UnconfiguredLLM(str(exc))

        pages = await _count_pages(ctx, document.storage_path)
        heartbeat = asyncio.create_task(_heartbeat(ctx, job_uuid))
        try:
            with anyio.fail_after(job_timeout(pages, settings)):
                outcome = await process_document(
                    document.id,
                    session,
                    llm,
                    settings,
                    job_id=job_uuid,
                    embedder=ctx.get("embedder"),
                    pools=ctx.get("pools"),
                )
        except TimeoutError:
            # The run was cancelled mid-way: reset the session, then read attempts afresh.
            await session.rollback()
            attempts = await session.scalar(
                select(ProcessingJob.attempts).where(ProcessingJob.id == job_uuid)
            )
            outcome = await record_failure(
                session,
                document.id,
                job_uuid,
                "job timed out",
                temporary=True,
                attempts=attempts,
                settings=settings,
            )
        except TEMPORARY_ERRORS as exc:  # e.g. the database went away mid-job
            raise Retry(defer=retry_delay(attempts_before + 1, settings)) from exc
        finally:
            heartbeat.cancel()
        await _save_llm_usage(session, job_uuid, llm)

    if outcome.retry:
        raise Retry(defer=retry_delay(outcome.attempts, settings))
    return outcome.status


async def _heartbeat(ctx: dict[str, Any], job_id: uuid.UUID) -> None:
    settings = ctx["settings"]

    def stalled(job: uuid.UUID, seconds: float) -> None:
        # The watchdog (any worker) will retry or fail the job; this worker restarts so
        # whatever it is stuck on is cleared too.
        if ctx.get("liveness"):
            ctx["liveness"].request_recycle(f"job {job} made no progress for {seconds:.0f} s")

    try:
        await job_heartbeat(
            ctx["sessionmaker"],
            job_id,
            every=settings.heartbeat_seconds,
            stuck_seconds=settings.stuck_job_minutes * 60,
            on_stalled=stalled,
        )
    except Exception:  # a heartbeat problem must never break the job itself
        logger.exception("heartbeat for job %s stopped", job_id)


async def _count_pages(ctx: dict[str, Any], path: str) -> int:
    """Only sizes the job timeout, so a file that cannot be counted quickly counts as 1;
    parsing itself has its own hard timeout."""
    pools = ctx.get("pools") or default_pools(ctx["settings"])
    try:
        return await pools.parse.run(count_pages_task, path, timeout=30)
    except Exception:
        return 1


async def _save_llm_usage(session: Any, job_id: uuid.UUID, llm: Any) -> None:
    """Add this attempt's provider usage to the job (summed over retries)."""
    usage = getattr(llm, "usage", None)
    if usage is None or not (usage.calls or usage.vision_calls):
        return
    await session.execute(
        update(ProcessingJob)
        .where(ProcessingJob.id == job_id)
        .values(
            llm_calls=ProcessingJob.llm_calls + usage.calls,
            llm_input_tokens=ProcessingJob.llm_input_tokens + usage.input_tokens,
            llm_output_tokens=ProcessingJob.llm_output_tokens + usage.output_tokens,
            llm_cached_input_tokens=ProcessingJob.llm_cached_input_tokens
            + usage.cached_input_tokens,
            llm_vision_calls=ProcessingJob.llm_vision_calls + usage.vision_calls,
            llm_vision_input_tokens=ProcessingJob.llm_vision_input_tokens
            + usage.vision_input_tokens,
            llm_vision_output_tokens=ProcessingJob.llm_vision_output_tokens
            + usage.vision_output_tokens,
        )
    )
    await session.commit()


async def run_cross_check_job(ctx: dict[str, Any], cross_check_id: str) -> str:
    """Compare two documents (see app.crosscheck). Idempotent: re-running replaces results."""
    async with ctx["sessionmaker"]() as session:
        check = await run_cross_check(session, uuid.UUID(cross_check_id), ctx["settings"])
        return check.status


class WorkerSettings:
    """Read by the `arq` command."""

    _settings = get_settings()
    functions = [process_job, run_cross_check_job]
    cron_jobs = [cron(watchdog, minute=set(range(60)), run_at_startup=False)]  # every minute
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(_settings.redis_url)
    max_jobs = _settings.worker_concurrency  # jobs run at the same time in this worker
    # arq's own timeout is only a safety net; the per-job timeout above is the real one.
    job_timeout = _settings.job_timeout_max_seconds + 60
    # Our attempt counter lives in the database; arq only needs a generous upper bound.
    max_tries = _settings.job_max_attempts + 2
    keep_result = 3600
