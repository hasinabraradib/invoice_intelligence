"""Parse xlsx / xls / csv into ParsedSheets of raw strings.

All three formats are first turned into a plain grid (list of rows of strings), then share
one cleaning pipeline (_build_sheet): strip whitespace, drop empty rows/columns, cap the
size, find the header row, and name the columns.

Why the header row must be detected: real invoices exported to Excel often start with
title rows ("ACME Ltd", "Invoice 42") above the actual table header.
"""

import csv
import io
import logging
import math
import re
import zipfile
from pathlib import Path

import openpyxl
import pandas as pd
import xlrd

from app.parsing.errors import CorruptFile, EmptyFile, PasswordProtected
from app.parsing.file_types import CSV_SAMPLE_BYTES, decode_text, sniff_delimiter
from app.parsing.models import FileKind, ParsedDocument, ParsedSheet, ParseLimits

logger = logging.getLogger(__name__)

HEADER_SCAN_ROWS = 30  # look for the header within the first 30 non-empty rows
_MERGE_CELL_TAG = re.compile(rb"<(?:\w+:)?mergeCell[\s/>]")  # not <mergeCells>
_ENCRYPTED_OOXML_MARKER = "EncryptedPackage".encode("utf-16-le")


# ---------------------------------------------------------------- public parsers


def parse_xlsx(path: Path, limits: ParseLimits | None = None) -> ParsedDocument:
    limits = limits or ParseLimits()
    # We pass an open file handle, not the path: given a path, openpyxl checks the file
    # NAME for ".xlsx", and our stored uploads have random names with no extension.
    with path.open("rb") as handle:
        try:
            # read_only streams rows instead of building the whole workbook in memory.
            # data_only returns the value Excel last calculated for formula cells, not the
            # formula text (files never opened in Excel may have no cached value -> "").
            workbook = openpyxl.load_workbook(handle, read_only=True, data_only=True)
        except Exception as exc:
            raise CorruptFile("The workbook (xlsx) is damaged or unreadable.") from exc
        try:
            merged = _xlsx_merged_ranges(path, workbook)
            frames = _read_excel_frames(workbook, engine="openpyxl", limits=limits)
        finally:
            workbook.close()
    return _workbook_document(FileKind.XLSX, frames, merged, limits)


def parse_xls(path: Path, limits: ParseLimits | None = None) -> ParsedDocument:
    limits = limits or ParseLimits()
    try:
        # formatting_info=True is needed to see merged cells; on_demand loads sheets lazily.
        book = xlrd.open_workbook(str(path), formatting_info=True, on_demand=True)
    except xlrd.XLRDError as exc:
        if "encrypted" in str(exc).lower():
            raise PasswordProtected() from exc
        raise _ole_error(path) from exc
    except Exception as exc:  # xlrd raises assorted low-level errors on garbage input
        raise _ole_error(path) from exc
    try:
        merged = {sheet.name: len(sheet.merged_cells) for sheet in book.sheets()}
        frames = _read_excel_frames(book, engine="xlrd", limits=limits)
    finally:
        book.release_resources()
    return _workbook_document(FileKind.XLS, frames, merged, limits)


def parse_csv(path: Path, limits: ParseLimits | None = None) -> ParsedDocument:
    limits = limits or ParseLimits()
    data = path.read_bytes()  # bounded by MAX_UPLOAD_MB, enforced at upload time
    decoded = decode_text(data)
    if decoded is None:
        raise CorruptFile("The CSV file is not valid text.")
    text, encoding = decoded
    delimiter = sniff_delimiter(text[:CSV_SAMPLE_BYTES])
    if delimiter is None:
        raise CorruptFile("Could not find a consistent delimiter (comma, semicolon or tab).")

    doc = ParsedDocument(file_kind=FileKind.CSV)
    if encoding not in {"utf-8-sig", "utf-8", "ascii"}:
        doc.warnings.append(f"CSV decoded as '{encoding}' (detected automatically).")

    # csv.reader handles "quoted; fields", doubled "" quotes and line breaks inside quotes.
    # It also accepts rows of different lengths (title rows), unlike pandas.read_csv.
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    max_raw_rows = limits.max_sheet_rows + HEADER_SCAN_ROWS + 1
    grid: list[list[str]] = []
    try:
        for row in reader:
            grid.append(row)
            if len(grid) >= max_raw_rows:
                break
    except csv.Error as exc:
        raise CorruptFile("The CSV file is malformed.") from exc

    sheet = _build_sheet("csv", grid, limits, hit_row_limit=len(grid) >= max_raw_rows)
    if sheet is None:
        raise EmptyFile("The CSV file contains no data.")
    doc.sheets.append(sheet)
    return doc


# ---------------------------------------------------------------- workbook helpers


def _read_excel_frames(source, *, engine: str, limits: ParseLimits) -> dict[str, pd.DataFrame]:
    # header=None: we find the header ourselves. dtype=str + keep_default_na=False keep
    # every cell as the raw text ("N/A" stays "N/A", empty stays ""), never NaN or float.
    try:
        return pd.read_excel(
            source,
            sheet_name=None,  # None = every sheet, as {name: DataFrame}
            header=None,
            dtype=str,
            keep_default_na=False,
            nrows=limits.max_sheet_rows + HEADER_SCAN_ROWS + 1,
            engine=engine,
        )
    except Exception as exc:
        raise CorruptFile("The workbook could not be read.") from exc


def _workbook_document(
    kind: FileKind,
    frames: dict[str, pd.DataFrame],
    merged: dict[str, int],
    limits: ParseLimits,
) -> ParsedDocument:
    doc = ParsedDocument(file_kind=kind)
    row_limit = limits.max_sheet_rows + HEADER_SCAN_ROWS + 1
    for name, frame in frames.items():
        grid = [[_cell_text(value) for value in row] for row in frame.itertuples(index=False)]
        sheet = _build_sheet(str(name), grid, limits, hit_row_limit=len(grid) >= row_limit)
        if sheet is None:
            doc.warnings.append(f"Sheet '{name}' is empty and was skipped.")
            continue
        if merged.get(str(name)):
            sheet.warnings.append(
                f"Sheet has {merged[str(name)]} merged cell range(s); a merged value "
                "appears only in the first cell of its range."
            )
        doc.sheets.append(sheet)
    if not doc.sheets:
        raise EmptyFile("The workbook contains no data.")
    return doc


def _cell_text(value: object) -> str:
    # Safety net: dtype=str should already give strings, but never let NaN/None become "nan".
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return str(value)


def _xlsx_merged_ranges(path: Path, workbook) -> dict[str, int]:
    """Count <mergeCell> tags per sheet by streaming each sheet's XML from the zip.

    openpyxl's read-only mode does not expose merged cells, and loading the workbook in
    full mode could use a lot of memory, so we scan the raw XML in 1 MiB chunks instead.
    """
    counts: dict[str, int] = {}
    try:
        with zipfile.ZipFile(path) as archive:
            for sheet in workbook.worksheets:
                member = getattr(sheet, "_worksheet_path", None)  # openpyxl internal
                if not member:
                    continue
                counts[sheet.title] = _count_tag(archive, member.lstrip("/"))
    except (zipfile.BadZipFile, KeyError, OSError):
        logger.warning("could not scan xlsx for merged cells")
    return counts


def _count_tag(archive: zipfile.ZipFile, member: str) -> int:
    count, tail = 0, b""
    with archive.open(member) as stream:
        while chunk := stream.read(1024 * 1024):
            buffer = tail + chunk
            # Only count matches that end in the new chunk; ones fully inside `tail`
            # were already counted in the previous round.
            count += sum(1 for m in _MERGE_CELL_TAG.finditer(buffer) if m.end() > len(tail))
            tail = buffer[-64:]
    return count


def _ole_error(path: Path) -> Exception:
    # A password-protected .xlsx is stored inside an OLE container (same magic bytes as
    # .xls) with an "EncryptedPackage" stream. Detect it to give a useful error.
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            if _ENCRYPTED_OOXML_MARKER in chunk:
                return PasswordProtected()
    return CorruptFile("The file is not a readable Excel (xls) workbook.")


# ---------------------------------------------------------------- shared pipeline


def _build_sheet(
    name: str, grid: list[list[str]], limits: ParseLimits, *, hit_row_limit: bool
) -> ParsedSheet | None:
    """Clean a raw grid and turn it into a ParsedSheet. Returns None if it has no data."""
    warnings: list[str] = []

    # 1. Strip whitespace and drop fully empty rows, remembering each row's original index.
    rows = [
        (index, [str(cell).strip() for cell in row])
        for index, row in enumerate(grid)
        if any(str(cell).strip() for cell in row)
    ]
    if not rows:
        return None

    # 2. Drop columns that are empty in every row.
    width = max(len(cells) for _, cells in rows)
    rows = [(i, cells + [""] * (width - len(cells))) for i, cells in rows]
    keep = [c for c in range(width) if any(cells[c] for _, cells in rows)]
    if len(keep) > limits.max_sheet_columns:
        warnings.append(
            f"Sheet has {len(keep)} columns; only the first {limits.max_sheet_columns} were kept."
        )
        keep = keep[: limits.max_sheet_columns]
    rows = [(i, [cells[c] for c in keep]) for i, cells in rows]

    # 3. Find the header row; rows above it are kept as the preamble.
    position = _find_header(rows[:HEADER_SCAN_ROWS])
    if position is None:
        warnings.append("No header row detected; columns are named column_1, column_2, ...")
        header_row_index = None
        columns = [f"column_{n}" for n in range(1, len(keep) + 1)]
        preamble, data = [], rows
    else:
        header_row_index, header_cells = rows[position]
        columns = _column_names(header_cells, warnings)
        preamble = [[cell for cell in cells if cell] for _, cells in rows[:position]]
        data = rows[position + 1 :]

    # 4. Cap the number of data rows.
    if hit_row_limit or len(data) > limits.max_sheet_rows:
        warnings.append(
            f"Sheet has more than {limits.max_sheet_rows} data rows; only the first "
            f"{limits.max_sheet_rows} were kept."
        )
        data = data[: limits.max_sheet_rows]

    return ParsedSheet(
        name=name,
        header_row_index=header_row_index,
        columns=columns,
        rows=[dict(zip(columns, cells, strict=True)) for _, cells in data],
        preamble_rows=preamble,
        warnings=warnings,
    )


def _find_header(rows: list[tuple[int, list[str]]]) -> int | None:
    """Return the position (in `rows`) of the first row that looks like a header.

    A header row:
    - has >= 2 filled cells and MORE than half as many as the widest row nearby
      (a 2-cell line above a 4-column table is not the header);
    - does not start with a label such as "Invoice no:" (that is a key: value line);
    - has mostly words, not numbers or dates ("Qty" yes, "3" or "2024-01-15" no).
    """
    if not rows:
        return None
    widest = max(sum(1 for cell in cells if cell) for _, cells in rows)
    for position, (_, cells) in enumerate(rows):
        filled = [cell for cell in cells if cell]
        if len(filled) < 2 or len(filled) <= widest / 2 or filled[0].endswith(":"):
            continue
        wordy = sum(1 for cell in filled if any(ch.isalpha() for ch in cell))
        if wordy / len(filled) >= 0.75:
            return position
    return None


def _column_names(header_cells: list[str], warnings: list[str]) -> list[str]:
    """Header texts become column names; blanks get a placeholder, duplicates a suffix."""
    names: list[str] = []
    seen: dict[str, int] = {}
    duplicates: list[str] = []
    for position, cell in enumerate(header_cells, start=1):
        base = cell or f"column_{position}"
        if base in seen:
            seen[base] += 1
            duplicates.append(base)
            names.append(f"{base}_{seen[base]}")
        else:
            seen[base] = 1
            names.append(base)
    if duplicates:
        unique = ", ".join(sorted(set(duplicates)))
        warnings.append(f"Duplicate column names renamed with a numeric suffix: {unique}.")
    return names
