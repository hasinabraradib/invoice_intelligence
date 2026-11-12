"""Extract the text layer of a digital PDF, page by page.

Two libraries, two jobs:
- pypdfium2 (Chrome's PDF engine) opens the file first. It is fast and strict about
  encryption and damage, so it gives clean PasswordProtected / CorruptFile errors and an
  exact page count.
- pdfplumber extracts the text of each page.

Memory: pages are processed one at a time and each page's cached layout objects are freed
(page.close()) before moving on, so a 1000-page PDF does not accumulate in memory.
"""

import logging
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium

from app.parsing.errors import CorruptFile, PasswordProtected
from app.parsing.models import FileKind, PageText, ParsedDocument

logger = logging.getLogger(__name__)

# Below this many non-whitespace characters a page is treated as scanned (image only) and
# flagged for OCR. Real invoice pages have hundreds of characters; 25 tolerates a stray
# page number or watermark on an otherwise scanned page.
MIN_TEXT_CHARS = 25

_PDFIUM_PASSWORD_ERROR = 4  # FPDF_ERR_PASSWORD in PDFium's C API


def parse_pdf(path: Path, *, max_pages: int = 2000) -> ParsedDocument:
    page_count = _open_and_count_pages(path)
    doc = ParsedDocument(file_kind=FileKind.PDF, page_count=page_count)
    pages_to_read = min(page_count, max_pages)
    if page_count > max_pages:
        doc.warnings.append(
            f"PDF has {page_count} pages; only the first {max_pages} were processed."
        )

    try:
        pdf = pdfplumber.open(path)
    except Exception as exc:
        # PDFium could open it but pdfminer cannot: no text layer we can read.
        # Every page goes to OCR (later step) instead of failing the whole document.
        logger.warning("pdfplumber could not open PDF: %s", type(exc).__name__)
        doc.errors.append(f"Text extraction failed for the whole file ({type(exc).__name__}).")
        doc.pages = [
            PageText(page_number=n, source="none", needs_ocr=True, error=type(exc).__name__)
            for n in range(1, pages_to_read + 1)
        ]
        return doc

    with pdf:
        for index, page in enumerate(pdf.pages[:pages_to_read]):
            doc.pages.append(_extract_page(page, index + 1))
            if doc.pages[-1].error:
                doc.errors.append(f"Page {index + 1}: text extraction failed.")

    if doc.pages_needing_ocr:
        doc.warnings.append(
            f"{doc.pages_needing_ocr} of {pages_to_read} page(s) have little or no text "
            "and need OCR."
        )
    return doc


def _open_and_count_pages(path: Path) -> int:
    try:
        pdf = pdfium.PdfDocument(path)
    except pdfium.PdfiumError as exc:
        if exc.err_code == _PDFIUM_PASSWORD_ERROR:
            raise PasswordProtected() from exc
        raise CorruptFile("The PDF is damaged or unreadable.") from exc
    try:
        count = len(pdf)
    finally:
        pdf.close()
    if count == 0:
        raise CorruptFile("The PDF contains no pages.")
    return count


def _extract_page(page: pdfplumber.page.Page, page_number: int) -> PageText:
    try:
        text = page.extract_text() or ""
    except Exception as exc:  # one broken page must not stop the document
        logger.warning("text extraction failed on page %d: %s", page_number, type(exc).__name__)
        return PageText(
            page_number=page_number, source="none", needs_ocr=True, error=type(exc).__name__
        )
    finally:
        page.close()  # free this page's cached characters/objects before the next page

    char_count = sum(1 for ch in text if not ch.isspace())
    return PageText(
        page_number=page_number,
        text=text,
        char_count=char_count,
        source="text_layer",
        needs_ocr=char_count < MIN_TEXT_CHARS,
    )
