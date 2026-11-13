"""Cross-verification endpoints: compare invoices with a ledger or with another PDF."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_queue
from app.api.errors import ERRORS, ApiError, not_found
from app.api.schemas import (
    CrossCheckCreate,
    CrossCheckDetail,
    CrossCheckView,
    DocumentRef,
    FindingView,
    Page,
)
from app.crosscheck.service import CrossCheckError, prepare
from app.db import get_session
from app.jobs import JobQueue
from app.models import CrossCheck, CrossCheckFinding, Document, JobStatus

router = APIRouter(prefix="/cross-checks", tags=["cross-checks"])
Session = Annotated[AsyncSession, Depends(get_session)]


async def _view(session: AsyncSession, check: CrossCheck) -> CrossCheckView:
    a = await session.get(Document, check.document_a_id)
    b = await session.get(Document, check.document_b_id)
    return CrossCheckView(
        id=check.id,
        document_a=DocumentRef(id=a.id, filename=a.filename),
        document_b=DocumentRef(id=b.id, filename=b.filename),
        mode=check.mode,
        status=check.status,
        error=check.error,
        summary=check.summary or {},
        created_at=check.created_at,
        finished_at=check.finished_at,
    )


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=CrossCheckView,
    responses={
        **ERRORS,
        409: {"model": ERRORS[404]["model"], "description": "A document is not processed yet"},
    },
    summary="Compare two documents (runs in the background)",
)
async def create_cross_check(
    body: CrossCheckCreate,
    session: Session,
    queue: Annotated[JobQueue, Depends(get_queue)],
) -> CrossCheckView:
    """Invoices vs a ledger spreadsheet, or invoices vs invoices (e.g. a purchase order or a
    second copy). The order does not matter for a ledger: invoices are always side A.
    Running the same pair again replaces the earlier results."""
    try:
        a_id, b_id, mode = await prepare(session, body.document_a_id, body.document_b_id)
    except CrossCheckError as exc:
        raise ApiError(exc.status, exc.code, exc.message) from exc
    check = await session.scalar(
        select(CrossCheck).where(CrossCheck.document_a_id == a_id, CrossCheck.document_b_id == b_id)
    )
    if check is None:
        check = CrossCheck(document_a_id=a_id, document_b_id=b_id, summary={})
        session.add(check)
    elif check.status in (JobStatus.QUEUED, JobStatus.RUNNING):
        return await _view(session, check)  # already on its way: do not queue it twice
    check.mode, check.status, check.error = mode, JobStatus.QUEUED, None
    await session.commit()
    await queue.enqueue_cross_check(check.id)
    return await _view(session, check)


@router.get("", response_model=Page[CrossCheckView], summary="List cross-checks")
async def list_cross_checks(
    session: Session,
    document_id: uuid.UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[CrossCheckView]:
    query = select(CrossCheck)
    if document_id:
        query = query.where(
            or_(CrossCheck.document_a_id == document_id, CrossCheck.document_b_id == document_id)
        )
    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    checks = await session.scalars(
        query.order_by(CrossCheck.created_at.desc(), CrossCheck.id).limit(limit).offset(offset)
    )
    items = [await _view(session, c) for c in checks]
    return Page[CrossCheckView](items=items, total=total or 0, limit=limit, offset=offset)


@router.get(
    "/{cross_check_id}",
    response_model=CrossCheckDetail,
    responses=ERRORS,
    summary="A cross-check with all findings and their evidence",
)
async def get_cross_check(cross_check_id: uuid.UUID, session: Session) -> CrossCheckDetail:
    check = await session.get(CrossCheck, cross_check_id)
    if check is None:
        raise not_found("cross-check")
    findings = await session.scalars(
        select(CrossCheckFinding)
        .where(CrossCheckFinding.cross_check_id == check.id)
        .order_by(CrossCheckFinding.position)
    )
    return CrossCheckDetail(
        **(await _view(session, check)).model_dump(),
        findings=[FindingView.model_validate(f) for f in findings],
    )
