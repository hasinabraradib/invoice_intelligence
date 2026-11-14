"""Keeping the worker alive and honest.

Three layers, from the job outwards:

1. Job heartbeat (`job_heartbeat`): while a job runs, it writes processing_jobs.heartbeat_at
   every HEARTBEAT_SECONDS, but only while the job is making progress (its stage, pages
   done or invoices done changed within STUCK_JOB_MINUTES). A job stuck on anything stops
   heartbeating even though its worker is alive.
2. The watchdog (`app.jobs.recover_stuck_jobs`, an arq cron job every minute in every
   worker, and at startup): running jobs with no heartbeat for STUCK_JOB_MINUTES are
   retried, or failed once their attempts are used up.
3. Worker liveness (`Liveness`): the event loop ticks every few seconds and touches a file
   (the Docker healthcheck reads its age). A monitor THREAD, which still runs when the
   loop is blocked, exits the process if the loop stops ticking for WORKER_LOOP_STALL_SECONDS
   or if a job of this worker stalled; Docker's restart policy starts a fresh worker, and
   the watchdog recovers its jobs.
"""

import asyncio
import contextlib
import logging
import os
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, update

from app.models import JobStatus, ProcessingJob

logger = logging.getLogger(__name__)

EXIT_CODE = 70  # "internal software error": Docker restarts the worker container


@dataclass
class ProgressWatch:
    """Is the job still moving? True until its progress is unchanged for `stuck_seconds`."""

    stuck_seconds: float
    last: Any = None
    changed_at: float = field(default_factory=time.monotonic)

    def observe(self, progress: Any, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        if progress != self.last:
            self.last, self.changed_at = progress, now
        return now - self.changed_at < self.stuck_seconds


async def job_heartbeat(
    sessionmaker: Any,
    job_id: uuid.UUID,
    *,
    every: float,
    stuck_seconds: float,
    on_stalled: Callable[[uuid.UUID, float], None],
) -> None:
    """Runs next to a job (cancelled when it ends). Writes heartbeat_at while the job
    progresses; when it stops progressing, stops writing and calls on_stalled."""
    watch = ProgressWatch(stuck_seconds)
    while True:
        async with sessionmaker() as session:
            row = (
                await session.execute(
                    select(
                        ProcessingJob.status,
                        ProcessingJob.stage,
                        ProcessingJob.pages_done,
                        ProcessingJob.segments_done,
                    ).where(ProcessingJob.id == job_id)
                )
            ).one_or_none()
            if row is None or row.status != JobStatus.RUNNING:
                return
            if not watch.observe((row.stage, row.pages_done, row.segments_done)):
                stalled_for = time.monotonic() - watch.changed_at
                logger.error(
                    "job %s made no progress for %.0f s (stage %s); no more heartbeats",
                    job_id,
                    stalled_for,
                    row.stage,
                )
                on_stalled(job_id, stalled_for)
                return
            await session.execute(
                update(ProcessingJob)
                .where(ProcessingJob.id == job_id)
                .values(heartbeat_at=datetime.now(UTC))
            )
            await session.commit()
        await asyncio.sleep(every)


class Liveness:
    """Proves the worker's event loop is alive; exits the process when it is not."""

    def __init__(
        self,
        *,
        loop_stall_seconds: float,
        file: str | None = None,
        tick_seconds: float = 5,
        exit_fn: Callable[[int], None] = os._exit,
    ) -> None:
        self.loop_stall_seconds = loop_stall_seconds
        self.file = Path(file) if file else None
        self.tick_seconds = tick_seconds
        self._exit = exit_fn
        self.last_tick = time.monotonic()
        self.recycle_reason: str | None = None
        self._stop = threading.Event()
        self._task: asyncio.Task | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> "Liveness":
        self._task = asyncio.get_running_loop().create_task(self._tick())
        self._thread = threading.Thread(target=self._monitor, name="liveness", daemon=True)
        self._thread.start()
        return self

    def request_recycle(self, reason: str) -> None:
        """Called when a job of this worker stalled: restart the whole worker."""
        self.recycle_reason = reason

    async def _tick(self) -> None:
        while True:
            self.last_tick = time.monotonic()
            if self.file is not None:
                with contextlib.suppress(OSError):
                    self.file.touch()
            await asyncio.sleep(self.tick_seconds)

    def _monitor(self) -> None:
        while not self._stop.wait(min(1.0, self.tick_seconds)):
            blocked = time.monotonic() - self.last_tick
            if blocked > self.loop_stall_seconds:
                reason = f"event loop blocked for {blocked:.0f} s"
            elif self.recycle_reason:
                reason = self.recycle_reason
            else:
                continue
            logger.critical("worker recycling itself: %s", reason)
            for handler in logging.getLogger().handlers:
                with contextlib.suppress(Exception):
                    handler.flush()
            self._exit(EXIT_CODE)
            return

    def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
