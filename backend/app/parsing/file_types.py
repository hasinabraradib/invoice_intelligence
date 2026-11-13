"""Detect what a file really is from its bytes ("magic numbers"), never from its name.

A file called invoice.pdf can be anything; trusting the extension would let an attacker
route a file to the wrong parser (CLAUDE.md rule 6). The extension is only compared
afterwards, to warn the user when it disagrees.
"""

import codecs
import csv
import io
import zipfile
from pathlib import Path, PurePath

from charset_normalizer import from_bytes

from app.parsing.errors import CorruptFile, EmptyFile, UnsupportedFileType
from app.parsing.models import FileKind

# Signatures (first bytes of the file) for binary formats.
PDF_MAGIC = b"%PDF"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"
TIFF_MAGICS = (b"II*\x00", b"MM\x00*")  # little-endian ("Intel") and big-endian ("Motorola")
ZIP_MAGIC = b"PK\x03\x04"  # xlsx is a zip archive of XML files
OLE_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")  # legacy Office "compound file" (.xls)

CSV_SAMPLE_BYTES = 64 * 1024  # enough lines to judge a CSV without reading it all
CSV_DELIMITERS = (",", ";", "\t")

_EXTENSION_KINDS: dict[str, set[FileKind]] = {
    ".pdf": {FileKind.PDF},
    ".png": {FileKind.PNG},
    ".jpg": {FileKind.JPEG},
    ".jpeg": {FileKind.JPEG},
    ".tif": {FileKind.TIFF},
    ".tiff": {FileKind.TIFF},
    ".xlsx": {FileKind.XLSX},
    ".xlsm": {FileKind.XLSX},
    ".xls": {FileKind.XLS},
    ".csv": {FileKind.CSV},
    ".tsv": {FileKind.CSV},
    ".txt": {FileKind.CSV},
}


def detect_file_kind(path: Path) -> FileKind:
    """Return the real kind of the file at `path`, or raise a DocumentError."""
    with path.open("rb") as handle:
        head = handle.read(8)
    if not head:
        raise EmptyFile()

    if head.startswith(PDF_MAGIC):
        return FileKind.PDF
    if head.startswith(PNG_MAGIC):
        return FileKind.PNG
    if head.startswith(JPEG_MAGIC):
        return FileKind.JPEG
    if head.startswith(TIFF_MAGICS):
        return FileKind.TIFF
    if head.startswith(OLE_MAGIC):
        # Could also be .doc/.msg; the xls parser rejects those as corrupt workbooks.
        return FileKind.XLS
    if head.startswith(ZIP_MAGIC):
        return _detect_zip(path)

    with path.open("rb") as handle:
        sample = handle.read(CSV_SAMPLE_BYTES)
    if _looks_like_csv(sample, truncated=path.stat().st_size > len(sample)):
        return FileKind.CSV
    raise UnsupportedFileType()


def _detect_zip(path: Path) -> FileKind:
    # Many formats are zips (docx, odt, jar...). Only a zip with an xl/ folder is a workbook.
    # namelist() reads the zip's table of contents only, it does not decompress anything.
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
    except zipfile.BadZipFile as exc:
        raise CorruptFile("The file looks like a zip/xlsx archive but is damaged.") from exc
    if any(name.startswith("xl/") for name in names):
        return FileKind.XLSX
    raise UnsupportedFileType()


def decode_text(data: bytes) -> tuple[str, str] | None:
    """Decode bytes as text. Returns (text, encoding) or None if it is not text.

    Order: a UTF-16 byte-order mark (Excel "Unicode text" export), then UTF-8 (utf-8-sig
    also strips a UTF-8 BOM), then charset-normalizer's best guess (e.g. cp1252, latin-1).
    """
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        try:
            return data.decode("utf-16"), "utf-16"
        except UnicodeDecodeError:
            return None
    if b"\x00" in data:
        return None  # NUL bytes never occur in real 8-bit text: this is binary data
    try:
        return data.decode("utf-8-sig"), "utf-8-sig"
    except UnicodeDecodeError:
        pass
    best = from_bytes(data).best()
    if best is None:
        return None
    return str(best), best.encoding


def sniff_delimiter(text: str) -> str | None:
    """Pick the delimiter that splits the lines into a consistent number of fields.

    Lines are parsed with the csv module, so delimiters inside "quoted, fields" are ignored.
    For a candidate delimiter we look at the lines it splits into >= 2 fields ("table
    lines"). It qualifies if there are at least 2 table lines, they are at least half of
    all lines (title lines like "ACME Ltd" are tolerated), and >= 80% of them have the same
    field count. Among qualifying delimiters, the one whose common field count covers the
    most lines wins (ties: more columns). A key/value block above a table (two widths)
    also qualifies, see below.
    """
    best: tuple[float, int, str] | None = None
    for delimiter in CSV_DELIMITERS:
        counts = [
            len(row)
            for row in csv.reader(io.StringIO(text), delimiter=delimiter)
            if row and (len(row) > 1 or row[0].strip())  # skip blank lines
        ][:200]
        table = [count for count in counts if count >= 2]
        if len(table) < 2 or len(table) < len(counts) / 2:
            continue
        modal = max(set(table), key=table.count)
        covered = table.count(modal)
        if covered / len(table) < 0.8:
            # Invoice exports often put a key/value block ("Invoice No,INV-55") above the
            # line-item table: two widths. Accept that if both appear on >= 2 lines and
            # together cover >= 80%; the wider one is the table.
            widths = sorted(set(table), key=table.count, reverse=True)[:2]
            if len(widths) < 2 or min(table.count(w) for w in widths) < 2:
                continue
            covered = sum(table.count(w) for w in widths)
            wide = max(widths)
            rows = [i for i, count in enumerate(table) if count == wide]
            # The table must be one unbroken run of >= 3 columns; prose that happens to
            # repeat two comma counts alternates instead.
            contiguous = rows[-1] - rows[0] + 1 == len(rows)
            if covered / len(table) < 0.8 or wide < 3 or not contiguous:
                continue
            modal = wide
        candidate = (covered / len(counts), modal, delimiter)
        if best is None or candidate[:2] > best[:2]:
            best = candidate
    return best[2] if best else None


def _looks_like_csv(sample: bytes, *, truncated: bool) -> bool:
    if truncated:
        # The sample may end mid-character: keep UTF-16 at an even length, and cut
        # 8-bit/UTF-8 text at the last newline byte.
        if sample.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
            sample = sample[: len(sample) // 2 * 2]
        else:
            sample = sample[: sample.rfind(b"\n") + 1] or sample
    decoded = decode_text(sample)
    if decoded is None:
        return False
    text = decoded[0]
    if truncated:
        text = text[: text.rfind("\n") + 1] or text  # drop the incomplete last line
    return sniff_delimiter(text) is not None


def extension_warning(kind: FileKind, original_filename: str | None) -> str | None:
    """Warn (never fail) when the name's extension disagrees with the detected kind."""
    if not original_filename:
        return None
    suffix = PurePath(original_filename).suffix.lower()
    if not suffix:
        return f"File has no extension; detected type '{kind}'."
    if kind in _EXTENSION_KINDS.get(suffix, set()):
        return None
    return (
        f"File extension '{suffix}' does not match its content; "
        f"treating it as '{kind}' based on its content."
    )
