"""POST /documents: upload one or more files, store them safely, and parse them.

Each file gets its own result with its own status code, so one bad file never fails the
batch. The overall response is 200 whenever the request itself was valid.
"""

import logging
from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.ocr.engine import OcrOptions
from app.parsing.dispatch import parse_document
from app.parsing.errors import (
    CorruptFile,
    DocumentError,
    EmptyFile,
    FileTooLarge,
    PasswordProtected,
    UnsupportedFileType,
)
from app.parsing.models import FileKind, ParseLimits
from app.storage import StoredFile, sanitize_filename, save_upload

logger = logging.getLogger(__name__)
router = APIRouter(tags=["documents"])

# Error type -> HTTP status, decided here in the API layer (parsing knows nothing of HTTP).
ERROR_STATUS: dict[type[DocumentError], int] = {
    UnsupportedFileType: status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    FileTooLarge: status.HTTP_413_CONTENT_TOO_LARGE,
    CorruptFile: status.HTTP_422_UNPROCESSABLE_CONTENT,
    PasswordProtected: status.HTTP_422_UNPROCESSABLE_CONTENT,
    EmptyFile: status.HTTP_422_UNPROCESSABLE_CONTENT,
}
MULTIPART_OVERHEAD_BYTES = 1024 * 1024


class FileError(BaseModel):
    code: str
    message: str


class FileResult(BaseModel):
    original_filename: str  # sanitised for display; never used as a path
    status_code: int
    detected_kind: FileKind | None = None
    sha256: str | None = None
    size: int | None = None
    page_count: int = 0
    pages_needing_ocr: int = 0  # still without text (OCR disabled or failed)
    pages_ocr: int = 0  # pages whose text came from OCR
    mean_ocr_confidence: float | None = None
    sheet_count: int = 0
    warnings: list[str] = Field(default_factory=list)
    error: FileError | None = None


class UploadResponse(BaseModel):
    results: list[FileResult]


def _check_request_size(request: Request, settings: Settings) -> None:
    # Cheap early rejection based on the declared body size. Starlette buffers the body
    # before our handler runs, so save_upload's streaming limit is the per-file guarantee;
    # this check stops obviously oversized requests before most of that work.
    declared = request.headers.get("content-length")
    limit = settings.max_upload_bytes * settings.max_files_per_request + MULTIPART_OVERHEAD_BYTES
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Request body is too large.")


@router.post("/documents", response_model=UploadResponse)
async def upload_documents(
    request: Request,
    # Annotated[...] is FastAPI's recommended style: the type and its source in one place.
    files: Annotated[list[UploadFile], File(description="One or more invoice files")],
    settings: Annotated[Settings, Depends(get_settings)],
) -> UploadResponse:
    _check_request_size(request, settings)
    if len(files) > settings.max_files_per_request:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Too many files: at most {settings.max_files_per_request} per request.",
        )
    limits = ParseLimits(
        max_pdf_pages=settings.max_pdf_pages,
        max_sheet_rows=settings.max_sheet_rows,
        max_sheet_columns=settings.max_sheet_columns,
    )
    ocr = OcrOptions(
        langs=settings.ocr_langs,
        min_confidence=settings.ocr_min_confidence,
        page_timeout_seconds=settings.ocr_page_timeout_seconds,
        dpi=settings.ocr_dpi,
    )
    results = [await _process_one(upload, settings, limits, ocr) for upload in files]
    return UploadResponse(results=results)


async def _process_one(
    upload: UploadFile, settings: Settings, limits: ParseLimits, ocr: OcrOptions
) -> FileResult:
    display_name = sanitize_filename(upload.filename)
    stored: StoredFile | None = None
    try:
        stored = await save_upload(upload, settings.upload_dir, settings.max_upload_bytes)
        # TODO(step 4): enqueue a background job instead of parsing inside the request.
        # Parsing is CPU-bound and synchronous, so it runs in a thread to keep the
        # event loop free for other requests meanwhile.
        doc = await run_in_threadpool(
            lambda: parse_document(
                stored.stored_path, display_name, limits, ocr, run_ocr=settings.ocr_enabled
            )
        )
    except DocumentError as exc:
        if stored is not None:  # do not keep files we could not use
            await anyio.Path(stored.stored_path).unlink(missing_ok=True)
        logger.info("upload rejected: code=%s size=%s", exc.code, stored.size if stored else None)
        return FileResult(
            original_filename=display_name,
            status_code=ERROR_STATUS.get(type(exc), status.HTTP_422_UNPROCESSABLE_CONTENT),
            sha256=stored.sha256 if stored else None,
            size=stored.size if stored else None,
            error=FileError(code=exc.code, message=exc.message),
        )
    except Exception:
        # A bug in a parser must not fail the other files. Log the traceback (no content).
        logger.exception("unexpected error while processing an upload")
        if stored is not None:
            await anyio.Path(stored.stored_path).unlink(missing_ok=True)
        return FileResult(
            original_filename=display_name,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            error=FileError(code="internal_error", message="Unexpected error; please retry."),
        )
    finally:
        await upload.close()

    logger.info(
        "upload parsed: sha256=%s kind=%s size=%d pages=%d sheets=%d",
        stored.sha256[:12],
        doc.file_kind,
        stored.size,
        doc.page_count,
        len(doc.sheets),
    )
    return FileResult(
        original_filename=display_name,
        status_code=status.HTTP_200_OK,
        detected_kind=doc.file_kind,
        sha256=stored.sha256,
        size=stored.size,
        page_count=doc.page_count,
        pages_needing_ocr=doc.pages_needing_ocr,
        pages_ocr=doc.pages_ocr,
        mean_ocr_confidence=_mean_confidence(doc),
        sheet_count=len(doc.sheets),
        warnings=doc.warnings + [w for sheet in doc.sheets for w in sheet.warnings],
        error=None,
    )


def _mean_confidence(doc) -> float | None:
    values = [p.ocr_confidence for p in doc.pages if p.ocr_confidence is not None]
    return round(sum(values) / len(values), 1) if values else None
