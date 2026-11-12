"""Document endpoints: upload, list, detail, status, result, reprocess."""

import logging
import uuid
from datetime import date
from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_queue
from app.api.errors import ERRORS, ApiError, not_found
from app.api.schemas import (
    DocumentDetail,
    DocumentStatusView,
    DocumentSummary,
    FileError,
    InvoiceResult,
    JobAccepted,
    JobView,
    Page,
    UploadResponse,
    UploadResult,
)
from app.api.views import document_summary, invoice_result
from app.config import Settings, get_settings
from app.db import get_session
from app.filters import DocumentFilters
from app.jobs import JobQueue
from app.models import Document, DocumentStatus, Invoice, JobStatus
from app.parsing.errors import (
    CorruptFile,
    DocumentError,
    EmptyFile,
    FileTooLarge,
    PasswordProtected,
    UnsupportedFileType,
)
from app.parsing.file_types import detect_file_kind, extension_warning
from app.parsing.models import FileKind
from app.repository import Repository
from app.storage import StoredFile, sanitize_filename, save_upload

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/documents", tags=["documents"])

Session = Annotated[AsyncSession, Depends(get_session)]
Queue = Annotated[JobQueue, Depends(get_queue)]
AppSettings = Annotated[Settings, Depends(get_settings)]

# Error type -> HTTP status, decided here in the API layer (parsing knows nothing of HTTP).
ERROR_STATUS: dict[type[DocumentError], int] = {
    UnsupportedFileType: status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    FileTooLarge: status.HTTP_413_CONTENT_TOO_LARGE,
    CorruptFile: status.HTTP_422_UNPROCESSABLE_CONTENT,
    PasswordProtected: status.HTTP_422_UNPROCESSABLE_CONTENT,
    EmptyFile: status.HTTP_422_UNPROCESSABLE_CONTENT,
}
MULTIPART_OVERHEAD_BYTES = 1024 * 1024
MIME_TYPES = {
    FileKind.PDF: "application/pdf",
    FileKind.PNG: "image/png",
    FileKind.JPEG: "image/jpeg",
    FileKind.TIFF: "image/tiff",
    FileKind.CSV: "text/csv",
    FileKind.XLSX: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    FileKind.XLS: "application/vnd.ms-excel",
}
KIND_BY_MIME = {mime: kind for kind, mime in MIME_TYPES.items()}


# ---------------------------------------------------------------- upload


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=UploadResponse,
    summary="Upload invoices (processed in the background)",
)
async def upload_documents(
    request: Request,
    files: Annotated[list[UploadFile], File(description="One or more invoice files")],
    settings: AppSettings,
    session: Session,
    queue: Queue,
) -> UploadResponse:
    """Stores each file, creates a queued job and returns at once (202). Poll
    `GET /documents/{id}/status` for progress. Each file has its own `status_code`, so one
    bad file never fails the others. The same bytes uploaded again are not stored twice."""
    declared = request.headers.get("content-length")
    limit = settings.max_upload_bytes * settings.max_files_per_request + MULTIPART_OVERHEAD_BYTES
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Request body is too large.")
    if len(files) > settings.max_files_per_request:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Too many files: at most {settings.max_files_per_request} per request.",
        )
    repo = Repository(session)
    return UploadResponse(results=[await _accept(f, settings, repo, queue) for f in files])


async def _accept(
    upload: UploadFile, settings: Settings, repo: Repository, queue: JobQueue
) -> UploadResult:
    name = sanitize_filename(upload.filename)
    stored: StoredFile | None = None
    try:
        stored = await save_upload(upload, settings.upload_dir, settings.max_upload_bytes)
        # The hash was computed while streaming; same bytes seen before -> keep one copy.
        if existing := await repo.get_document_by_sha256(stored.sha256):
            await anyio.Path(stored.stored_path).unlink(missing_ok=True)
            return await _duplicate(name, existing, repo)
        # Only the cheap magic-byte check here; full parsing happens in the worker.
        kind = await anyio.to_thread.run_sync(detect_file_kind, stored.stored_path)
    except DocumentError as exc:
        if stored is not None:
            await anyio.Path(stored.stored_path).unlink(missing_ok=True)
        logger.info("upload rejected: code=%s", exc.code)
        return UploadResult(
            original_filename=name,
            error=FileError(code=exc.code, message=exc.message),
            status_code=ERROR_STATUS.get(type(exc), status.HTTP_422_UNPROCESSABLE_CONTENT),
            sha256=stored.sha256 if stored else None,
            size=stored.size if stored else None,
        )
    finally:
        await upload.close()

    try:
        document = await repo.create_document(
            filename=name,
            sha256=stored.sha256,
            mime_type=MIME_TYPES[kind],
            size_bytes=stored.size,
            storage_path=str(stored.stored_path),
        )
        job = await repo.create_job(document.id)
        await repo.session.commit()  # commit BEFORE enqueueing so the worker finds the rows
    except IntegrityError:
        # Two identical uploads at the same moment: the unique sha256 lets only one win.
        await repo.session.rollback()
        await anyio.Path(stored.stored_path).unlink(missing_ok=True)
        return await _duplicate(name, await repo.get_document_by_sha256(stored.sha256), repo)

    result = UploadResult(
        original_filename=name,
        status_code=status.HTTP_202_ACCEPTED,
        document_id=document.id,
        job_id=job.id,
        detected_kind=kind,
        sha256=stored.sha256,
        size=stored.size,
        warnings=[w for w in [extension_warning(kind, name)] if w],
    )
    await _enqueue_or_mark_failed(repo, queue, job.id, document.id, result)
    logger.info("upload accepted: document=%s kind=%s size=%d", document.id, kind, stored.size)
    return result


async def _enqueue_or_mark_failed(repo, queue, job_id, document_id, result=None) -> bool:
    try:
        await queue.enqueue(job_id)
        return True
    except Exception as exc:  # Redis down: keep the file, let POST /jobs/retry-failed resume
        logger.warning("enqueue failed for job %s: %s", job_id, type(exc).__name__)
        await repo.finish_job(job_id, error="queue unavailable; use POST /jobs/retry-failed")
        await repo.set_document_status(
            document_id, DocumentStatus.FAILED, error="queue unavailable"
        )
        await repo.session.commit()
        if result is not None:
            result.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            result.error = FileError(
                code="queue_unavailable", message="Stored, but could not be queued; retry later."
            )
        return False


async def _duplicate(name: str, existing: Document, repo: Repository) -> UploadResult:
    job = await repo.latest_job(existing.id)
    return UploadResult(
        original_filename=name,
        status_code=status.HTTP_200_OK,
        document_id=existing.id,
        job_id=job.id if job else None,
        duplicate=True,
        detected_kind=KIND_BY_MIME.get(existing.mime_type),
        sha256=existing.sha256,
        size=existing.size_bytes,
        warnings=["This file was already uploaded; returning the existing document."],
    )


# ---------------------------------------------------------------- reading


@router.get("", response_model=Page[DocumentSummary], summary="List documents")
async def list_documents(
    session: Session,
    status_: Annotated[DocumentStatus | None, Query(alias="status")] = None,
    needs_review: bool | None = None,
    supplier: Annotated[str | None, Query(max_length=200)] = None,
    date_from: Annotated[date | None, Query(description="Invoice date from (inclusive)")] = None,
    date_to: Annotated[date | None, Query(description="Invoice date to (inclusive)")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[DocumentSummary]:
    """Newest first. Invoice filters (supplier, dates, needs_review) only match documents
    that already have an extracted invoice."""
    filters = DocumentFilters(status_, needs_review, supplier, date_from, date_to)
    base = filters.apply(
        select(Document, Invoice).outerjoin(Invoice, Invoice.document_id == Document.id)
    )
    total = await session.scalar(select(func.count()).select_from(base.subquery()))
    rows = await session.execute(
        base.order_by(Document.created_at.desc(), Document.id).limit(limit).offset(offset)
    )
    items = [document_summary(doc, invoice) for doc, invoice in rows]
    return Page[DocumentSummary](items=items, total=total or 0, limit=limit, offset=offset)


async def _get_document(session: AsyncSession, document_id: uuid.UUID) -> Document:
    document = await session.get(Document, document_id)
    if document is None:
        raise not_found("document")
    return document


@router.get(
    "/{document_id}",
    response_model=DocumentDetail,
    responses=ERRORS,
    summary="Document metadata and latest job",
)
async def get_document(document_id: uuid.UUID, session: Session) -> DocumentDetail:
    repo = Repository(session)
    document = await _get_document(session, document_id)
    invoice = await repo.get_invoice_by_document(document_id)
    job = await repo.latest_job(document_id)
    detail = DocumentDetail.model_validate(document)
    detail.invoice = document_summary(document, invoice).invoice
    detail.latest_job = JobView.model_validate(job) if job else None
    return detail


@router.get(
    "/{document_id}/status",
    response_model=DocumentStatusView,
    responses=ERRORS,
    summary="Processing progress (for polling)",
)
async def get_status(document_id: uuid.UUID, session: Session) -> DocumentStatusView:
    document = await _get_document(session, document_id)
    job = await Repository(session).latest_job(document_id)
    view = DocumentStatusView(document_id=document.id, status=document.status, error=document.error)
    if job:
        view.job_id, view.job_status, view.stage = job.id, job.status, job.stage
        view.pages_done, view.pages_total, view.attempts = (
            job.pages_done,
            job.pages_total,
            job.attempts,
        )
        view.error = view.error or job.error
    return view


@router.get(
    "/{document_id}/result",
    response_model=InvoiceResult,
    responses=ERRORS,
    summary="Extracted invoice with evidence, issues and duplicates",
)
async def get_result(document_id: uuid.UUID, session: Session) -> InvoiceResult:
    repo = Repository(session)
    await _get_document(session, document_id)
    invoice = await repo.get_invoice_by_document(document_id)
    if invoice is None:
        raise ApiError(404, "result_not_ready", "No extraction result for this document yet.")
    return invoice_result(
        invoice, await repo.get_line_items(invoice.id), await repo.get_issues(invoice.id)
    )


# ---------------------------------------------------------------- reprocessing


@router.post(
    "/{document_id}/reprocess",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobAccepted,
    responses={
        **ERRORS,
        409: {"model": ERRORS[404]["model"], "description": "A job is already queued or running"},
    },
    summary="Process a document again",
)
async def reprocess(document_id: uuid.UUID, session: Session, queue: Queue) -> JobAccepted:
    """Safe to call any time: processing is idempotent and human corrections are kept."""
    repo = Repository(session)
    await _get_document(session, document_id)
    if await repo.has_active_job(document_id):
        raise ApiError(
            409, "job_in_progress", "A job for this document is already queued or running."
        )
    job = await repo.create_job(document_id)
    await session.commit()
    await _enqueue_or_mark_failed(repo, queue, job.id, document_id)
    return JobAccepted(document_id=document_id, job_id=job.id, status=JobStatus.QUEUED)
