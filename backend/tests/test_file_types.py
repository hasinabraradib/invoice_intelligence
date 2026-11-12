"""File type detection must rely on content (magic bytes), never on the file name."""

import zipfile

import pytest

from app.parsing.errors import CorruptFile, EmptyFile, UnsupportedFileType
from app.parsing.file_types import detect_file_kind, extension_warning
from app.parsing.models import FileKind
from tests import factories


@pytest.mark.parametrize(
    ("builder", "expected"),
    [
        (lambda p: factories.text_pdf(p / "a.pdf"), FileKind.PDF),
        (lambda p: factories.image(p / "a.png", "PNG"), FileKind.PNG),
        (lambda p: factories.image(p / "a.jpg", "JPEG"), FileKind.JPEG),
        (lambda p: factories.image(p / "a.tif", "TIFF"), FileKind.TIFF),
        (lambda p: factories.invoice_xlsx(p / "a.xlsx"), FileKind.XLSX),
        (lambda p: factories.invoice_xls(p / "a.xls"), FileKind.XLS),
        (lambda p: factories.semicolon_csv_with_bom(p / "a.csv"), FileKind.CSV),
    ],
)
def test_detects_real_files(tmp_path, builder, expected):
    assert detect_file_kind(builder(tmp_path)) is expected


@pytest.mark.parametrize("magic", [b"II*\x00", b"MM\x00*"])
def test_detects_tiff_in_both_byte_orders(tmp_path, magic):
    path = tmp_path / "scan"
    path.write_bytes(magic + b"\x00" * 100)
    assert detect_file_kind(path) is FileKind.TIFF


def test_png_named_pdf_is_detected_as_png_with_warning(tmp_path):
    path = factories.image(tmp_path / "invoice.pdf", "PNG")
    kind = detect_file_kind(path)
    assert kind is FileKind.PNG
    assert "does not match" in extension_warning(kind, "invoice.pdf")
    assert extension_warning(FileKind.PDF, "invoice.PDF") is None


def test_empty_file(tmp_path):
    path = tmp_path / "empty.pdf"
    path.write_bytes(b"")
    with pytest.raises(EmptyFile):
        detect_file_kind(path)


def test_zip_without_xl_folder_is_unsupported(tmp_path):
    path = tmp_path / "fake.xlsx"  # e.g. a .docx or any other zip renamed to .xlsx
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", "<w/>")
    with pytest.raises(UnsupportedFileType):
        detect_file_kind(path)


def test_damaged_zip_is_corrupt(tmp_path):
    path = tmp_path / "broken.xlsx"
    path.write_bytes(b"PK\x03\x04" + b"\x00" * 50)
    with pytest.raises(CorruptFile):
        detect_file_kind(path)


@pytest.mark.parametrize(
    "content",
    [
        b"\x7fELF\x02\x01\x01" + bytes(range(256)) * 4,  # an executable
        b"Dear customer, thank you for your order.\nWe will ship it soon!\n",  # prose
        b"GIF89a" + b"\x00" * 50,  # an image format we do not accept
    ],
)
def test_other_content_is_unsupported(tmp_path, content):
    path = tmp_path / "file.csv"
    path.write_bytes(content)
    with pytest.raises(UnsupportedFileType):
        detect_file_kind(path)


def test_utf16_tab_separated_text_is_csv(tmp_path):
    # Excel's "Unicode text" export: UTF-16 with BOM, tab separated.
    path = tmp_path / "export.txt"
    path.write_bytes("Item\tQty\nBolt\t3\n".encode("utf-16"))
    assert detect_file_kind(path) is FileKind.CSV
