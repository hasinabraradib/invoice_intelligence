"""Functions run inside ProcessPool workers by the tests (must be importable by path)."""

import os
import time


def add(a: int, b: int) -> int:
    return a + b


def pid() -> int:
    return os.getpid()


def sleep_forever(pid_file: str | None = None) -> None:
    if pid_file:  # lets the test check that exactly this process gets killed
        with open(pid_file, "w") as f:
            f.write(str(os.getpid()))
    while True:  # like the torch kernel that never returned
        time.sleep(1)


def sleep(seconds: float) -> int:
    time.sleep(seconds)
    return os.getpid()


def crash() -> None:
    os._exit(1)  # the process dies mid-call (e.g. killed for memory)


def fail(message: str) -> None:
    raise ValueError(message)
