"""Job maintenance endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_queue
from app.api.schemas import RetryFailedResponse
from app.db import get_session
from app.jobs import JobQueue
from app.repository import Repository

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post(
    "/retry-failed",
    response_model=RetryFailedResponse,
    summary="Retry every document whose last job failed (only its failed invoices)",
)
async def retry_failed(
    session: Annotated[AsyncSession, Depends(get_session)],
    queue: Annotated[JobQueue, Depends(get_queue)],
) -> RetryFailedResponse:
    repo = Repository(session)
    # Each new job continues the failed one: finished invoices (and the page texts) are
    # reused, so only what failed is done again.
    jobs = [
        await repo.create_job(doc_id, resume_job_id=failed_job_id)
        for doc_id, failed_job_id in await repo.documents_with_failed_latest_job()
    ]
    await session.commit()
    for job in jobs:
        await queue.enqueue(job.id)
    return RetryFailedResponse(requeued=len(jobs), job_ids=[job.id for job in jobs])
