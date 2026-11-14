"""ProcessPool: hard per-call timeouts; a stuck or dead process is replaced."""

import asyncio
import os
import time

import pytest

from app.procpool import ProcessPool, StepCrashed, StepTimeout
from tests import procpool_tasks as tasks

pytestmark = pytest.mark.anyio


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A killed child may linger as a zombie until reaped; treat zombies as dead.
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().split()[2] != "Z"
    except FileNotFoundError:
        return False


@pytest.fixture
def pool():
    p = ProcessPool("test", size=2)
    yield p
    p.close()


async def test_runs_calls_and_reuses_the_process(pool):
    assert await pool.run(tasks.add, 2, 3, timeout=30) == 5
    first = await pool.run(tasks.pid, timeout=30)
    assert first != os.getpid() and await pool.run(tasks.pid, timeout=30) == first


async def test_a_call_that_never_returns_is_killed_and_the_next_call_runs(pool, tmp_path):
    """Regression for the embedding deadlock: the stuck process is killed at its timeout,
    no thread or lock is left behind, and the next call gets a fresh process."""
    pid_file = tmp_path / "stuck.pid"
    started = time.monotonic()
    with pytest.raises(StepTimeout, match="longer than 1 s"):
        await pool.run(tasks.sleep_forever, str(pid_file), timeout=1)
    assert time.monotonic() - started < 5
    stuck = int(pid_file.read_text())
    assert not _alive(stuck)  # killed before run() returned
    assert await pool.run(tasks.add, 1, 1, timeout=30) == 2
    assert await pool.run(tasks.pid, timeout=30) != stuck


async def test_a_crashed_process_is_reported_and_replaced(pool):
    with pytest.raises(StepCrashed):
        await pool.run(tasks.crash, timeout=30)
    assert await pool.run(tasks.add, 4, 5, timeout=30) == 9


async def test_exceptions_keep_their_type_and_the_process_is_reused(pool):
    first = await pool.run(tasks.pid, timeout=30)
    with pytest.raises(ValueError, match="bad page"):
        await pool.run(tasks.fail, "bad page", timeout=30)
    assert await pool.run(tasks.pid, timeout=30) == first


async def test_processes_are_recycled_after_max_calls():
    pool = ProcessPool("recycle", size=1, max_calls=2)
    try:
        pids = [await pool.run(tasks.pid, timeout=30) for _ in range(3)]
        assert pids[0] == pids[1] != pids[2]
    finally:
        pool.close()


async def test_cancelling_the_caller_kills_the_process(pool):
    first = await pool.run(tasks.pid, timeout=30)
    with pytest.raises(TimeoutError):  # e.g. the whole job timed out
        await asyncio.wait_for(pool.run(tasks.sleep_forever, timeout=60), 1)
    for _ in range(50):
        if not _alive(first):
            break
        await asyncio.sleep(0.1)
    assert not _alive(first)
    assert await pool.run(tasks.add, 2, 2, timeout=30) == 4


async def test_calls_run_in_parallel_up_to_size(pool):
    await asyncio.gather(*(pool.run(tasks.pid, timeout=30) for _ in range(2)))  # warm up
    started = time.monotonic()
    pids = await asyncio.gather(*(pool.run(tasks.sleep, 1, timeout=30) for _ in range(2)))
    assert time.monotonic() - started < 1.8 and len(set(pids)) == 2
