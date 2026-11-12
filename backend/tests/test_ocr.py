"""OCR end to end with the real Tesseract binary (skipped where it is not installed)."""

import shutil

import numpy as np
import pytest

from app.ocr.engine import LOW_CONFIDENCE_WARNING, NO_TEXT_WARNING, ocr_page
from app.ocr.preprocess import preprocess
from app.parsing.dispatch import parse_document
from app.parsing.models import FileKind
from tests import factories

pytestmark = pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract missing")


def _ocr(page):
    return ocr_page(preprocess(np.asarray(page)))


def test_clean_invoice_image():
    result = _ocr(factories.invoice_image())

    assert "INV-2041" in result.text
    assert "1,320.00" in result.text
    assert result.mean_confidence >= 80
    assert result.word_count >= 15
    assert result.psm_used in (4, 6)
    assert result.warnings == []


def test_noisy_and_rotated_still_readable():
    clean = _ocr(factories.invoice_image())
    damaged = _ocr(factories.rotated(factories.noisy(factories.invoice_image(), sigma=30), 5))

    assert "INV-2041" in damaged.text
    assert "1,320.00" in damaged.text
    assert 60 <= damaged.mean_confidence <= clean.mean_confidence


def test_blank_page_returns_empty_text_with_warning():
    result = _ocr(np.full((900, 1700), 255, np.uint8))

    assert result.text == ""
    assert result.word_count == 0
    assert result.mean_confidence == 0
    assert result.warnings == [NO_TEXT_WARNING]


@pytest.mark.slow  # ~7 s: Tesseract struggles (by design) with both PSMs
def test_very_noisy_image_warns_low_confidence():
    small = factories.invoice_image(font_size=14)
    result = _ocr(factories.noisy(small, sigma=110, seed=3))

    assert LOW_CONFIDENCE_WARNING in result.warnings


def test_multi_frame_tiff_gives_one_ocr_page_per_frame(tmp_path):
    frames = [
        factories.invoice_image(["Invoice No: INV-1001", "Page one of two"]),
        factories.invoice_image(["Invoice No: INV-1001", "Total: 99.50"]),
    ]
    path = tmp_path / "scan.tiff"
    frames[0].save(path, format="TIFF", save_all=True, append_images=frames[1:])

    doc = parse_document(path, "scan.tiff")

    assert doc.file_kind is FileKind.TIFF
    assert [p.source for p in doc.pages] == ["ocr", "ocr"]
    assert "one of two" in doc.pages[0].text and "99.50" in doc.pages[1].text
    assert all(not p.needs_ocr and p.ocr_confidence > 60 for p in doc.pages)


def test_mixed_pdf_only_ocrs_the_image_page(tmp_path):
    doc = parse_document(factories.scanned_pdf_mixed(tmp_path / "mixed.pdf"))

    text_page, scan_page = doc.pages
    assert text_page.source == "text_layer" and text_page.ocr_confidence is None
    assert "INV-2024-001" in text_page.text
    assert scan_page.source == "ocr" and not scan_page.needs_ocr
    assert "INV-2041" in scan_page.text and "1,320.00" in scan_page.text
    assert doc.pages_needing_ocr == 0 and doc.pages_ocr == 1
    assert "OCR applied to 1 page(s)." in doc.warnings
