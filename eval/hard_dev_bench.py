"""Measure OCR preprocessing choices on the HARD DEV set only (see `make hard-dev-bench`).

For each configuration of photo steps, OCR every hard-dev page, record mean OCR confidence
and the photo/scan decision, extract fields with the real LLM, and score them against the
labels. Refuses to touch anything that is not split "dev" and subset "hard": the hard test
files stay held out.

    python /eval/hard_dev_bench.py [--configs base,warp,flatten,...] [--out results.json]
"""

import argparse
import concurrent.futures
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, "/app")  # the backend image: `app` is importable

from app.config import get_settings  # noqa: E402
from app.evaluation import scoring  # noqa: E402
from app.extraction.llm import get_llm_client  # noqa: E402
from app.extraction.prompt import source_pages  # noqa: E402
from app.extraction.service import extract_from_pages  # noqa: E402
from app.ocr.engine import OcrOptions  # noqa: E402
from app.ocr.pipeline import _image_frames, _pdf_pages, ocr_document  # noqa: E402
from app.ocr.preprocess import check_photo, to_grayscale  # noqa: E402
from app.parsing.dispatch import parse_document  # noqa: E402

DATASET = Path(__file__).resolve().parent / "dataset"
CONFIGS = {
    "base": (),
    "warp": ("warp",),
    "flatten": ("flatten",),
    "clahe": ("clahe",),
    "warp+flatten": ("warp", "flatten"),
    "warp+flatten+clahe": ("warp", "flatten", "clahe"),
    "flatten+clahe": ("flatten", "clahe"),
}


def hard_dev_labels() -> list[dict]:
    labels = [json.loads(p.read_text()) for p in sorted((DATASET / "labels").glob("*.json"))]
    chosen = [x for x in labels if x.get("subset") == "hard" and x["split"] == "dev"]
    if not chosen or not all(x["id"].startswith("harddev-") for x in chosen):
        raise SystemExit("expected the hard dev set (harddev-*) only; run make eval-dataset")
    return chosen


def _invoice_dict(result) -> dict:
    """ExtractionResult -> the API-like shape the scorer reads."""

    def field(f):
        return {"value": None if f is None else f.value, "evidence": f and f.evidence}

    return {
        "fields": {name: field(result.fields.get(name)) for name in scoring.FIELDS},
        "line_items": [
            {"description": field(i.description), "line_total": field(i.line_total)}
            for i in result.line_items
        ],
        "issues": [],
    }


def run_config(name: str, steps: tuple, labels: list[dict], llm, settings) -> dict:
    options = OcrOptions(
        langs=settings.ocr_langs, min_confidence=settings.ocr_min_confidence, photo_steps=steps
    )
    rows = []

    def one(label: dict) -> dict:
        path = DATASET / "files" / label["file"]
        parsed = parse_document(path, label["file"], run_ocr=False)
        photo = None
        if parsed.pages_needing_ocr:
            frames = (
                _pdf_pages(path, [1], 200) if path.suffix == ".pdf" else _image_frames(path, [1])
            )
            _, image, _, _ = next(iter(frames))
            photo = check_photo(to_grayscale(image)).is_photo if image is not None else None
            frames.close()
            parsed = ocr_document(path, parsed, options)
        confidences = [p.ocr_confidence for p in parsed.pages if p.ocr_confidence is not None]
        result = extract_from_pages(source_pages(parsed), llm)
        score = scoring.score_invoice(label, _invoice_dict(result) if result.ok else None)
        fields = scoring.field_metrics([score])["overall"]
        return {
            "id": label["id"],
            "kind": label["hard_kind"],
            "photo": photo,
            "ocr_confidence": round(statistics.mean(confidences), 1) if confidences else None,
            "field_accuracy": fields["accuracy"],
            "line_f1": scoring.line_metrics([score])["f1"],
        }

    started = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(4) as pool:
        rows = list(pool.map(one, labels))
    ocr_rows = [r for r in rows if r["ocr_confidence"] is not None]
    scores = [r["field_accuracy"] for r in rows if r["field_accuracy"] is not None]
    return {
        "config": name,
        "steps": list(steps),
        "mean_ocr_confidence": round(statistics.mean(r["ocr_confidence"] for r in ocr_rows), 1),
        "field_accuracy": round(statistics.mean(scores), 4),
        "seconds": round(time.monotonic() - started, 1),
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--configs", default=",".join(CONFIGS))
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    settings = get_settings()
    llm = get_llm_client(settings)
    labels = hard_dev_labels()
    results = []
    for name in args.configs.split(","):
        result = run_config(name, CONFIGS[name], labels, llm, settings)
        results.append(result)
        print(
            f"{name:<20} OCR conf {result['mean_ocr_confidence']:>5}  "
            f"field acc {result['field_accuracy']:.3f}  ({result['seconds']} s)",
            flush=True,
        )
    print("\nper invoice (OCR confidence / field accuracy):")
    for i, label in enumerate(labels):
        cells = "  ".join(
            f"{r['rows'][i]['ocr_confidence'] or '-':>5}/{r['rows'][i]['field_accuracy']:.2f}"
            for r in results
        )
        print(
            f"{label['id']:<12} {label['hard_kind']:<16} "
            f"photo={results[0]['rows'][i]['photo']!s:<5} {cells}"
        )
    print("usage:", json.dumps(llm.usage.as_dict()))
    if args.out:
        args.out.write_text(json.dumps(results, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
