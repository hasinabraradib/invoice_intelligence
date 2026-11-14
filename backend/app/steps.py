"""The CPU-heavy pipeline steps (parsing, OCR, embedding), each run in a killable worker
process with a hard timeout per call (see app.procpool). LLM calls stay in threads: they
wait on the network and the HTTP client already times them out.

The worker creates one StepPools at startup and closes it at shutdown. Code that calls the
pipeline without one (scripts, most tests) gets a shared default, created on first use.
"""

import functools
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anyio

from app.config import Settings
from app.ocr.parallel import _cores, _init_worker
from app.parsing.dispatch import count_pages, parse_document
from app.parsing.models import ParsedDocument, ParseLimits
from app.procpool import ProcessPool

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- tasks (run in children)


def parse_task(path: str, filename: str, limits: ParseLimits) -> ParsedDocument:
    """Read the text layer / sheets; OCR happens per page in its own step."""
    return parse_document(Path(path), filename, limits, run_ocr=False)


def count_pages_task(path: str) -> int:
    return count_pages(Path(path))


_CHILD_EMBEDDERS: dict[Any, Any] = {}


def embed_task(embedder: Any, texts: list[str]) -> list[list[float]]:
    """Embedders arrive pickled without their model; keep one per process so the model
    loads once per worker process, not once per call."""
    key = getattr(embedder, "cache_key", None)
    if key is not None:
        embedder = _CHILD_EMBEDDERS.setdefault(key, embedder)
    return embedder.embed(texts)


# ---------------------------------------------------------------- pools


class InlinePool:
    """Same interface as ProcessPool, but runs in a thread (no kill on timeout). Only for
    tests that need to patch code in this process; the worker never uses it."""

    name = "inline"

    async def run(
        self,
        fn: Callable[..., Any],
        *args: Any,
        timeout: float,  # noqa: ASYNC109 (mirrors ProcessPool.run; not enforced here)
        **kwargs: Any,
    ) -> Any:
        return await anyio.to_thread.run_sync(functools.partial(fn, *args, **kwargs))

    def close(self) -> None:
        pass


@dataclass
class StepPools:
    parse: Any
    ocr: Any
    embed: Any
    parse_timeout: float = 300
    ocr_timeout: float = 180
    embed_timeout: float = 60
    embed_batch: int = 64
    # The function each step runs; tests swap in a stuck one to check the timeout.
    parse_fn: Callable[..., Any] = field(default=parse_task)

    @classmethod
    def create(cls, settings: Settings) -> "StepPools":
        ocr_size = max(1, settings.ocr_workers)
        return cls(
            # Parsing runs once per job: one process per concurrent job is enough.
            parse=ProcessPool("parse", size=settings.worker_concurrency),
            ocr=ProcessPool(
                "ocr",
                size=ocr_size,
                initializer=_init_worker,
                initargs=(max(1, _cores() // ocr_size),),
                max_calls=100,
            ),
            # One process holds the embedding model (~100 MB); jobs take turns.
            embed=ProcessPool("embed", size=1, max_calls=1000),
            parse_timeout=settings.parse_timeout_seconds,
            ocr_timeout=settings.ocr_task_timeout_seconds,
            embed_timeout=settings.embed_timeout_seconds,
            embed_batch=settings.embed_batch_chunks,
        )

    @classmethod
    def inline(cls, settings: Settings) -> "StepPools":
        pool = InlinePool()
        return cls(parse=pool, ocr=None, embed=pool, parse_timeout=settings.parse_timeout_seconds)

    def close(self) -> None:
        for pool in (self.parse, self.ocr, self.embed):
            if pool is not None:
                pool.close()


_default: StepPools | None = None
_default_lock = threading.Lock()


def default_pools(settings: Settings) -> StepPools:
    """Shared pools for callers without their own (created on first use)."""
    global _default
    with _default_lock:
        if _default is None:
            _default = StepPools.create(settings)
        return _default
