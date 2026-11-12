"""parse_document picks the parser from the detected kind; images go straight to OCR."""

import pytest

from app.parsing.dispatch import parse_document
from app.parsing.errors import CorruptFile, EmptyFile
from app.parsing.models import FileKind
from tests import factories


def test_multi_frame_tiff_has_one_ocr_page_per_frame(tmp_path):
    doc = parse_document(
        factories.image(tmp_path / "scan.tiff", "TIFF", frames=3), "scan.tiff", run_ocr=False
    )

    assert doc.file_kind is FileKind.TIFF
    assert doc.page_count == 3
    assert [p.page_number for p in doc.pages] == [1, 2, 3]
    assert all(p.needs_ocr and p.text == "" for p in doc.pages)
    assert doc.warnings == []


def test_png_renamed_to_pdf_is_parsed_as_image_with_warning(tmp_path):
    doc = parse_document(factories.image(tmp_path / "upload", "PNG"), "invoice.pdf", run_ocr=False)

    assert doc.file_kind is FileKind.PNG
    assert doc.page_count == 1 and doc.pages_needing_ocr == 1
    assert "does not match" in doc.warnings[0]


@pytest.mark.parametrize(
    ("builder", "kind"),
    [
        (lambda p: factories.text_pdf(p / "f"), FileKind.PDF),
        (lambda p: factories.image(p / "f", "JPEG"), FileKind.JPEG),
        (lambda p: factories.invoice_xlsx(p / "f"), FileKind.XLSX),
        (lambda p: factories.invoice_xls(p / "f"), FileKind.XLS),
        (lambda p: factories.semicolon_csv_with_bom(p / "f"), FileKind.CSV),
    ],
)
def test_dispatches_each_kind(tmp_path, builder, kind):
    assert parse_document(builder(tmp_path), run_ocr=False).file_kind is kind


def test_truncated_png_is_corrupt(tmp_path):
    path = factories.image(tmp_path / "scan.png", "PNG")
    path.write_bytes(path.read_bytes()[:40])
    with pytest.raises(CorruptFile):
        parse_document(path)


def test_empty_file(tmp_path):
    path = tmp_path / "empty"
    path.write_bytes(b"")
    with pytest.raises(EmptyFile):
        parse_document(path)
