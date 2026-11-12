"""Print per-page text, OCR confidence and timing for one file (a developer tool).

The app itself never logs document text (CLAUDE.md rule 5); this script prints it to
YOUR terminal only, so you can judge OCR quality on real invoices.

Usage (inside the backend container, see `make ocr-sample`):
    python scripts/ocr_sample.py /samples/scan.jpg [--langs eng+ben]
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # make `app` importable

from app.ocr.engine import OcrOptions  # noqa: E402
from app.parsing.dispatch import parse_document  # noqa: E402
from app.parsing.errors import DocumentError  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", type=Path)
    parser.add_argument("--langs", default="eng", help='Tesseract languages, e.g. "eng+ben"')
    args = parser.parse_args()

    started = time.perf_counter()
    try:
        doc = parse_document(args.path, args.path.name, ocr_options=OcrOptions(langs=args.langs))
    except DocumentError as exc:
        print(f"ERROR [{exc.code}] {exc.message}")
        return 1
    total = time.perf_counter() - started

    print(f"file: {args.path.name}  kind: {doc.file_kind}  pages: {doc.page_count}")
    for page in doc.pages:
        confidence = f"{page.ocr_confidence:.1f}" if page.ocr_confidence is not None else "-"
        seconds = f"{page.ocr_seconds:.2f}s" if page.ocr_seconds is not None else "-"
        print(
            f"\n=== page {page.page_number} | source={page.source} | confidence={confidence}"
            f" | ocr_time={seconds} | chars={page.char_count}"
        )
        for warning in page.warnings + ([page.error] if page.error else []):
            print(f"  ! {warning}")
        print(page.text)
    for sheet in doc.sheets:
        print(f"\n=== sheet {sheet.name}: {len(sheet.rows)} rows, columns={sheet.columns}")
    for warning in doc.warnings + doc.errors:
        print(f"! {warning}")
    print(f"\ntotal time: {total:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
