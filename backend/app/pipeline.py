"""process_document: read pages -> split into invoices -> extract each -> save -> embed.

A document can hold many invoices (a 1000-page scanned batch). The steps:
1. read: parse + OCR, then save the page texts (a retry of the same job reuses them);
2. segment: find where each invoice starts (rules, LLM only for unclear pages); the
   boundaries are stored on the job so a retry resumes with the same segments;
3. extract: segments run concurrently (SEGMENT_CONCURRENCY, to respect LLM rate limits);
   each is validated, duplicate-checked and SAVED as soon as it finishes. A retry of the
   same job skips segments it already saved;
4. embed the page texts for semantic search (a failure only adds a warning).

Idempotent (CLAUDE.md rule 4): re-processing updates the invoice rows whose page range is
unchanged and replaces their line items and issues; nothing is added twice.

Errors are sorted into two kinds:
- permanent (corrupt file, unsupported type, password, bad LLM output): fail at once;
- temporary (LLM rate limit/outage, timeouts, DB connection trouble): the job goes back to
  "queued" and the worker retries it later, up to JOB_MAX_ATTEMPTS.
"""

import functools
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import anyio
from sqlalchemy import func, select
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.extraction.llm import LLMClient
from app.extraction.prompt import SourcePage, source_pages
from app.extraction.schema import ALL_FIELDS
from app.extraction.second_opinion import compare_readings, vision_triggers
from app.extraction.service import ExtractionResult, extract_from_pages
from app.extraction.vision import extract_from_images
from app.invoices import (
    apply_review_state,
    boundary_issue,
    keep_corrections,
    reconcile_segments,
    refresh_document_status,
)
from app.ledger.store import ledger_pages, save_ledgers
from app.models import DocumentStatus, Invoice, JobStatus, ValidationIssue
from app.ocr.engine import OcrOptions
from app.ocr.parallel import ocr_document_parallel
from app.parsing.errors import DocumentError
from app.parsing.models import ParseLimits
from app.procpool import StepCrashed, StepTimeout
from app.repository import Repository
from app.search.embedder import Embedder
from app.search.indexing import index_document
from app.segmentation import Segment, segment_document
from app.segmentation.classifier import llm_page_classifier
from app.steps import StepPools, default_pools, render_page_task
from app.validation.duplicates import find_duplicate_issues
from app.validation.rules import ValidationIssue as RuleIssue
from app.validation.rules import validate_invoice

logger = logging.getLogger(__name__)

# Exceptions that mean "try again later", not "this document is broken".
VISION_EXTRACTED = "VISION_EXTRACTED"
TEMPORARY_ERRORS = (TimeoutError, ConnectionError, OperationalError, InterfaceError)

Stage = Callable[..., Awaitable[None]]


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
    invoice_ids: tuple[uuid.UUID, ...] = ()
    issue_count: int = 0
    error: str | None = None
    retry: bool = False  # temporary failure: the worker should try again later
    attempts: int = 0

    @property
    def invoice_id(self) -> uuid.UUID | None:
        return self.invoice_ids[0] if self.invoice_ids else None


@dataclass
class _SegmentFailure:
    segment: Segment
    reason: str
    retryable: bool


async def process_document(
    document_id: uuid.UUID,
    session: AsyncSession,
    llm: LLMClient,
    settings: Settings | None = None,
    *,
    job_id: uuid.UUID | None = None,
    embedder: Embedder | None = None,
    pools: StepPools | None = None,
) -> ProcessOutcome:
    settings = settings or get_settings()
    pools = pools or default_pools(settings)  # CPU-heavy steps run in killable processes
    repo = Repository(session)
    document = await repo.get_document(document_id)
    if document is None:
        raise ValueError(f"document {document_id} not found")

    job = await repo.get_job(job_id) if job_id else await repo.create_job(document_id)
    job_id = job.id  # plain values: safe to use even after a rollback expires the objects
    attempts = job.attempts + 1
    stored_segments = job.segments  # saved by an earlier attempt of THIS job
    # "Retry failed" jobs continue an earlier job: its page texts, boundaries and finished
    # invoices count as this job's own.
    resume_from = job.resume_job_id
    own_jobs = {job_id} | ({resume_from} if resume_from else set())
    if not stored_segments and resume_from:
        previous = await repo.get_job(resume_from)
        stored_segments = previous.segments if previous else None
    reuse_text = document.text_job_id in own_jobs
    path, filename, page_count = Path(document.storage_path), document.filename, document.page_count
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
        if reuse_text:
            await stage("resuming")
            pages = await repo.load_source_pages(document_id)
        else:
            pages, page_count, sheets = await _read_pages(path, filename, settings, stage, pools)
            await repo.save_page_texts(document_id, pages, page_count=page_count, job_id=job_id)
            if sheets:  # spreadsheets: store rows, recognise ledgers (lists of invoices)
                await save_ledgers(session, await repo.get_document(document_id), sheets)
            await session.commit()
        # A ledger lists many invoices; it is not one invoice, so it is not sent to the LLM.
        # Cross-checks use its rows instead.
        skip_pages = ledger_pages(await repo.get_document(document_id))
        if not pages:
            raise PipelineError("the document has no text to extract from")

        rows = {i.segment_index: i for i in await repo.get_invoices(document_id)}
        if stored_segments and all(s["index"] in rows for s in stored_segments):
            segments = [Segment(**s) for s in stored_segments]
            await repo.update_job(job_id, segments=stored_segments, segments_total=len(segments))
        else:
            await stage("segmenting")
            classifier = llm_page_classifier(llm, settings.segment_classifier_batch)
            segments = await anyio.to_thread.run_sync(
                segment_document, pages, page_count, classifier
            )
            # A workbook with a ledger is a ledger document: its other sheets (notes, totals)
            # are not invoices either.
            segments = [] if skip_pages else segments
            for index, segment in enumerate(segments):
                segment.index = index
            await reconcile_segments(repo, document_id, segments)
            await repo.update_job(
                job_id, segments=[s.to_dict() for s in segments], segments_total=len(segments)
            )
            await session.commit()

        rows = {i.segment_index: i for i in await repo.get_invoices(document_id)}
        pending = [s for s in segments if rows[s.index].job_id not in own_jobs]
        finished = len(segments) - len(pending)
        if finished:
            logger.info(
                "document %s: resuming, %d of %d invoice(s) already done",
                document_id,
                finished,
                len(segments),
            )
        await stage("extracting", segments_done=finished, segments_total=len(segments))
        failures = await _extract_segments(
            session,
            repo,
            document_id,
            job_id,
            pending,
            pages,
            llm,
            settings,
            finished,
            path=path,
            pools=pools,
        )
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

    if failures:
        # Finished segments are already saved; a retry of this job redoes only these.
        details = "; ".join(
            f"invoice {f.segment.index + 1} (pages {f.segment.page_start}-{f.segment.page_end}): "
            f"{f.reason}"
            for f in sorted(failures, key=lambda f: f.segment.index)[:3]
        )
        return await record_failure(
            session,
            document_id,
            job_id,
            f"{len(failures)} of {len(segments)} invoice(s) failed: {details}",
            temporary=any(f.retryable for f in failures),
            attempts=attempts,
            settings=settings,
            partial=len(failures) < len(segments),  # the other invoices are saved
        )

    # Results are saved. Embedding is a bonus for search: if it fails, the document keeps
    # its results and the job succeeds with a warning.
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
                # In a killable process with a hard timeout per batch: a model call that
                # never returns (or fails) only costs the semantic index, never the job.
                pool=pools.embed,
                step_timeout=pools.embed_timeout,
                batch=pools.embed_batch,
            )
            await session.commit()
        except Exception as exc:
            await session.rollback()
            logger.warning("embedding failed for document %s: %s", document_id, type(exc).__name__)
            note = (
                f"warning: semantic search index not updated ({type(exc).__name__}); "
                "keyword search still works"
            )
    status = await refresh_document_status(repo, document_id)
    await repo.finish_job(job_id, note=note)
    await session.commit()

    invoice_ids = tuple(i.id for i in await repo.get_invoices(document_id))
    issue_count = (
        await session.scalar(
            select(func.count())
            .select_from(ValidationIssue)
            .where(ValidationIssue.invoice_id.in_(invoice_ids))
        )
        if invoice_ids
        else 0
    )
    logger.info(
        "document %s processed: status=%s invoices=%d issues=%d",
        document_id,
        status,
        len(invoice_ids),
        issue_count,
    )
    return ProcessOutcome(
        document_id, status, job_id, invoice_ids, issue_count or 0, attempts=attempts
    )


async def _read_pages(
    path: Path, filename: str, settings: Settings, stage: Stage, pools: StepPools
) -> tuple[list[SourcePage], int, list]:
    """Parse, then OCR the pages without text. Returns (pages, page_count, sheets)."""
    # CPU-heavy work runs in a worker process with a hard timeout: a file that makes the
    # parser hang has its process killed instead of blocking the worker.
    await stage("parsing")
    limits = ParseLimits(
        max_pdf_pages=settings.max_pdf_pages,
        max_sheet_rows=settings.max_sheet_rows,
        max_sheet_columns=settings.max_sheet_columns,
    )
    try:
        parsed = await pools.parse.run(
            pools.parse_fn, str(path), filename, limits, timeout=pools.parse_timeout
        )
    except StepTimeout as exc:  # the same file would hang again: do not retry
        raise PipelineError(
            f"reading the file took longer than {exc.seconds:g} s and was stopped"
        ) from exc
    except StepCrashed as exc:  # e.g. out of memory: may work on a retry
        raise PipelineError("the file reader stopped unexpectedly", temporary=True) from exc
    already = len(parsed.pages) - parsed.pages_needing_ocr
    await stage("parsing", pages_total=len(parsed.pages), pages_done=already)

    if settings.ocr_enabled and parsed.pages_needing_ocr:
        await stage("ocr")

        async def progress(done: int) -> None:
            await stage("ocr", pages_done=already + done)

        # Pages run in parallel worker processes; if the job times out, the cancellation
        # reaches this await and the worker processes are killed.
        parsed = await ocr_document_parallel(
            path,
            parsed,
            OcrOptions(
                langs=settings.ocr_langs,
                min_confidence=settings.ocr_min_confidence,
                page_timeout_seconds=settings.ocr_page_timeout_seconds,
                dpi=settings.ocr_dpi,
                photo_steps=tuple(s for s in settings.ocr_photo_steps.split(",") if s),
            ),
            workers=settings.ocr_workers,
            on_page=progress,
            pool=pools.ocr,  # None (inline test pools): a pool just for this document
            page_timeout=settings.ocr_task_timeout_seconds,
        )
    await stage("segmenting", pages_done=len(parsed.pages))
    pages = source_pages(parsed)
    return pages, parsed.page_count or max((p.number for p in pages), default=0), parsed.sheets


async def _extract_segments(
    session: AsyncSession,
    repo: Repository,
    document_id: uuid.UUID,
    job_id: uuid.UUID,
    segments: list[Segment],
    pages: list[SourcePage],
    llm: LLMClient,
    settings: Settings,
    already_done: int,
    *,
    path: Path,
    pools: StepPools,
) -> list[_SegmentFailure]:
    """Extract segments concurrently; save each one as soon as it is ready. A segment
    whose pages OCR could not read (confidence below OCR_MIN_CONFIDENCE) is read from the
    page images instead, when the provider can (the vision fallback)."""
    failures: list[_SegmentFailure] = []
    limiter = anyio.CapacityLimiter(settings.segment_concurrency)  # LLM calls in flight
    db_lock = anyio.Lock()  # one AsyncSession: database work is done one segment at a time
    done = already_done

    async def run(segment: Segment) -> None:
        nonlocal done
        own_pages = [p for p in pages if segment.page_start <= p.number <= segment.page_end]
        transcripts: dict[int, str] = {}
        low: list[int] = []
        try:
            result = await anyio.to_thread.run_sync(
                functools.partial(
                    extract_from_pages, own_pages, llm, chunk_tokens=settings.llm_chunk_tokens
                ),
                limiter=limiter,
            )
            # OCR'd pages get a second reading from the page images when something looks
            # off. It is compared with the OCR reading, never silently swapped in; only
            # when the OCR text gave nothing usable is the image reading used instead.
            triggers = (
                vision_triggers(own_pages, result, settings)
                if vision_allowed(own_pages, llm, settings)
                else []
            )
            if triggers:
                vision, transcripts, note = await _vision_extract(
                    own_pages, llm, settings, path, pools, limiter
                )
                if vision is None:
                    result.warnings.append(note)
                elif result.ok:
                    result.vision_triggers = triggers
                    result.second_opinion = compare_readings(result, vision)
                else:
                    vision.vision_triggers = triggers
                    result = vision
                    low = _low_pages(own_pages, settings)
        except Exception as exc:  # a bug inside extraction: this segment fails, others go on
            logger.exception("extraction crashed for segment %d", segment.index)
            failures.append(
                _SegmentFailure(segment, f"internal error ({type(exc).__name__})", False)
            )
            return
        if not result.ok:
            failures.append(
                _SegmentFailure(segment, result.failure_reason or "failed", result.retryable)
            )
            return
        async with db_lock:
            try:
                for number in low if result.source == "vision" else []:
                    if transcripts.get(number):  # the better reading, for search too
                        await repo.replace_page_text(
                            document_id, number, transcripts[number], "vision"
                        )
                await _save_segment(repo, document_id, job_id, segment, result, settings)
                done += 1
                await repo.update_job(job_id, segments_done=done)
                await session.commit()  # saved now: a later retry will not redo it
            except Exception as exc:
                await session.rollback()
                temporary = isinstance(exc, TEMPORARY_ERRORS)
                if not temporary:
                    logger.exception("saving segment %d failed", segment.index)
                failures.append(
                    _SegmentFailure(
                        segment, f"could not be saved ({type(exc).__name__})", temporary
                    )
                )

    async with anyio.create_task_group() as group:
        for segment in segments:
            group.start_soon(run, segment)
    return failures


def vision_allowed(pages: list[SourcePage], llm: LLMClient, settings: Settings) -> bool:
    """May this invoice's pages be read from their images (fallback on, the provider reads
    images, not too many pages)? What triggers a reading: app.extraction.second_opinion."""
    return (
        settings.vision_fallback_enabled
        and getattr(llm, "supports_images", False)
        and len({p.number for p in pages}) <= settings.vision_max_pages
    )


def _low_pages(pages: list[SourcePage], settings: Settings) -> list[int]:
    return sorted(
        {
            p.number
            for p in pages
            if p.ocr_confidence is not None and p.ocr_confidence < settings.ocr_min_confidence
        }
    )


async def _vision_extract(
    pages: list[SourcePage],
    llm: LLMClient,
    settings: Settings,
    path: Path,
    pools: StepPools,
    limiter: anyio.CapacityLimiter,
) -> tuple[ExtractionResult | None, dict[int, str], str | None]:
    """(result, transcripts, note). result None means: use the OCR text instead, and add
    `note` (why the vision fallback did not work) as a warning."""
    numbers = sorted({p.number for p in pages})
    try:
        images = [
            (
                number,
                await pools.parse.run(
                    render_page_task,
                    str(path),
                    number,
                    settings.vision_image_max_side,
                    timeout=pools.parse_timeout,
                ),
            )
            for number in numbers
        ]
        result, transcripts = await anyio.to_thread.run_sync(
            functools.partial(extract_from_images, images, llm), limiter=limiter
        )
    except Exception as exc:
        logger.warning("vision fallback failed: %s", type(exc).__name__)
        return None, {}, f"vision fallback failed ({type(exc).__name__}); used the OCR text"
    if not result.ok:
        logger.warning("vision fallback failed: %s", result.failure_reason)
        return None, {}, "vision fallback failed; used the OCR text"
    return result, transcripts, None


def vision_issue() -> RuleIssue:
    return RuleIssue(
        code=VISION_EXTRACTED,
        severity="warning",
        field=None,
        message="OCR could not read this invoice, so its values were read from the page "
        "image by the model. They could not be checked against OCR text: please check them.",
    )


async def _save_segment(
    repo: Repository,
    document_id: uuid.UUID,
    job_id: uuid.UUID,
    segment: Segment,
    result: ExtractionResult,
    settings: Settings,
) -> None:
    invoice = await repo.session.scalar(
        select(Invoice).where(
            Invoice.document_id == document_id, Invoice.segment_index == segment.index
        )
    )
    if not segment.recheck:  # same pages as before: human corrections still apply
        keep_corrections(result, invoice, await repo.get_line_items(invoice.id))
    report = validate_invoice(
        result, tolerance=settings.money_tolerance, review_confidence=settings.review_confidence
    )
    # Checked inside the database lock, after earlier segments were saved, so two copies
    # of one invoice in the same batch are found.
    duplicates = await find_duplicate_issues(
        repo,
        invoice_id=invoice.id,
        supplier_key=result.supplier_key,
        invoice_number=_text(result, "invoice_number"),
        invoice_date=result.fields["invoice_date"].value or None,
        total=result.fields["total"].value,
    )
    issues = report.issues + duplicates
    cross = await repo.get_cross_issues(invoice.id)  # earlier cross-check findings still apply
    needs_review = apply_review_state(
        invoice,
        [*issues, *cross],
        report.needs_review or any(i.severity == "error" for i in [*duplicates, *cross]),
    )
    if result.source == "vision":  # never grounded in OCR text: always reviewed
        issues.append(vision_issue())
        needs_review = True
    if result.second_opinion:  # OCR and the page image disagree: a human decides
        issues.extend(result.second_opinion)
        needs_review = True
    if segment.recheck:  # boundaries moved: corrections were kept but must be re-checked
        issues.append(boundary_issue(segment))
        needs_review = True
        invoice.reviewed_by = invoice.reviewed_at = invoice.review_override_reason = None
    invoice.page_start, invoice.page_end = segment.page_start, segment.page_end
    await repo.save_segment(
        invoice,
        result=result,
        issues=issues,
        needs_review=needs_review,
        overall_confidence=_overall_confidence(result),
        job_id=job_id,
    )


async def record_failure(
    session: AsyncSession,
    document_id: uuid.UUID,
    job_id: uuid.UUID,
    message: str,
    *,
    temporary: bool,
    attempts: int,
    settings: Settings,
    partial: bool = False,
) -> ProcessOutcome:
    """Roll back unsaved work, then either schedule a retry (temporary error with attempts
    left) or mark the document and job as failed. Segments already saved stay saved; if
    some invoices were saved (partial), the document becomes partially_failed instead, and
    POST /jobs/retry-failed redoes only the failed invoices."""
    await session.rollback()
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
        status = DocumentStatus.PARTIALLY_FAILED if partial else DocumentStatus.FAILED
        await repo.set_document_status(document_id, status, error=message)
        await repo.finish_job(job_id, error=message)
    await session.commit()
    logger.warning("document %s %s: %s", document_id, "will retry" if retry else "failed", message)
    return ProcessOutcome(
        document_id, status, job_id, error=message, retry=retry, attempts=attempts
    )


def _text(result: ExtractionResult, name: str) -> str | None:
    value = result.fields[name].value
    return value if isinstance(value, str) else None


def _overall_confidence(result: ExtractionResult) -> float:
    """Mean confidence of the 9 header fields (missing counts as 0)."""
    return round(sum(result.fields[n].confidence for n in ALL_FIELDS) / len(ALL_FIELDS), 3)
