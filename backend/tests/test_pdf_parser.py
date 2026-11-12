"""PDF text-layer extraction, OCR flags and typed errors."""

import subprocess
import sys
from pathlib import Path

import pytest

from app.parsing import pdf_parser
from app.parsing.errors import CorruptFile, PasswordProtected
from app.parsing.pdf_parser import parse_pdf
from tests import factories


def test_text_pdf_pages_have_text(tmp_path):
    doc = parse_pdf(factories.text_pdf(tmp_path / "invoice.pdf", pages=2))

    assert doc.page_count == 2
    assert [p.page_number for p in doc.pages] == [1, 2]
    assert all(not p.needs_ocr and p.source == "text_layer" for p in doc.pages)
    assert "INV-2024-001" in doc.pages[0].text
    assert doc.pages[0].char_count > 100
    assert doc.errors == []
    assert doc.pages_needing_ocr == 0


def test_image_only_page_needs_ocr(tmp_path):
    doc = parse_pdf(factories.pdf_with_image_page(tmp_path / "mixed.pdf"))

    assert [p.needs_ocr for p in doc.pages] == [False, True]
    assert doc.pages[1].char_count == 0
    assert doc.pages_needing_ocr == 1
    assert any("need OCR" in w for w in doc.warnings)


def test_encrypted_pdf_raises_password_protected(tmp_path):
    with pytest.raises(PasswordProtected):
        parse_pdf(factories.encrypted_pdf(tmp_path / "locked.pdf"))


def test_truncated_pdf_raises_corrupt(tmp_path):
    with pytest.raises(CorruptFile):
        parse_pdf(factories.truncated_pdf(tmp_path / "broken.pdf"))


def test_one_failing_page_does_not_stop_the_others(tmp_path, monkeypatch):
    real_extract = pdf_parser.pdfplumber.page.Page.extract_text

    def flaky(page, *args, **kwargs):
        if page.page_number == 2:
            raise ValueError("simulated broken page")
        return real_extract(page, *args, **kwargs)

    monkeypatch.setattr(pdf_parser.pdfplumber.page.Page, "extract_text", flaky)
    doc = parse_pdf(factories.text_pdf(tmp_path / "invoice.pdf", pages=3))

    assert [p.error for p in doc.pages] == [None, "ValueError", None]
    assert doc.pages[1].needs_ocr  # fall back to OCR for the broken page
    assert doc.errors == ["Page 2: text extraction failed."]
    assert "simulated" not in str(doc.model_dump())  # raw exception text is not exposed


def test_max_pages_cap(tmp_path):
    doc = parse_pdf(factories.text_pdf(tmp_path / "long.pdf", pages=5), max_pages=2)

    assert doc.page_count == 5
    assert len(doc.pages) == 2
    assert any("only the first 2" in w for w in doc.warnings)


_PEAK_MEMORY_SCRIPT = """
import resource, sys
from pathlib import Path
from app.parsing.pdf_parser import parse_pdf
doc = parse_pdf(Path(sys.argv[1]))
assert len(doc.pages) == doc.page_count and doc.pages_needing_ocr == 0
peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
print(peak if sys.platform == "darwin" else peak * 1024)  # macOS: bytes, Linux: KiB
"""


def _peak_rss_bytes(pdf_path) -> int:
    # A fresh subprocess, so the peak is not polluted by other tests in this process.
    result = subprocess.run(  # noqa: S603  (fixed command, our own interpreter)
        [sys.executable, "-c", _PEAK_MEMORY_SCRIPT, str(pdf_path)],
        capture_output=True,
        text=True,
        check=True,
        cwd=Path(__file__).resolve().parents[1],
    )
    return int(result.stdout.strip())


@pytest.mark.slow
def test_thousand_page_pdf_memory_stays_bounded(tmp_path):
    """Page-by-page processing: 10x more pages must not need anywhere near 10x the memory.

    (tracemalloc is not used: it slows pdfminer's many small allocations ~100x.)
    """
    small = _peak_rss_bytes(factories.text_pdf(tmp_path / "small.pdf", pages=100))
    large = _peak_rss_bytes(factories.text_pdf(tmp_path / "large.pdf", pages=1000))

    print(f"peak RSS: 100 pages {small / 1e6:.0f} MB, 1000 pages {large / 1e6:.0f} MB")
    assert large < small * 2, f"{large / 1e6:.0f} MB vs {small / 1e6:.0f} MB"
