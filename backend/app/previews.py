"""PNG previews of document pages for the review screen, cached on disk."""

import os
import tempfile
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageOps

from app.parsing.file_types import detect_file_kind
from app.parsing.models import FileKind

PREVIEW_WIDTH = 1200  # sharp enough to read on a laptop, ~200-400 KB per page
MAX_HEIGHT = 6000  # stop absurdly tall pages from becoming huge images


class NoPreview(Exception):
    """This kind of document has no pages to show (spreadsheets)."""


class PageNotFound(Exception):
    pass


def page_preview(source: Path, page: int, cache_dir: Path) -> Path:
    """Return the cached PNG for `page` (1-based), rendering it the first time."""
    target = cache_dir / f"{page}-{PREVIEW_WIDTH}.png"
    if target.exists():
        return target
    kind = detect_file_kind(source)  # by content, never by name
    if kind is FileKind.PDF:
        image = _pdf_page(source, page)
    elif kind.is_image:
        image = _image_frame(source, page)
    else:
        raise NoPreview()
    cache_dir.mkdir(parents=True, exist_ok=True)
    # Write to a temp file, then rename: a reader never sees a half-written PNG.
    with tempfile.NamedTemporaryFile(dir=cache_dir, suffix=".tmp", delete=False) as tmp:
        image.save(tmp, format="PNG", optimize=True)
    os.replace(tmp.name, target)
    return target


def _pdf_page(source: Path, page: int) -> Image.Image:
    pdf = pdfium.PdfDocument(source)
    try:
        if not 1 <= page <= len(pdf):
            raise PageNotFound()
        pdf_page = pdf[page - 1]
        width, height = pdf_page.get_size()
        scale = min(PREVIEW_WIDTH / width, MAX_HEIGHT / height)
        bitmap = pdf_page.render(scale=scale)
        image = bitmap.to_pil()
        bitmap.close()
        pdf_page.close()
        return image
    finally:
        pdf.close()


def _image_frame(source: Path, page: int) -> Image.Image:
    with Image.open(source) as image:
        try:
            image.seek(page - 1)  # TIFF frames; other images only have frame 1
        except EOFError as exc:
            raise PageNotFound() from exc
        if page < 1:
            raise PageNotFound()
        frame = ImageOps.exif_transpose(image)  # phone photos: apply EXIF rotation
        frame = frame.convert("L" if frame.mode in ("1", "L", "I;16", "I", "F") else "RGB")
    if frame.width > PREVIEW_WIDTH:  # shrink only; never blow up small images
        frame = frame.resize((PREVIEW_WIDTH, round(frame.height * PREVIEW_WIDTH / frame.width)))
    return frame
