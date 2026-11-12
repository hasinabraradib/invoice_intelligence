"""Single entry point: detect the real file type, then call the matching parser."""

from pathlib import Path

from PIL import Image, UnidentifiedImageError

from app.parsing.errors import CorruptFile, FileTooLarge
from app.parsing.file_types import detect_file_kind, extension_warning
from app.parsing.models import FileKind, PageText, ParsedDocument, ParseLimits
from app.parsing.pdf_parser import parse_pdf
from app.parsing.spreadsheet_parser import parse_csv, parse_xls, parse_xlsx

# Pillow refuses images above this many pixels ("decompression bomb" protection): a tiny
# compressed file could otherwise expand to gigabytes when decoded. 100 MP is far above
# a 600-DPI A4 scan (about 35 MP).
Image.MAX_IMAGE_PIXELS = 100_000_000
MAX_IMAGE_FRAMES = 500


def parse_document(
    path: Path, original_filename: str | None = None, limits: ParseLimits | None = None
) -> ParsedDocument:
    limits = limits or ParseLimits()
    kind = detect_file_kind(path)

    if kind is FileKind.PDF:
        doc = parse_pdf(path, max_pages=limits.max_pdf_pages)
    elif kind is FileKind.XLSX:
        doc = parse_xlsx(path, limits)
    elif kind is FileKind.XLS:
        doc = parse_xls(path, limits)
    elif kind is FileKind.CSV:
        doc = parse_csv(path, limits)
    else:
        doc = parse_image(path, kind)

    if warning := extension_warning(kind, original_filename):
        doc.warnings.insert(0, warning)
    return doc


def parse_image(path: Path, kind: FileKind) -> ParsedDocument:
    """Images have no text layer: report one page per frame, all needing OCR."""
    try:
        with Image.open(path) as image:
            image.verify()  # checks structure/checksums without decoding all pixels
        with Image.open(path) as image:  # verify() leaves the object unusable: reopen
            frames = getattr(image, "n_frames", 1)  # multi-page TIFFs have several frames
    except Image.DecompressionBombError as exc:
        raise FileTooLarge("The image has too many pixels to process safely.") from exc
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise CorruptFile("The image is damaged or unreadable.") from exc

    doc = ParsedDocument(file_kind=kind, page_count=frames)
    if frames > MAX_IMAGE_FRAMES:
        doc.warnings.append(
            f"Image has {frames} frames; only the first {MAX_IMAGE_FRAMES} will be processed."
        )
        frames = MAX_IMAGE_FRAMES
    doc.pages = [
        PageText(page_number=n, source="none", needs_ocr=True) for n in range(1, frames + 1)
    ]
    return doc
