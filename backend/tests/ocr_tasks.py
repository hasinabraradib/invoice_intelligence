"""Stand-in OCR tasks for the parallel OCR tests. They must live in an importable module:
worker processes are started fresh ("spawn") and import the task by name."""

import time


def fast_task(path, kind, number, options):
    # Later pages finish FIRST (shorter sleep), to prove results come back in page order.
    time.sleep(0.05 * (10 - number))
    return {
        "page": number,
        "text": f"text of page {number}",
        "confidence": 90.0,
        "warnings": [],
        "seconds": 0.01,
    }


def slow_task(path, kind, number, options):
    time.sleep(60)  # far longer than the test's timeout
    return {"page": number, "text": "", "confidence": 0.0, "warnings": [], "seconds": 60}


def missing_tesseract_task(path, kind, number, options):
    return {"page": number, "error": "tesseract is not installed", "unavailable": True}
