"""A small pool of worker PROCESSES where every call has a hard timeout.

Why: a thread cannot be killed. When CPU-heavy library code never returns (torch's fused
attention once span forever on a long page), a thread-based step hangs the job, keeps any
lock it holds, and a job timeout can only stop *waiting* for it. A process can be killed.

How (the pattern of the parallel-OCR pool, generalised):
- Processes are started with "spawn" (clean interpreter, no forked threads or locks) and
  kept for many calls, so imports and models load once per process, not once per call.
- Each process runs ONE call at a time. The parent waits for the reply on the event loop
  (a reader on the pipe), never in a helper thread, so nothing is left blocked.
- On timeout, crash or cancellation, that one process is terminated and dropped; the next
  call gets a fresh process. Other processes keep working.
- A process is replaced after `max_calls` calls, so slow memory growth cannot build up.

Functions and arguments must be picklable: module-level functions and plain data.
"""

import asyncio
import contextlib
import logging
import multiprocessing
import pickle
import signal
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from multiprocessing.connection import Connection
from typing import Any

logger = logging.getLogger(__name__)


class StepTimeout(Exception):
    """The call did not finish within its timeout; its process was killed."""

    def __init__(self, step: str, seconds: float) -> None:
        super().__init__(f"{step} took longer than {seconds:g} s and was stopped")
        self.step = step
        self.seconds = seconds


class StepCrashed(Exception):
    """The worker process died during the call (e.g. killed for memory)."""


class RemoteError(Exception):
    """An exception from the worker process that could not be sent back as itself."""


def _serve(conn: Connection, initializer: Callable[..., None] | None, initargs: tuple) -> None:
    """Runs IN THE WORKER PROCESS: answer one call at a time until the pipe closes."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # Ctrl-C is for the parent to handle
    if initializer:
        initializer(*initargs)
    while True:
        try:
            message = conn.recv()
        except (EOFError, OSError):
            return
        if message is None:
            return
        fn, args, kwargs = message
        try:
            reply = ("ok", fn(*args, **kwargs))
        except BaseException as exc:  # noqa: BLE001 (everything goes back to the caller)
            try:
                payload = pickle.dumps(exc)
                pickle.loads(payload)  # noqa: S301 (our own data; checks it rebuilds)
                reply = ("error", payload)
            except Exception:
                reply = ("remote", f"{type(exc).__name__}: {exc}"[:500])
        try:
            conn.send(reply)
        except Exception as exc:  # the result itself could not be pickled
            conn.send(("remote", f"result could not be sent back ({type(exc).__name__})"))


@dataclass
class _Worker:
    process: Any
    conn: Connection
    calls: int = 0

    def alive(self) -> bool:
        return self.process.is_alive()

    def kill(self) -> None:
        """Synchronous on purpose: it must finish even inside a cancelled task."""
        with contextlib.suppress(Exception):
            self.conn.close()
        self.process.terminate()
        self.process.join(2)
        if self.process.is_alive():
            self.process.kill()
            self.process.join(2)


@dataclass
class ProcessPool:
    """Up to `size` worker processes; `await pool.run(fn, *args, timeout=...)`."""

    name: str
    size: int
    initializer: Callable[..., None] | None = None
    initargs: tuple = ()
    max_calls: int = 200
    _idle: list[_Worker] = field(default_factory=list)
    _busy: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _closed: bool = False

    def _start(self) -> _Worker:
        ctx = multiprocessing.get_context("spawn")
        parent, child = ctx.Pipe()
        process = ctx.Process(
            target=_serve,
            args=(child, self.initializer, self.initargs),
            name=f"{self.name}-worker",
            daemon=True,  # dies with the parent; may still run subprocesses (tesseract)
        )
        process.start()
        child.close()
        return _Worker(process, parent)

    async def _acquire(self) -> _Worker:
        while True:
            with self._lock:
                if self._closed:
                    raise RuntimeError(f"{self.name} pool is closed")
                while self._idle:
                    worker = self._idle.pop()
                    if worker.alive():
                        self._busy += 1
                        return worker
                    worker.kill()
                if self._busy < self.size:
                    self._busy += 1
                    break
            await asyncio.sleep(0.02)  # all busy: wait for one to come back
        try:
            return self._start()
        except BaseException:
            with self._lock:
                self._busy -= 1
            raise

    def _release(self, worker: _Worker | None) -> None:
        with self._lock:
            self._busy -= 1
            if worker is not None and not self._closed and worker.calls < self.max_calls:
                self._idle.append(worker)
                return
        if worker is not None:
            worker.kill()  # recycled (max_calls) or the pool closed

    async def run(
        self,
        fn: Callable[..., Any],
        *args: Any,
        timeout: float,  # noqa: ASYNC109 (enforced by killing the worker process)
        **kwargs: Any,
    ) -> Any:
        """Call fn(*args, **kwargs) in a worker process. Raises StepTimeout (the process
        was killed), StepCrashed (it died), or the function's own exception."""
        worker = await self._acquire()
        keep: _Worker | None = None
        started = time.monotonic()
        try:
            worker.conn.send((fn, args, kwargs))
            loop = asyncio.get_running_loop()
            ready: asyncio.Future[None] = loop.create_future()
            fd = worker.conn.fileno()
            loop.add_reader(fd, lambda: ready.done() or ready.set_result(None))
            try:
                await asyncio.wait_for(ready, timeout)
            except TimeoutError:
                logger.warning(
                    "%s: %s timed out after %.0f s; killing its process",
                    self.name,
                    getattr(fn, "__name__", "call"),
                    time.monotonic() - started,
                )
                raise StepTimeout(self.name, timeout) from None
            finally:
                loop.remove_reader(fd)
            try:
                status, payload = worker.conn.recv()
            except (EOFError, OSError) as exc:
                raise StepCrashed(f"{self.name} worker process died") from exc
            worker.calls += 1
            keep = worker  # healthy: goes back to the pool
        finally:
            # Timeout, crash, or the job was cancelled while waiting: this process may be
            # in the middle of the work, so it is killed rather than reused.
            if keep is None:
                worker.kill()
            self._release(keep)
        if status == "ok":
            return payload
        if status == "error":
            raise pickle.loads(payload)  # noqa: S301 (only from our own worker process)
        raise RemoteError(payload)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            idle, self._idle = self._idle, []
        for worker in idle:
            with contextlib.suppress(Exception):
                worker.conn.send(None)
            worker.kill()
