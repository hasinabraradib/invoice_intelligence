"""OCR every page of a ParsedDocument that has no usable text, one page at a time.

Memory: only one page image exists at any moment. A 300-DPI A4 page is ~8.7 million
pixels (~9 MB in grayscale), so a 1000-page scan stays at one page's worth of memory.
Pages that already have good text (from the PDF text layer) are never OCR'd.
"""

import logging
import math
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import numpy as np
import pypdfium2 as pdfium
from PIL import Image, ImageOps

from app.ocr.engine import LOW_CONFIDENCE_WARNING, OcrError, OcrOptions, ocr_page
from app.ocr.preprocess import preprocess
from app.parsing.models import FileKind, PageText, ParsedDocument

logger = logging.getLogger(__name__)

MAX_RENDER_PIXELS = 60_000_000  # caps odd PDFs with poster-sized pages (~A0 at 300 DPI)

# (page_number, image or None, dpi, error name or None)
PageImage = tuple[int, np.ndarray | None, float | None, str | None]


def ocr_document(
    path: Path,
    parsed: ParsedDocument,
    options: OcrOptions | None = None,
    on_page: Callable[[int], None] | None = None,
) -> ParsedDocument:
    """Return a copy of `parsed` where pages that needed OCR now carry OCR text.

    on_page(n) is called after each OCR'd page with how many are done (for progress).
    """
    options = options or OcrOptions()
    doc = parsed.model_copy(deep=True)  # never mutate the caller's object
    targets = {page.page_number: page for page in doc.pages if page.needs_ocr}
    if not targets:
        return doc

    done = attempted = 0
    for number, image, dpi, error in _page_images(path, doc.file_kind, sorted(targets), options):
        attempted += 1
        page = targets[number]
        if image is None:
            page.error = error
            doc.errors.append(f"Page {number}: could not be rendered for OCR.")
            if on_page:
                on_page(attempted)
            continue
        try:
            _ocr_into(page, image, dpi, options)
        except OcrError as exc:
            page.error = str(exc)
            doc.errors.append(f"Page {number}: OCR failed ({exc}).")
            if "not installed" in str(exc):  # every other page would fail the same way
                doc.warnings.append("OCR is unavailable (tesseract not installed).")
                break
        except Exception as exc:  # one bad page must not stop the document
            logger.warning("OCR failed on page %d: %s", number, type(exc).__name__)
            page.error = type(exc).__name__
            doc.errors.append(f"Page {number}: OCR failed.")
        else:
            done += 1
        finally:
            del image  # drop the only reference so the page's pixels are freed now
            if on_page:
                on_page(attempted)

    low = sum(1 for p in targets.values() if LOW_CONFIDENCE_WARNING in p.warnings)
    if done:
        doc.warnings.append(f"OCR applied to {done} page(s).")
    if low:
        doc.warnings.append(f"{low} page(s) have low OCR confidence; please review.")
    logger.info("OCR finished: pages=%d ok=%d low_confidence=%d", len(targets), done, low)
    return doc


def _ocr_into(page: PageText, image: np.ndarray, dpi: float | None, options: OcrOptions) -> None:
    started = time.perf_counter()
    result = ocr_page(preprocess(image, dpi, options.photo_steps), options)
    if page.error:  # the text layer was unreadable but OCR worked: keep a note of it
        page.warnings.append(f"Text layer unreadable ({page.error}); used OCR instead.")
        page.error = None
    page.text = result.text
    page.char_count = sum(1 for ch in result.text if not ch.isspace())
    page.source = "ocr"
    page.ocr_confidence = result.mean_confidence
    page.ocr_seconds = round(time.perf_counter() - started, 2)
    page.needs_ocr = False
    page.warnings.extend(result.warnings)


def _page_images(
    path: Path, kind: FileKind, numbers: list[int], options: OcrOptions
) -> Iterator[PageImage]:
    if kind is FileKind.PDF:
        yield from _pdf_pages(path, numbers, options.dpi)
    elif kind.is_image:
        yield from _image_frames(path, numbers)


def _pdf_pages(path: Path, numbers: list[int], dpi: int) -> Iterator[PageImage]:
    pdf = pdfium.PdfDocument(path)
    try:
        for number in numbers:
            try:
                image, used_dpi = _render(pdf, number, dpi)
            except Exception as exc:
                logger.warning("render failed on page %d: %s", number, type(exc).__name__)
                yield number, None, None, type(exc).__name__
                continue
            yield number, image, used_dpi, None
            del image
    finally:
        pdf.close()


def _render(pdf: pdfium.PdfDocument, number: int, dpi: int) -> tuple[np.ndarray, float]:
    page = pdf[number - 1]
    try:
        width_pt, height_pt = page.get_size()  # PDF units are points: 72 per inch
        scale = dpi / 72
        pixels = width_pt * height_pt * scale * scale
        if pixels > MAX_RENDER_PIXELS:
            scale *= math.sqrt(MAX_RENDER_PIXELS / pixels)
        bitmap = page.render(scale=scale, grayscale=True)  # grayscale: 1/3 of the memory
        try:
            # copy(): the numpy view points into pdfium's buffer, which close() frees.
            image = np.squeeze(bitmap.to_numpy()).copy()
        finally:
            bitmap.close()
    finally:
        page.close()
    return image, scale * 72


def _image_frames(path: Path, numbers: list[int]) -> Iterator[PageImage]:
    with Image.open(path) as source:
        dpi = _dpi(source)
        for number in numbers:
            try:
                source.seek(number - 1)  # TIFFs can hold many pages ("frames")
                # Phones store "rotate me" in EXIF instead of rotating pixels; apply it.
                frame = ImageOps.exif_transpose(source)
                if frame.mode not in ("L", "RGB"):
                    # Fax TIFFs are 1-bit, some scans 16-bit or palette: normalise them.
                    frame = frame.convert("L" if frame.mode in ("1", "I;16", "I", "F") else "RGB")
                image = np.asarray(frame)
            except Exception as exc:
                logger.warning("could not load frame %d: %s", number, type(exc).__name__)
                yield number, None, None, type(exc).__name__
                continue
            yield number, image, dpi, None
            del image, frame


def _dpi(image: Image.Image) -> float | None:
    value = image.info.get("dpi")
    try:
        return float(value[0]) if value else None
    except (TypeError, ValueError, IndexError):
        return None
