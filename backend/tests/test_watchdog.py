"""Watchdog: heartbeats, recovering stuck jobs, and the worker recycling itself."""

import asyncio
import os
import time
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.models as m
from app.jobs import recover_stuck_jobs
from app.repository import Repository
from app.watchdog import EXIT_CODE, Liveness, ProgressWatch, job_heartbeat

pytestmark = pytest.mark.anyio


async def _job(repo, *, started_minutes_ago, heartbeat_minutes_ago=None, attempts=1):
    doc = await repo.create_document(
        filename="a.pdf",
        sha256=os.urandom(32).hex(),
        mime_type="application/pdf",
        size_bytes=1,
        storage_path="/x",
    )
    job = await repo.create_job(doc.id)
    now = datetime.now(UTC)
    await repo.update_job(
        job.id,
        status=m.JobStatus.RUNNING,
        stage="extracting",
        attempts=attempts,
        started_at=now - timedelta(minutes=started_minutes_ago),
        heartbeat_at=(
            None
            if heartbeat_minutes_ago is None
            else now - timedelta(minutes=heartbeat_minutes_ago)
        ),
    )
    await repo.session.commit()
    return doc, job


async def _reload(session, model, id_):
    return await session.get(model, id_, populate_existing=True)


async def test_a_job_without_heartbeat_is_retried_and_a_live_one_is_left_alone(
    db_session, queue, settings
):
    repo = Repository(db_session)
    _, stuck = await _job(repo, started_minutes_ago=50, heartbeat_minutes_ago=45)
    _, never_beat = await _job(repo, started_minutes_ago=45)  # crashed before a heartbeat
    _, long_but_alive = await _job(repo, started_minutes_ago=90, heartbeat_minutes_ago=1)
    _, fresh = await _job(repo, started_minutes_ago=2)

    assert await recover_stuck_jobs(repo, queue, settings) == (2, 0)  # STUCK_JOB_MINUTES=30

    assert sorted(queue.job_ids) == sorted([stuck.id, never_beat.id])
    for job_id in (stuck.id, never_beat.id):
        job = await _reload(db_session, m.ProcessingJob, job_id)
        assert job.status == m.JobStatus.QUEUED and "no heartbeat for over 30 min" in job.error
    for job_id in (long_but_alive.id, fresh.id):  # a long job that still beats is fine
        assert (await _reload(db_session, m.ProcessingJob, job_id)).status == m.JobStatus.RUNNING
    assert await recover_stuck_jobs(repo, queue, settings) == (0, 0)  # claimed only once


async def test_a_stuck_job_out_of_attempts_is_failed(db_session, queue, settings):
    repo = Repository(db_session)
    doc, job = await _job(
        repo, started_minutes_ago=60, heartbeat_minutes_ago=40, attempts=settings.job_max_attempts
    )

    assert await recover_stuck_jobs(repo, queue, settings) == (0, 1)

    assert queue.job_ids == []
    job = await _reload(db_session, m.ProcessingJob, job.id)
    document = await _reload(db_session, m.Document, doc.id)
    assert job.status == m.JobStatus.FAILED and job.finished_at is not None
    assert document.status == m.DocumentStatus.FAILED
    assert document.error.startswith("stopped: no progress for over 30 min")


async def test_the_cron_watchdog_picks_up_a_stuck_job(db_session, queue, settings, database_url):
    from app.worker import watchdog

    _, stuck = await _job(Repository(db_session), started_minutes_ago=40)
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        ctx = {
            "settings": settings,
            "queue": queue,
            "sessionmaker": async_sessionmaker(engine, expire_on_commit=False),
        }
        await watchdog(ctx)
    finally:
        await engine.dispose()
    assert queue.job_ids == [stuck.id]


def test_progress_watch_allows_slow_progress_but_not_none():
    watch = ProgressWatch(stuck_seconds=60)
    assert watch.observe(("ocr", 1, 0), now=0)
    assert watch.observe(("ocr", 1, 0), now=59)  # same progress, not yet too long
    assert watch.observe(("ocr", 2, 0), now=100)  # moved on: the clock restarts
    assert watch.observe(("ocr", 2, 0), now=159)
    assert not watch.observe(("ocr", 2, 0), now=161)  # 61 s without progress


async def test_heartbeat_is_written_while_the_job_moves_and_stops_when_it_stalls(
    db_session, database_url
):
    repo = Repository(db_session)
    _, job = await _job(repo, started_minutes_ago=0)
    engine = create_async_engine(database_url, poolclass=NullPool)
    stalls = []
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        beat = asyncio.create_task(
            job_heartbeat(
                sessions,
                job.id,
                every=0.05,
                stuck_seconds=0.5,
                on_stalled=lambda job_id, seconds: stalls.append(job_id),
            )
        )
        await asyncio.sleep(0.2)
        first = (await _reload(db_session, m.ProcessingJob, job.id)).heartbeat_at
        assert first is not None and not stalls

        await asyncio.wait_for(beat, 5)  # progress never changes: it gives up
        assert stalls == [job.id]
        last = (await _reload(db_session, m.ProcessingJob, job.id)).heartbeat_at
        assert last >= first
        await asyncio.sleep(0.2)
        assert (await _reload(db_session, m.ProcessingJob, job.id)).heartbeat_at == last
    finally:
        await engine.dispose()


async def test_heartbeat_ends_quietly_when_the_job_is_no_longer_running(db_session, database_url):
    repo = Repository(db_session)
    _, job = await _job(repo, started_minutes_ago=0)
    await repo.update_job(job.id, status=m.JobStatus.SUCCEEDED)
    await db_session.commit()
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        await asyncio.wait_for(
            job_heartbeat(sessions, job.id, every=0.05, stuck_seconds=60, on_stalled=print), 5
        )
    finally:
        await engine.dispose()


async def test_worker_exits_when_its_event_loop_is_blocked(tmp_path):
    exits = []
    liveness = Liveness(
        loop_stall_seconds=0.5, file=str(tmp_path / "alive"), tick_seconds=0.1, exit_fn=exits.append
    ).start()
    try:
        await asyncio.sleep(0.3)
        assert exits == [] and (tmp_path / "alive").exists()
        time.sleep(1.5)  # noqa: ASYNC251 (blocking the event loop is the point)
        assert exits == [EXIT_CODE]
    finally:
        liveness.stop()


async def test_worker_exits_when_one_of_its_jobs_stalled():
    exits = []
    liveness = Liveness(loop_stall_seconds=60, tick_seconds=0.1, exit_fn=exits.append).start()
    try:
        liveness.request_recycle("job x made no progress for 1800 s")
        for _ in range(30):
            if exits:
                break
            await asyncio.sleep(0.1)
        assert exits == [EXIT_CODE]
    finally:
        liveness.stop()


async def test_liveness_file_stays_fresh_while_healthy(tmp_path):
    alive = tmp_path / "alive"
    liveness = Liveness(
        loop_stall_seconds=60, file=str(alive), tick_seconds=0.1, exit_fn=lambda code: None
    ).start()
    try:
        await asyncio.sleep(0.15)
        first = alive.stat().st_mtime
        await asyncio.sleep(0.3)
        assert alive.stat().st_mtime > first
    finally:
        liveness.stop()
