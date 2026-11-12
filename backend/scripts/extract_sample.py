"""Parse + OCR + extract one file with the REAL LLM provider and print a table.

This sends the document's text to the provider set in LLM_PROVIDER (Gemini or Groq), so
only use files you are allowed to share with that provider.

Usage (inside the backend container, see `make extract-sample`):
    python scripts/extract_sample.py /samples/invoice.pdf
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # make `app` importable

from app.config import get_settings  # noqa: E402
from app.extraction.llm import get_llm_client  # noqa: E402
from app.extraction.service import FieldResult, extract_invoice  # noqa: E402
from app.ocr.engine import OcrOptions  # noqa: E402
from app.parsing.dispatch import parse_document  # noqa: E402
from app.parsing.errors import DocumentError  # noqa: E402


def _short(text: object, width: int) -> str:
    text = " ".join(str(text).split()) if text is not None else "-"
    return text if len(text) <= width else text[: width - 1] + "…"


def _row(name: str, field: FieldResult) -> str:
    return (
        f"{name:<16} {_short(field.value, 22):<22} {field.status:<9} "
        f"{field.confidence:>4.2f}  p{field.page or '-':<3} {_short(field.evidence, 38):<38} "
        f"{_short('; '.join(field.reasons), 40)}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    settings = get_settings()

    try:
        llm = get_llm_client(settings)
    except ValueError as exc:
        print(f"Configure the LLM in .env first: {exc}")
        return 2
    try:
        doc = parse_document(
            args.path,
            args.path.name,
            ocr_options=OcrOptions(
                langs=settings.ocr_langs, min_confidence=settings.ocr_min_confidence
            ),
        )
    except DocumentError as exc:
        print(f"ERROR [{exc.code}] {exc.message}")
        return 1

    result = extract_invoice(doc, llm, chunk_tokens=settings.llm_chunk_tokens)
    print(
        f"file: {args.path.name}  kind: {doc.file_kind}  pages: {doc.page_count}  "
        f"provider: {result.provider}  chunks: {result.chunk_count}  "
        f"calls: {result.llm_calls}  time: {result.duration_seconds}s"
    )
    if not result.ok:
        print(f"FAILED: {result.failure_reason}")
        return 1

    print(f"\n{'field':<16} {'value':<22} {'status':<9} conf  page {'evidence':<38} reasons")
    print("-" * 140)
    for name, field in result.fields.items():
        print(_row(name, field))
    for number, item in enumerate(result.line_items, start=1):
        print(f"\nline item {number}")
        for name in ("description", "quantity", "unit_price", "line_total"):
            print(_row(f"  {name}", getattr(item, name)))
    for warning in doc.warnings + result.warnings:
        print(f"! {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
