"""Run Tesseract on one preprocessed page and measure how confident it is."""

from collections import defaultdict

import numpy as np
import pytesseract
from pydantic import BaseModel, Field

# --psm = page segmentation mode (how Tesseract splits the page into text blocks):
#   6 = "one uniform block of text": best for typical invoice bodies.
#   4 = "one column of text of variable sizes": better for mixed layouts with tables.
PRIMARY_PSM = 6
FALLBACK_PSM = 4
LOW_CONFIDENCE_WARNING = "low OCR confidence"
NO_TEXT_WARNING = "no text found on page"


class OcrError(Exception):
    """OCR could not run on this page (timeout, tesseract missing or crashed)."""


class OcrOptions(BaseModel):
    langs: str = "eng"
    min_confidence: float = 60
    page_timeout_seconds: int = 60
    dpi: int = 300
    # Which photo steps (app.ocr.preprocess.PHOTO_STEPS) may run on images that look like
    # phone photos. Default chosen by measurement on the hard dev set (docs/evaluation.md):
    # flatten helped a lot, CLAHE hurt, and the warp could not be measured there.
    photo_steps: tuple[str, ...] = ("flatten",)


class OcrResult(BaseModel):
    text: str
    mean_confidence: float = Field(ge=0, le=100)  # average over recognised words
    word_count: int
    psm_used: int
    warnings: list[str] = Field(default_factory=list)


def ocr_page(image: np.ndarray, options: OcrOptions | None = None) -> OcrResult:
    """OCR one page. Raises OcrError if Tesseract cannot run; an empty page is not an error.

    PSM 6 runs first. PSM 4 runs only if PSM 6 found nothing or was not confident, so a
    clean page costs one Tesseract call, not two. The more confident result is kept.
    """
    options = options or OcrOptions()
    result = _run(image, PRIMARY_PSM, options)
    if result.word_count == 0 or result.mean_confidence < options.min_confidence:
        fallback = _run(image, FALLBACK_PSM, options)
        if fallback.mean_confidence > result.mean_confidence:
            result = fallback

    if result.word_count == 0:
        result.warnings.append(NO_TEXT_WARNING)
    elif result.mean_confidence < options.min_confidence:
        result.warnings.append(LOW_CONFIDENCE_WARNING)
    return result


def _run(image: np.ndarray, psm: int, options: OcrOptions) -> OcrResult:
    try:
        # One call gives words, their confidences AND their line positions, so we rebuild
        # the text ourselves instead of calling Tesseract a second time.
        data = pytesseract.image_to_data(
            image,
            lang=options.langs,
            config=f"--oem 1 --psm {psm}",  # oem 1 = the LSTM neural-network engine
            output_type=pytesseract.Output.DICT,
            timeout=options.page_timeout_seconds,
        )
    except pytesseract.TesseractNotFoundError as exc:
        raise OcrError("tesseract is not installed") from exc
    except RuntimeError as exc:  # pytesseract signals a timeout with RuntimeError
        raise OcrError("OCR timed out") from exc
    except pytesseract.TesseractError as exc:
        raise OcrError("tesseract failed") from exc

    lines: dict[tuple[int, int, int], list[str]] = defaultdict(list)
    confidences: list[float] = []
    for word, conf, block, par, line in zip(
        data["text"],
        data["conf"],
        data["block_num"],
        data["par_num"],
        data["line_num"],
        strict=True,
    ):
        conf = float(conf)
        # conf == -1 marks layout rows (blocks, lines), not words; skip blanks too.
        if conf < 0 or not word.strip():
            continue
        confidences.append(conf)
        lines[(block, par, line)].append(word.strip())

    text = "\n".join(" ".join(words) for _, words in sorted(lines.items()))
    mean = sum(confidences) / len(confidences) if confidences else 0.0
    return OcrResult(
        text=text, mean_confidence=round(mean, 1), word_count=len(confidences), psm_used=psm
    )
