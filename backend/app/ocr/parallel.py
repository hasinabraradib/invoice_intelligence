"""OCR many pages at once, one page per worker PROCESS.

Why processes: Tesseract and OpenCV are CPU-bound; separate processes use every core, and a
process can be killed, so a job timeout really stops the work (a thread cannot be killed).

- Each worker renders AND OCRs its own page, so page images never travel between
  processes; only the short text result comes back.
- Memory stays bounded: at most 2 x workers pages are in flight at a time.
- Results are applied in page order, whatever order they finish in.
- On cancellation (job timeout) or error the pool is terminated: pending pages are
  dropped and the worker processes are killed.
"""

import asyncio
import logging
import multiprocessing
import os
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from app.ocr.engine import LOW_CONFIDENCE_WARNING, OcrError, OcrOptions, ocr_page
from app.ocr.pipeline import _image_frames, _pdf_pages
from app.ocr.preprocess import preprocess
from app.parsing.models import FileKind, PageText, ParsedDocument

logger = logging.getLogger(__name__)

PageTask = Callable[[str, str, int, dict[str, Any]], dict[str, Any]]


def _init_worker(opencv_threads: int) -> None:
    """Runs once in each new worker process. Splits the cores between workers: OpenCV
    would otherwise spread every page over ALL cores, and N workers x all cores only fight
    over the CPU (measured: no speed-up at all)."""
    import cv2

    cv2.setNumThreads(opencv_threads)


def _cores() -> int:
    try:
        return len(os.sched_getaffinity(0))
    except AttributeError:
        return os.cpu_count() or 1


def ocr_page_task(path: str, kind: str, number: int, options: dict[str, Any]) -> dict[str, Any]:
    """Runs IN A WORKER PROCESS: render page `number`, clean it up, OCR it.
    Returns plain data (cheap to send back); never raises."""
    started = time.perf_counter()
    opts = OcrOptions(**options)
    file_kind = FileKind(kind)
    pages = (
        _pdf_pages(Path(path), [number], opts.dpi)
        if file_kind is FileKind.PDF
        else _image_frames(Path(path), [number])
    )
    try:
        _, image, dpi, error = next(iter(pages))
        if image is None:
            return {"page": number, "error": error or "render failed", "render_failed": True}
        result = ocr_page(preprocess(image, dpi), opts)
    except OcrError as exc:
        return {"page": number, "error": str(exc), "unavailable": "not installed" in str(exc)}
    except Exception as exc:  # one bad page must not stop the document
        return {"page": number, "error": type(exc).__name__}
    finally:
        pages.close()  # closes the PDF in this worker
    return {
        "page": number,
        "text": result.text,
        "confidence": result.mean_confidence,
        "warnings": result.warnings,
        "seconds": round(time.perf_counter() - started, 2),
    }


async def ocr_document_parallel(
    path: Path,
    parsed: ParsedDocument,
    options: OcrOptions | None = None,
    *,
    workers: int,
    on_page: Callable[[int], Awaitable[None]] | None = None,
    task: PageTask = ocr_page_task,
) -> ParsedDocument:
    """Like ocr_document, but pages run in parallel processes. on_page(n) is awaited after
    each finished page with how many are done."""
    options = options or OcrOptions()
    doc = parsed.model_copy(deep=True)  # never mutate the caller's object
    targets = {page.page_number: page for page in doc.pages if page.needs_ocr}
    if not targets:
        return doc

    loop = asyncio.get_running_loop()
    # "spawn": start clean processes instead of forking one that already runs threads.
    # maxtasksperchild: replace workers now and then so memory cannot creep up.
    processes = min(workers, len(targets))
    pool = multiprocessing.get_context("spawn").Pool(
        processes=processes,
        initializer=_init_worker,
        initargs=(max(1, _cores() // processes),),
        maxtasksperchild=100,
    )
    in_flight = asyncio.Semaphore(2 * workers)  # bounds memory: pages queued + running
    progress_lock = asyncio.Lock()  # on_page touches the database: one at a time
    stop = asyncio.Event()  # set when tesseract is missing: no point in continuing
    results: dict[int, dict[str, Any]] = {}
    done = 0
    args = (str(path), doc.file_kind.value)

    async def run(number: int) -> None:
        nonlocal done
        async with in_flight:
            if stop.is_set():
                return
            future: asyncio.Future[dict[str, Any]] = loop.create_future()

            def finish(value: dict[str, Any]) -> None:  # runs in the pool's result thread
                loop.call_soon_threadsafe(lambda: future.done() or future.set_result(value))

            pool.apply_async(
                task,
                (*args, number, options.model_dump()),
                callback=finish,
                error_callback=lambda exc: finish({"page": number, "error": type(exc).__name__}),
            )
            results[number] = await future
        if results[number].get("unavailable"):
            stop.set()
        async with progress_lock:
            done += 1
            if on_page:
                await on_page(done)

    try:
        await asyncio.gather(*(run(n) for n in sorted(targets)))
    finally:
        # Normal end, error or timeout: kill the workers. Synchronous on purpose: awaiting
        # inside a cancelled scope would be interrupted before the processes are gone.
        pool.terminate()
        pool.join()

    ok = 0
    for number in sorted(targets):  # apply in page order
        result = results.get(number)
        page = targets[number]
        if result is None:
            page.error = "OCR stopped"
        elif "error" in result:
            page.error = result["error"]
            doc.errors.append(f"Page {number}: OCR failed ({result['error']}).")
        else:
            _apply(page, result)
            ok += 1
    if stop.is_set():
        doc.warnings.append("OCR is unavailable (tesseract not installed).")
    low = sum(1 for p in targets.values() if LOW_CONFIDENCE_WARNING in p.warnings)
    if ok:
        doc.warnings.append(f"OCR applied to {ok} page(s).")
    if low:
        doc.warnings.append(f"{low} page(s) have low OCR confidence; please review.")
    logger.info("parallel OCR finished: pages=%d ok=%d workers=%d", len(targets), ok, workers)
    return doc


def _apply(page: PageText, result: dict[str, Any]) -> None:
    if page.error:  # the text layer was unreadable but OCR worked: keep a note of it
        page.warnings.append(f"Text layer unreadable ({page.error}); used OCR instead.")
        page.error = None
    page.text = result["text"]
    page.char_count = sum(1 for ch in result["text"] if not ch.isspace())
    page.source = "ocr"
    page.ocr_confidence = result["confidence"]
    page.ocr_seconds = result["seconds"]
    page.needs_ocr = False
    page.warnings.extend(result["warnings"])
