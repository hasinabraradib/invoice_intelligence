"""process_document: parse -> OCR -> extract -> validate -> duplicate check -> save -> embed.

Idempotent (CLAUDE.md rule 4): running it again for the same document replaces that
document's invoice, line items, issues and page texts in ONE transaction; nothing is
added twice, and human corrections are kept. The arq worker calls this per job.

Errors are sorted into two kinds:
- permanent (corrupt file, unsupported type, password, bad LLM output): fail at once;
- temporary (LLM rate limit/outage, timeouts, DB connection trouble): the job goes back
  to "queued" and the worker retries it later, up to JOB_MAX_ATTEMPTS.
"""

import functools
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import anyio
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.extraction.llm import LLMClient
from app.extraction.prompt import source_pages
from app.extraction.schema import ALL_FIELDS
from app.extraction.service import extract_invoice
from app.invoices import apply_review_state, keep_corrections
from app.models import DocumentStatus, JobStatus
from app.ocr.engine import OcrOptions
from app.ocr.pipeline import ocr_document
from app.parsing.dispatch import parse_document
from app.parsing.errors import DocumentError
from app.parsing.models import ParseLimits
from app.repository import Repository
from app.search.embedder import Embedder
from app.search.indexing import index_document
from app.validation.duplicates import find_duplicate_issues
from app.validation.rules import validate_invoice

logger = logging.getLogger(__name__)

# Exceptions that mean "try again later", not "this document is broken".
TEMPORARY_ERRORS = (TimeoutError, ConnectionError, OperationalError, InterfaceError)


class PipelineError(Exception):
    """A clean, expected failure with a short message for the user."""

    def __init__(self, message: str, *, temporary: bool = False) -> None:
        super().__init__(message)
        self.temporary = temporary


@dataclass(frozen=True)
class ProcessOutcome:
    document_id: uuid.UUID
    status: DocumentStatus
    job_id: uuid.UUID | None = None
    invoice_id: uuid.UUID | None = None
    issue_count: int = 0
    error: str | None = None
    retry: bool = False  # temporary failure: the worker should try again later
    attempts: int = 0


async def process_document(
    document_id: uuid.UUID,
    session: AsyncSession,
    llm: LLMClient,
    settings: Settings | None = None,
    *,
    job_id: uuid.UUID | None = None,
    embedder: Embedder | None = None,
) -> ProcessOutcome:
    settings = settings or get_settings()
    repo = Repository(session)
    document = await repo.get_document(document_id)
    if document is None:
        raise ValueError(f"document {document_id} not found")

    job = await repo.get_job(job_id) if job_id else await repo.create_job(document_id)
    job_id = job.id  # plain value: safe to use even after a rollback expires `job`
    attempts = job.attempts + 1
    await repo.update_job(
        job_id,
        status=JobStatus.RUNNING,
        attempts=attempts,
        stage=None,
        error=None,
        started_at=datetime.now(UTC),
        finished_at=None,
    )
    await repo.set_document_status(document_id, DocumentStatus.PROCESSING)
    await session.commit()

    async def stage(name: str, **progress: int | None) -> None:
        await repo.update_job(job_id, stage=name, **progress)
        await session.commit()  # visible to pollers right away

    try:
        path = Path(document.storage_path)
        # CPU-heavy, synchronous work runs in a thread so the event loop stays free.
        await stage("parsing")
        parsed = await anyio.to_thread.run_sync(
            functools.partial(
                parse_document,
                path,
                document.filename,
                ParseLimits(
                    max_pdf_pages=settings.max_pdf_pages,
                    max_sheet_rows=settings.max_sheet_rows,
                    max_sheet_columns=settings.max_sheet_columns,
                ),
                run_ocr=False,
            )
        )
        already = len(parsed.pages) - parsed.pages_needing_ocr
        await stage("parsing", pages_total=len(parsed.pages), pages_done=already)

        if settings.ocr_enabled and parsed.pages_needing_ocr:
            await stage("ocr")

            def progress(done: int) -> None:  # called from the OCR thread
                anyio.from_thread.run(functools.partial(stage, "ocr", pages_done=already + done))

            parsed = await anyio.to_thread.run_sync(
                ocr_document,
                path,
                parsed,
                OcrOptions(
                    langs=settings.ocr_langs,
                    min_confidence=settings.ocr_min_confidence,
                    page_timeout_seconds=settings.ocr_page_timeout_seconds,
                    dpi=settings.ocr_dpi,
                ),
                progress,
            )

        await stage("extracting", pages_done=len(parsed.pages))
        result = await anyio.to_thread.run_sync(
            functools.partial(extract_invoice, parsed, llm, chunk_tokens=settings.llm_chunk_tokens)
        )
        if not result.ok:
            raise PipelineError(
                f"extraction failed: {result.failure_reason}", temporary=result.retryable
            )

        await stage("validating")
        existing = await repo.get_invoice_by_document(document_id)
        if existing:  # never overwrite what a human corrected
            keep_corrections(result, existing, await repo.get_line_items(existing.id))
        report = validate_invoice(
            result, tolerance=settings.money_tolerance, review_confidence=settings.review_confidence
        )
        duplicates = await find_duplicate_issues(
            repo,
            document_id=document_id,
            supplier_key=result.supplier_key,
            invoice_number=_text(result, "invoice_number"),
            invoice_date=result.fields["invoice_date"].value or None,
            total=result.fields["total"].value,
        )
        issues = report.issues + duplicates
        needs_review = apply_review_state(
            existing, issues, report.needs_review or any(i.severity == "error" for i in duplicates)
        )
        status = DocumentStatus.NEEDS_REVIEW if needs_review else DocumentStatus.PROCESSED

        await repo.update_job(job_id, stage="saving")
        invoice = await repo.save_extraction(
            document_id=document_id,
            result=result,
            issues=issues,
            needs_review=needs_review,
            overall_confidence=_overall_confidence(result),
            pages=source_pages(parsed),
        )
        invoice_id = invoice.id
        await repo.set_document_status(document_id, status, page_count=parsed.page_count)
        await session.commit()  # the single transaction that replaces everything
    except DocumentError as exc:
        return await record_failure(
            session,
            document_id,
            job_id,
            exc.message,
            temporary=False,
            attempts=attempts,
            settings=settings,
        )
    except PipelineError as exc:
        return await record_failure(
            session,
            document_id,
            job_id,
            str(exc),
            temporary=exc.temporary,
            attempts=attempts,
            settings=settings,
        )
    except TEMPORARY_ERRORS as exc:
        return await record_failure(
            session,
            document_id,
            job_id,
            f"temporary error ({type(exc).__name__})",
            temporary=True,
            attempts=attempts,
            settings=settings,
        )
    except Exception as exc:  # a bug: log the traceback (no document text), fail cleanly
        logger.exception("processing crashed for document %s", document_id)
        return await record_failure(
            session,
            document_id,
            job_id,
            f"internal error ({type(exc).__name__})",
            temporary=False,
            attempts=attempts,
            settings=settings,
        )

    # The result is saved. Embedding is a bonus for search: if it fails, the document
    # keeps its result and the job succeeds with a warning.
    note = None
    if embedder is not None:
        try:
            await stage("embedding")
            await index_document(
                session,
                document_id,
                embedder,
                chunk_tokens=settings.embed_chunk_tokens,
                overlap_tokens=settings.embed_chunk_overlap,
            )
            await session.commit()
        except Exception as exc:
            await session.rollback()
            logger.warning("embedding failed for document %s: %s", document_id, type(exc).__name__)
            note = (
                f"warning: semantic search index not updated ({type(exc).__name__}); "
                "keyword search still works"
            )
    await repo.finish_job(job_id, note=note)
    await session.commit()

    logger.info("document %s processed: status=%s issues=%d", document_id, status, len(issues))
    return ProcessOutcome(document_id, status, job_id, invoice_id, len(issues), attempts=attempts)


async def record_failure(
    session: AsyncSession,
    document_id: uuid.UUID,
    job_id: uuid.UUID,
    message: str,
    *,
    temporary: bool,
    attempts: int,
    settings: Settings,
) -> ProcessOutcome:
    """Roll back this run, then either schedule a retry (temporary error with attempts
    left) or mark the document and job as failed."""
    await session.rollback()  # drop any half-written rows of this run
    repo = Repository(session)
    message = message[:500]
    await repo.update_job(job_id, attempts=attempts)  # the rollback may have undone it
    retry = temporary and attempts < settings.job_max_attempts
    if retry:
        await repo.update_job(
            job_id, status=JobStatus.QUEUED, error=f"{message} (will retry, attempt {attempts})"
        )
        status = DocumentStatus.PROCESSING
    else:
        await repo.set_document_status(document_id, DocumentStatus.FAILED, error=message)
        await repo.finish_job(job_id, error=message)
        status = DocumentStatus.FAILED
    await session.commit()
    logger.warning("document %s %s: %s", document_id, "will retry" if retry else "failed", message)
    return ProcessOutcome(
        document_id, status, job_id, error=message, retry=retry, attempts=attempts
    )


def _text(result, name: str) -> str | None:
    value = result.fields[name].value
    return value if isinstance(value, str) else None


def _overall_confidence(result) -> float:
    """Mean confidence of the 9 header fields (missing counts as 0)."""
    return round(sum(result.fields[n].confidence for n in ALL_FIELDS) / len(ALL_FIELDS), 3)
