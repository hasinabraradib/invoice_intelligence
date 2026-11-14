"""Numbers for the dashboard (GET /stats): daily activity, issues, the review queue and
recent uploads. Plain SQL aggregates; nothing is estimated or made up. A value with no
data behind it is None, never 0, so the UI can say "no data yet".
"""

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import Date, Float, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Document, DocumentStatus, Invoice, JobStatus, ProcessingJob, ValidationIssue

SEVERITY_ORDER = case((ValidationIssue.severity == "error", 0), else_=1)


async def dashboard_stats(
    session: AsyncSession, *, days: int = 14, today: date | None = None
) -> dict[str, Any]:
    today = today or datetime.now(UTC).date()
    first = today - timedelta(days=days - 1)
    since = datetime.combine(first, datetime.min.time(), tzinfo=UTC)

    # Uploads per day, split by each document's current status.
    day = cast(func.timezone("UTC", Document.created_at), Date)
    uploads = (
        await session.execute(
            select(day, Document.status, func.count())
            .where(Document.created_at >= since)
            .group_by(day, Document.status)
        )
    ).all()
    # Processing time per page, by the day the job finished (succeeded jobs only).
    finished = cast(func.timezone("UTC", ProcessingJob.finished_at), Date)
    seconds = func.extract("epoch", ProcessingJob.finished_at - ProcessingJob.started_at)
    per_page = seconds / func.greatest(Document.page_count, 1)
    timing = (
        await session.execute(
            select(finished, func.avg(cast(per_page, Float)))
            .join(Document, Document.id == ProcessingJob.document_id)
            .where(
                ProcessingJob.status == JobStatus.SUCCEEDED,
                ProcessingJob.finished_at >= since,
                ProcessingJob.started_at.is_not(None),
            )
            .group_by(finished)
        )
    ).all()
    overall_seconds = await session.scalar(
        select(func.avg(cast(per_page, Float)))
        .select_from(ProcessingJob)
        .join(Document, Document.id == ProcessingJob.document_id)
        .where(
            ProcessingJob.status == JobStatus.SUCCEEDED,
            ProcessingJob.finished_at >= since,
            ProcessingJob.started_at.is_not(None),
        )
    )

    daily = {
        first + timedelta(days=i): {
            "date": (first + timedelta(days=i)).isoformat(),
            "uploaded": 0,
            "processed": 0,
            "needs_review": 0,
            "failed": 0,
            "seconds_per_page": None,
        }
        for i in range(days)
    }
    for when, status, count in uploads:
        row = daily.get(when)
        if row is None:
            continue
        row["uploaded"] += count
        if status == DocumentStatus.PROCESSED:
            row["processed"] += count
        elif status == DocumentStatus.NEEDS_REVIEW:
            row["needs_review"] += count
        elif status in (DocumentStatus.FAILED, DocumentStatus.PARTIALLY_FAILED):
            row["failed"] += count
    for when, value in timing:
        if when in daily and value is not None:
            daily[when]["seconds_per_page"] = round(float(value), 2)

    confidence = await session.scalar(
        select(func.avg(Invoice.overall_confidence)).where(Invoice.job_id.is_not(None))
    )
    top_issues = (
        await session.execute(
            select(ValidationIssue.code, func.count().label("n"))
            .join(Invoice, Invoice.id == ValidationIssue.invoice_id)
            .where(Invoice.job_id.is_not(None))
            .group_by(ValidationIssue.code)
            .order_by(func.count().desc(), ValidationIssue.code)
            .limit(5)
        )
    ).all()

    queue_rows = (
        await session.execute(
            select(Invoice, Document.filename)
            .join(Document, Document.id == Invoice.document_id)
            .where(Invoice.needs_review, Invoice.job_id.is_not(None))
            .order_by(Invoice.extracted_at, Invoice.segment_index)
            .limit(5)
        )
    ).all()
    queue = []
    for invoice, filename in queue_rows:
        issue = (
            await session.execute(
                select(ValidationIssue.code, ValidationIssue.message)
                .where(ValidationIssue.invoice_id == invoice.id)
                .order_by(SEVERITY_ORDER, ValidationIssue.code)
                .limit(1)
            )
        ).first()
        queue.append(
            {
                "invoice_id": str(invoice.id),
                "document_id": str(invoice.document_id),
                "filename": filename,
                "segment_index": invoice.segment_index,
                "invoice_number": invoice.invoice_number,
                "supplier_name": invoice.supplier_name,
                "main_issue": issue.code if issue else None,
                "main_issue_message": issue.message if issue else None,
            }
        )

    recent = (
        await session.scalars(
            select(Document).order_by(Document.created_at.desc(), Document.id).limit(6)
        )
    ).all()
    seconds_avg = None if overall_seconds is None else round(float(overall_seconds), 2)
    confidence_avg = None if confidence is None else round(float(confidence), 4)
    return {
        "daily": list(daily.values()),
        "avg_seconds_per_page": seconds_avg,
        "avg_extraction_confidence": confidence_avg,
        "top_issues": [{"code": code, "count": n} for code, n in top_issues],
        "review_queue": queue,
        "recent_uploads": [
            {
                "id": str(d.id),
                "filename": d.filename,
                "status": str(d.status),
                "mime_type": d.mime_type,
                "page_count": d.page_count,
                "created_at": d.created_at.isoformat(),
            }
            for d in recent
        ],
    }
