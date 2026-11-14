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
from typing import Any

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


def dev_labels_with_ocr() -> list[dict]:
    """The hard dev set plus the clean dev documents that go through OCR (the only kind
    the second opinion can touch). Never test files."""
    labels = [json.loads(p.read_text()) for p in sorted((DATASET / "labels").glob("*.json"))]
    chosen = [
        x
        for x in labels
        if x["split"] == "dev"
        and (x.get("subset") == "hard" or x["file_type"] in ("image_scan", "pdf_scanned"))
    ]
    if any(x["split"] != "dev" for x in chosen):
        raise SystemExit("dev only")
    return chosen


def run_second_opinion(labels: list[dict], llm, settings) -> dict:
    """Current system (vision only below OCR_MIN_CONFIDENCE) vs vision as a second opinion
    with the wider triggers: review-flag recall, new flags on correct invoices, cost."""
    from app.extraction.second_opinion import compare_readings, vision_triggers
    from app.extraction.vision import extract_from_images
    from app.steps import render_page_task
    from app.validation.rules import validate_invoice

    options = OcrOptions(langs=settings.ocr_langs, min_confidence=settings.ocr_min_confidence)

    def review(result, extra=()) -> bool:
        report = validate_invoice(
            result, tolerance=settings.money_tolerance, review_confidence=settings.review_confidence
        )
        return report.needs_review or bool(extra)

    def errors(label, result) -> tuple[bool, Any]:
        score = scoring.score_invoice(label, _invoice_dict(result) if result.ok else None)
        bad = any(f.outcome in ("wrong", "missing") for f in score.fields)
        return bad or bool(score.line_fn or score.line_fp), score

    def one(label: dict) -> dict:
        path = DATASET / "files" / label["file"]
        parsed = parse_document(path, label["file"], run_ocr=False)
        if parsed.pages_needing_ocr:
            parsed = ocr_document(path, parsed, options)
        pages = source_pages(parsed)
        text = extract_from_pages(pages, llm)
        before_review = review(text) if text.ok else True
        has_errors, score = errors(label, text)
        triggers = vision_triggers(pages, text, settings)
        after_review, disagreements = before_review, []
        if triggers and text.ok:
            numbers = sorted({p.number for p in pages})
            images = [
                (n, render_page_task(str(path), n, settings.vision_image_max_side)) for n in numbers
            ]
            vision, _ = extract_from_images(images, llm)
            if vision.ok:
                merged = text.model_copy(deep=True)
                issues = compare_readings(merged, vision)
                disagreements = [i.field for i in issues]
                after_review = review(merged, issues)
        return {
            "id": label["id"],
            "kind": label.get("hard_kind") or label["file_type"],
            "subset": label.get("subset", "clean"),
            "triggers": triggers,
            "errors": has_errors,
            "planted": bool(label.get("expected_flags")),
            "review_before": before_review,
            "review_after": after_review,
            "disagreements": disagreements,
            "field_accuracy": scoring.field_metrics([score])["overall"]["accuracy"],
        }

    before = llm.usage.as_dict()
    with concurrent.futures.ThreadPoolExecutor(4) as pool:
        rows = list(pool.map(one, labels))
    after = llm.usage.as_dict()
    err = [r for r in rows if r["errors"]]
    correct = [r for r in rows if not r["errors"] and not r["planted"]]
    return {
        "invoices": len(rows),
        "triggered": sum(bool(r["triggers"]) for r in rows),
        "errors_present": len(err),
        "flag_recall_before": sum(r["review_before"] for r in err) / len(err) if err else None,
        "flag_recall_after": sum(r["review_after"] for r in err) / len(err) if err else None,
        "correct_invoices": len(correct),
        "new_flags_on_correct": sum(r["review_after"] and not r["review_before"] for r in correct),
        "vision_calls": after["vision_calls"] - before["vision_calls"],
        "vision_input_tokens": after["vision_input_tokens"] - before["vision_input_tokens"],
        "vision_output_tokens": after["vision_output_tokens"] - before["vision_output_tokens"],
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--configs", default=",".join(CONFIGS))
    parser.add_argument("--out", type=Path)
    parser.add_argument("--second-opinion", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    llm = get_llm_client(settings)
    if args.second_opinion:
        result = run_second_opinion(dev_labels_with_ocr(), llm, settings)
        for row in result["rows"]:
            print(
                f"{row['id']:<12} {row['kind']:<16} errors={row['errors']!s:<5} "
                f"review {row['review_before']!s:<5} -> {row['review_after']!s:<5} "
                f"triggers={row['triggers']} disagree={row['disagreements']}"
            )
        summary = {k: v for k, v in result.items() if k != "rows"}
        print(json.dumps(summary, indent=2))
        if args.out:
            args.out.write_text(json.dumps(result, indent=2, default=str))
        return 0
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
