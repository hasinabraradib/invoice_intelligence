"""Parallel OCR: page order, progress, bounded work, and real cancellation."""

import multiprocessing
import shutil
import time

import anyio
import pytest

from app.ocr.parallel import ocr_document_parallel
from app.ocr.pipeline import ocr_document
from app.parsing.models import FileKind, PageText, ParsedDocument
from tests import factories, ocr_tasks

pytestmark = pytest.mark.anyio


def _document_workers():
    """This document's OCR processes (the shared step pools may legitimately be alive)."""
    return [p for p in multiprocessing.active_children() if p.name == "ocr-document-worker"]


def scanned(pages: int) -> ParsedDocument:
    return ParsedDocument(
        file_kind=FileKind.PDF,
        page_count=pages,
        pages=[PageText(page_number=n, source="none", needs_ocr=True) for n in range(1, pages + 1)],
    )


async def test_results_keep_page_order_and_progress_counts_each_page(tmp_path):
    seen: list[int] = []

    async def progress(done: int) -> None:
        seen.append(done)

    doc = await ocr_document_parallel(
        tmp_path / "x.pdf", scanned(6), workers=3, on_page=progress, task=ocr_tasks.fast_task
    )

    assert [p.text for p in doc.pages] == [f"text of page {n}" for n in range(1, 7)]
    assert all(p.source == "ocr" and not p.needs_ocr for p in doc.pages)
    assert seen == [1, 2, 3, 4, 5, 6]
    assert _document_workers() == []  # workers are gone afterwards


async def test_timeout_really_stops_the_work(tmp_path):
    started = time.perf_counter()
    with pytest.raises(TimeoutError), anyio.fail_after(1.5):
        await ocr_document_parallel(
            tmp_path / "x.pdf", scanned(20), workers=2, task=ocr_tasks.slow_task
        )
    elapsed = time.perf_counter() - started

    assert elapsed < 6  # did not wait for the 60-second pages
    assert _document_workers() == []  # worker processes were terminated


async def test_missing_tesseract_stops_early(tmp_path):
    doc = await ocr_document_parallel(
        tmp_path / "x.pdf", scanned(50), workers=2, task=ocr_tasks.missing_tesseract_task
    )
    assert "OCR is unavailable (tesseract not installed)." in doc.warnings
    assert sum(1 for p in doc.pages if p.error == "OCR stopped") > 0  # rest never submitted
    assert all(p.needs_ocr for p in doc.pages)


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract missing")
async def test_parallel_matches_sequential_ocr(tmp_path):
    frames = [
        factories.invoice_image([f"Invoice No: INV-{n}", f"Total: {n}0.00"]) for n in range(4)
    ]
    path = tmp_path / "scan.tiff"
    frames[0].save(path, format="TIFF", save_all=True, append_images=frames[1:])
    parsed = ParsedDocument(
        file_kind=FileKind.TIFF,
        page_count=4,
        pages=[PageText(page_number=n, source="none", needs_ocr=True) for n in range(1, 5)],
    )

    parallel = await ocr_document_parallel(path, parsed, workers=3)
    sequential = await anyio.to_thread.run_sync(ocr_document, path, parsed)

    assert [p.text for p in parallel.pages] == [p.text for p in sequential.pages]
    assert "INV-2" in parallel.pages[2].text
