"""The common structure every parser returns, whatever the input format.

Spreadsheet cells stay raw strings on purpose: converting "1.234,50" to Decimal or
"03/04/2024" to a date depends on locale and context, so it happens later, in tested code
(CLAUDE.md rules 1 and 2). Here we only preserve what the file says.
"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class FileKind(StrEnum):
    PDF = "pdf"
    PNG = "png"
    JPEG = "jpeg"
    TIFF = "tiff"
    XLSX = "xlsx"
    XLS = "xls"
    CSV = "csv"

    @property
    def is_image(self) -> bool:
        return self in {FileKind.PNG, FileKind.JPEG, FileKind.TIFF}


class PageText(BaseModel):
    page_number: int = Field(ge=1)  # 1-based, as humans count pages
    text: str = ""
    char_count: int = 0  # non-whitespace characters, so a page of blanks counts as 0
    # "text_layer" = text embedded in a digital PDF, "ocr" = recognised from pixels,
    # "none" = no text obtained yet.
    source: Literal["text_layer", "ocr", "none"] = "text_layer"
    needs_ocr: bool = False
    ocr_confidence: float | None = None  # 0-100, mean over words; only for source="ocr"
    ocr_seconds: float | None = None  # time spent on OCR, kept for evaluation
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None


class ParsedSheet(BaseModel):
    name: str
    # 0-based row index in the original sheet (row 3 in Excel = index 2).
    # None means no header row was found; columns are then named column_1, column_2, ...
    header_row_index: int | None
    columns: list[str]
    rows: list[dict[str, str]]
    # Non-empty rows ABOVE the header (e.g. "ACME Ltd", "Invoice no: 42"). Invoices often
    # keep key facts there, so we keep them instead of throwing them away.
    preamble_rows: list[list[str]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ParsedDocument(BaseModel):
    file_kind: FileKind
    pages: list[PageText] = Field(default_factory=list)
    sheets: list[ParsedSheet] = Field(default_factory=list)
    page_count: int = 0  # PDF pages or image frames; 0 for spreadsheets
    warnings: list[str] = Field(default_factory=list)  # processed, but worth a look
    errors: list[str] = Field(default_factory=list)  # partial failures (e.g. one bad page)

    @property
    def pages_needing_ocr(self) -> int:
        return sum(1 for page in self.pages if page.needs_ocr)

    @property
    def pages_ocr(self) -> int:
        return sum(1 for page in self.pages if page.source == "ocr")


class ParseLimits(BaseModel):
    """Caps applied while parsing (filled from settings by the caller)."""

    max_pdf_pages: int = 2000
    max_sheet_rows: int = 10_000
    max_sheet_columns: int = 200
