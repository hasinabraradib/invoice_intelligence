"""Request and response models for the REST API (they also drive the OpenAPI docs)."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models import DocumentStatus, JobStatus
from app.parsing.models import FileKind


class Page[T](BaseModel):
    items: list[T]
    total: int  # number of matches before limit/offset
    limit: int
    offset: int


# ---------------------------------------------------------------- uploads


class FileError(BaseModel):
    code: str
    message: str


class UploadResult(BaseModel):
    original_filename: str  # sanitised for display; never used as a path
    status_code: int  # per file: 202 accepted, 200 duplicate, 413/415/422 rejected
    document_id: uuid.UUID | None = None
    job_id: uuid.UUID | None = None
    duplicate: bool = False  # True: these bytes were uploaded before; nothing new stored
    detected_kind: FileKind | None = None
    sha256: str | None = None
    size: int | None = None
    warnings: list[str] = Field(default_factory=list)
    error: FileError | None = None


class UploadResponse(BaseModel):
    results: list[UploadResult]

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "results": [
                    {
                        "original_filename": "invoice.pdf",
                        "status_code": 202,
                        "document_id": "0b7c6a1e-5d7e-4c1a-9a51-3f0f9a5f6a10",
                        "job_id": "5f3c2b1a-9e8d-4c7b-a6f5-e4d3c2b1a090",
                        "duplicate": False,
                        "detected_kind": "pdf",
                        "sha256": "9f86d08…",
                        "size": 48213,
                        "warnings": [],
                    }
                ]
            }
        }
    )


# ---------------------------------------------------------------- documents and jobs


class JobView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: JobStatus
    stage: str | None
    attempts: int
    pages_done: int
    pages_total: int | None
    segments_done: int
    segments_total: int | None
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    # LLM usage over all attempts (tokens as reported by the provider; cached included)
    llm_calls: int = 0
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0
    llm_cached_input_tokens: int = 0


class InvoiceSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    segment_index: int
    page_start: int | None
    page_end: int | None
    invoice_number: str | None
    invoice_date: date | None
    supplier_name: str | None
    total: Decimal | None
    currency: str | None
    needs_review: bool


class JobProgress(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    status: JobStatus
    stage: str | None
    pages_done: int
    pages_total: int | None
    segments_done: int
    segments_total: int | None
    attempts: int


class DocumentSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    status: DocumentStatus
    mime_type: str
    size_bytes: int
    page_count: int
    created_at: datetime
    invoice: InvoiceSummary | None = None  # the first invoice (most documents have one)
    invoice_count: int = 0  # a batch PDF can hold many invoices
    needs_review: bool = False  # True if ANY invoice needs review
    job: JobProgress | None = None  # latest job, for progress in lists


class DocumentDetail(DocumentSummary):
    sha256: str
    error: str | None
    updated_at: datetime
    latest_job: JobView | None = None


class DocumentStatusView(BaseModel):
    """Small and cheap, for polling every second or two."""

    document_id: uuid.UUID
    status: DocumentStatus
    error: str | None
    job_id: uuid.UUID | None = None
    job_status: JobStatus | None = None
    stage: str | None = None
    pages_done: int = 0
    pages_total: int | None = None
    segments_done: int = 0  # invoices extracted so far
    segments_total: int | None = None  # invoices found in the document
    attempts: int = 0


class JobAccepted(BaseModel):
    document_id: uuid.UUID
    job_id: uuid.UUID
    status: JobStatus = JobStatus.QUEUED


class RetryFailedResponse(BaseModel):
    requeued: int
    job_ids: list[uuid.UUID]


# ---------------------------------------------------------------- results


class FieldView(BaseModel):
    value: Any = None  # money and dates as strings ("1320.00", "2026-03-15")
    raw_value: str | None = None
    status: Literal["found", "missing", "uncertain", "corrected"] = "missing"
    evidence: str | None = None
    page: int | None = None
    confidence: float = 0.0
    reasons: list[str] = Field(default_factory=list)


class LineItemView(BaseModel):
    position: int
    description: FieldView
    quantity: FieldView
    unit_price: FieldView
    line_total: FieldView


class IssueView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    severity: Literal["error", "warning"]
    field: str | None
    message: str
    expected: str | None
    actual: str | None


class DuplicateLink(BaseModel):
    document_id: uuid.UUID
    code: Literal["DUPLICATE_INVOICE", "POSSIBLE_DUPLICATE"]


class InvoiceResult(BaseModel):
    invoice_id: uuid.UUID
    document_id: uuid.UUID
    segment_index: int  # 0-based position of this invoice in the document
    page_start: int | None
    page_end: int | None
    extracted: bool  # False while a (re)processing job has not reached this invoice yet
    needs_review: bool
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    review_override_reason: str | None = None
    overall_confidence: float
    extracted_at: datetime
    fields: dict[str, FieldView]
    line_items: list[LineItemView]
    issues: list[IssueView]
    duplicates: list[DuplicateLink]


# ---------------------------------------------------------------- corrections

Money = Annotated[Decimal, Field(max_digits=18, decimal_places=2)]
Quantity = Annotated[Decimal, Field(max_digits=18, decimal_places=4)]


class LineItemPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position: int = Field(ge=1, description="1-based line number from the result")
    description: str | None = Field(default=None, max_length=2000)
    quantity: Quantity | None = None
    unit_price: Quantity | None = None
    line_total: Money | None = None


class InvoicePatch(BaseModel):
    """Only the fields you send are changed. Send money as strings ("1320.00") to avoid
    float rounding in JSON; dates as ISO "YYYY-MM-DD". null clears a field."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "corrected_by": "reviewer@example.com",
                "total": "1320.00",
                "invoice_date": "2026-03-15",
                "line_items": [{"position": 1, "line_total": "1200.00"}],
            }
        },
    )

    corrected_by: str = Field(min_length=1, max_length=255)
    invoice_number: str | None = Field(default=None, max_length=100)
    invoice_date: date | None = None
    due_date: date | None = None
    supplier_name: str | None = Field(default=None, max_length=500)
    customer_name: str | None = Field(default=None, max_length=500)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    subtotal: Money | None = None
    tax: Money | None = None
    total: Money | None = None
    line_items: list[LineItemPatch] = Field(default_factory=list)


class PageInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    page_number: int
    source: str  # text_layer | ocr | sheet
    ocr_confidence: float | None
    text: str  # sheets: tab-separated rows (first rows may be a preamble)
    has_image: bool  # False for spreadsheets: show `text` as a table instead


class CorrectionView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    field: str
    old_value: str | None
    new_value: str | None
    corrected_by: str
    corrected_at: datetime


class ApproveRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "reviewed_by": "reviewer@example.com",
                "override_reason": "Supplier confirmed the total by phone.",
            }
        },
    )

    reviewed_by: str = Field(min_length=1, max_length=255)
    # Required (non-empty) when error-level issues remain; stored for the audit trail.
    override_reason: str | None = Field(default=None, max_length=2000)


class DocumentResult(BaseModel):
    """Every invoice found in one document, in page order."""

    document_id: uuid.UUID
    invoices: list[InvoiceResult]


# ---------------------------------------------------------------- ledgers

LedgerField = Literal["invoice_number", "invoice_date", "supplier_name", "total", "tax", "currency"]


class LedgerSheetView(BaseModel):
    name: str
    page: int  # the sheet's page number (as in /pages)
    is_ledger: bool
    source: Literal["auto", "manual"]
    columns: dict[str, str | None]  # field -> column header (None = not mapped)
    available_columns: list[str]
    row_count: int


class LedgerRowView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sheet_name: str
    row_number: int  # as shown in Excel
    invoice_number: str | None
    invoice_date: date | None
    supplier_name: str | None
    total: Decimal | None
    tax: Decimal | None
    currency: str | None
    problems: list[str]
    cells: dict[str, Any]


class LedgerView(BaseModel):
    sheets: list[LedgerSheetView]
    rows: Page[LedgerRowView]  # rows of ledger sheets only


class ColumnMappingUpdate(BaseModel):
    """Fix a wrong mapping. Only the fields you send change; null un-maps a field."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "sheet": "Invoices",
                "columns": {"total": "Gross (BDT)", "tax": None},
            }
        },
    )

    sheet: str = Field(min_length=1, max_length=255)
    columns: dict[LedgerField, str | None] = Field(default_factory=dict)
    is_ledger: bool | None = None  # None = decide from the mapping


# ---------------------------------------------------------------- cross-checks


class CrossCheckCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_a_id: uuid.UUID
    document_b_id: uuid.UUID


class DocumentRef(BaseModel):
    id: uuid.UUID
    filename: str


class CrossCheckView(BaseModel):
    id: uuid.UUID
    document_a: DocumentRef  # always the invoices side
    document_b: DocumentRef  # a ledger, or another document with invoices
    mode: Literal["invoices_vs_ledger", "invoices_vs_invoices"] | None
    status: JobStatus
    error: str | None
    summary: dict[str, int]  # matched, mismatched, missing_in_a, missing_in_b, ...
    created_at: datetime
    finished_at: datetime | None


class FindingView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kind: str  # matched, amount_mismatch, date_mismatch, missing_in_ledger, ...
    severity: Literal["info", "warning", "error"]
    field: str | None
    value_a: str | None
    value_b: str | None
    message: str
    # Evidence on each side: an invoice page, or a ledger sheet row.
    invoice_a_id: uuid.UUID | None
    invoice_b_id: uuid.UUID | None
    segment_a: int | None
    segment_b: int | None
    page_a: int | None
    page_b: int | None
    sheet_b: str | None
    row_b: int | None


class CrossCheckDetail(CrossCheckView):
    findings: list[FindingView]


# ---------------------------------------------------------------- assistant


class Attachment(BaseModel):
    document_id: uuid.UUID
    filename: str = Field(max_length=255)


class MessageCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "content": "How much did we spend with ACME in March?",
                "attachments": [],
            }
        },
    )

    content: str = Field(min_length=1, max_length=4000)
    attachments: list[Attachment] = Field(default_factory=list, max_length=20)


class ConversationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(None, max_length=200)


class ActionView(BaseModel):
    id: uuid.UUID
    tool: str
    summary: str
    status: Literal["pending", "confirmed", "cancelled", "expired", "failed"]
    expires_at: datetime
    result: dict[str, Any] | None = None
    error: str | None = None


class MessageView(BaseModel):
    id: uuid.UUID
    role: Literal["user", "assistant"]
    content: str
    created_at: datetime
    citations: list[dict[str, Any]] = Field(default_factory=list)
    tools_used: list[dict[str, Any]] = Field(default_factory=list)
    actions: list[ActionView] = Field(default_factory=list)
    attachments: list[dict[str, Any]] = Field(default_factory=list)


class ConversationView(BaseModel):
    id: uuid.UUID
    title: str | None
    created_at: datetime
    updated_at: datetime


class ConversationDetail(ConversationView):
    messages: list[MessageView]


class Stats(BaseModel):
    documents: int
    documents_by_status: dict[str, int]
    invoices: int
    invoices_need_review: int
    jobs_running: int
    jobs_queued: int
    documents_failed: int
    spreadsheets: int
    cross_checks: int


# ---------------------------------------------------------------- evaluation


class EvalRunView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    run_at: datetime
    dataset: str
    split: str
    provider: str
    model: str
    results_file: str
    # Flat numbers, e.g. field_accuracy, false_fill_rate, line_item_f1, flag_precision,
    # flag_recall, document_success_rate, p50_seconds_per_document, cost_usd_per_document
    headline: dict[str, Any]
