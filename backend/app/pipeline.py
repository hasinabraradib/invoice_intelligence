"""process_document: parse -> OCR -> extract -> validate -> duplicate check -> save.

Idempotent (CLAUDE.md rule 4): running it again for the same document replaces that
document's invoice, line items, issues and page texts in ONE transaction; nothing is
added twice. The step 4b worker will call this function.
"""

import functools
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path

import anyio
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.extraction.llm import LLMClient
from app.extraction.prompt import source_pages
from app.extraction.schema import ALL_FIELDS
from app.extraction.service import extract_invoice
from app.models import DocumentStatus
from app.ocr.engine import OcrOptions
from app.ocr.pipeline import ocr_document
from app.parsing.dispatch import parse_document
from app.parsing.errors import DocumentError
from app.parsing.models import ParseLimits
from app.repository import Repository
from app.validation.duplicates import find_duplicate_issues
from app.validation.rules import validate_invoice

logger = logging.getLogger(__name__)


class PipelineError(Exception):
    """A clean, expected failure with a short message for the user."""


@dataclass(frozen=True)
class ProcessOutcome:
    document_id: uuid.UUID
    status: DocumentStatus
    invoice_id: uuid.UUID | None = None
    issue_count: int = 0
    error: str | None = None


async def process_document(
    document_id: uuid.UUID,
    session: AsyncSession,
    llm: LLMClient,
    settings: Settings | None = None,
) -> ProcessOutcome:
    settings = settings or get_settings()
    repo = Repository(session)
    document = await repo.get_document(document_id)
    if document is None:
        raise ValueError(f"document {document_id} not found")

    job_id = await repo.start_job(document_id)
    await repo.set_document_status(document_id, DocumentStatus.PROCESSING)
    await session.commit()

    async def stage(name: str) -> None:
        await repo.update_job(job_id, stage=name)
        await session.commit()  # visible to others right away (progress)

    try:
        # CPU-heavy, synchronous work runs in a thread so the event loop stays free.
        await stage("parsing")
        parsed = await anyio.to_thread.run_sync(
            functools.partial(
                parse_document,
                Path(document.storage_path),
                document.filename,
                ParseLimits(
                    max_pdf_pages=settings.max_pdf_pages,
                    max_sheet_rows=settings.max_sheet_rows,
                    max_sheet_columns=settings.max_sheet_columns,
                ),
                run_ocr=False,
            )
        )
        if settings.ocr_enabled and parsed.pages_needing_ocr:
            await stage("ocr")
            parsed = await anyio.to_thread.run_sync(
                ocr_document,
                Path(document.storage_path),
                parsed,
                OcrOptions(
                    langs=settings.ocr_langs,
                    min_confidence=settings.ocr_min_confidence,
                    page_timeout_seconds=settings.ocr_page_timeout_seconds,
                    dpi=settings.ocr_dpi,
                ),
            )

        await stage("extracting")
        result = await anyio.to_thread.run_sync(
            functools.partial(extract_invoice, parsed, llm, chunk_tokens=settings.llm_chunk_tokens)
        )
        if not result.ok:
            raise PipelineError(f"extraction failed: {result.failure_reason}")

        await stage("validating")
        report = validate_invoice(
            result, tolerance=settings.money_tolerance, review_confidence=settings.review_confidence
        )
        duplicates = await find_duplicate_issues(
            repo,
            document_id=document_id,
            supplier_key=result.supplier_key,
            invoice_number=_text(result, "invoice_number"),
            invoice_date=result.fields["invoice_date"].value or None,
            total=result.fields["total"].value or None,
        )
        issues = report.issues + duplicates
        needs_review = report.needs_review or any(i.severity == "error" for i in duplicates)
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
        await repo.set_document_status(document_id, status, page_count=parsed.page_count)
        await repo.finish_job(job_id)
        await session.commit()  # the single transaction that replaces everything
    except (DocumentError, PipelineError) as exc:
        message = exc.message if isinstance(exc, DocumentError) else str(exc)
        return await _fail(session, repo, document_id, job_id, message)
    except Exception as exc:  # a bug: log the traceback (no document text), fail cleanly
        logger.exception("processing crashed for document %s", document_id)
        return await _fail(
            session, repo, document_id, job_id, f"internal error ({type(exc).__name__})"
        )

    logger.info("document %s processed: status=%s issues=%d", document_id, status, len(issues))
    return ProcessOutcome(document_id, status, invoice.id, len(issues))


async def _fail(session, repo, document_id, job_id, message: str) -> ProcessOutcome:
    await session.rollback()  # drop any half-written rows of this run
    message = message[:500]
    await repo.set_document_status(document_id, DocumentStatus.FAILED, error=message)
    await repo.finish_job(job_id, error=message)
    await session.commit()
    logger.warning("document %s failed: %s", document_id, message)
    return ProcessOutcome(document_id, DocumentStatus.FAILED, error=message)


def _text(result, name: str) -> str | None:
    value = result.fields[name].value
    return value if isinstance(value, str) else None


def _overall_confidence(result) -> float:
    """Mean confidence of the 9 header fields (missing counts as 0)."""
    return round(sum(result.fields[n].confidence for n in ALL_FIELDS) / len(ALL_FIELDS), 3)
