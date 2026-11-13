"""PATCH /invoices/{id}: human corrections, with an audit trail and re-validation."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ERRORS, ApiError, not_found
from app.api.schemas import ApproveRequest, CorrectionView, InvoicePatch, InvoiceResult
from app.api.views import invoice_result
from app.config import Settings, get_settings
from app.db import get_session
from app.invoices import ApprovalRefused, apply_corrections, approve, revalidate
from app.repository import Repository

router = APIRouter(prefix="/invoices", tags=["invoices"])


@router.patch(
    "/{invoice_id}",
    response_model=InvoiceResult,
    responses=ERRORS,
    summary="Correct invoice fields",
)
async def correct_invoice(
    invoice_id: uuid.UUID,
    patch: InvoicePatch,
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> InvoiceResult:
    """Each change is stored in the `corrections` audit table (old and new value, who),
    the field's status becomes "corrected", validation and duplicate checks run again,
    and later re-processing will not overwrite it."""
    repo = Repository(session)
    invoice = await repo.get_invoice(invoice_id)
    if invoice is None:
        raise not_found("invoice")

    header = {
        name: getattr(patch, name)
        for name in patch.model_fields_set
        if name not in ("corrected_by", "line_items")
    }
    positions = {item.position for item in await repo.get_line_items(invoice.id)}
    line_items = {}
    for item in patch.line_items:
        if item.position not in positions:
            raise ApiError(422, "unknown_line_item", f"Line item {item.position} does not exist.")
        line_items[item.position] = {
            n: getattr(item, n) for n in item.model_fields_set if n != "position"
        }

    await apply_corrections(repo, invoice, header, line_items, patch.corrected_by)
    await revalidate(repo, invoice, settings)
    await session.commit()
    return invoice_result(
        invoice, await repo.get_line_items(invoice.id), await repo.get_issues(invoice.id)
    )


@router.get(
    "/{invoice_id}/corrections",
    response_model=list[CorrectionView],
    responses=ERRORS,
    summary="Audit history of human changes",
)
async def list_corrections(
    invoice_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_session)]
) -> list[CorrectionView]:
    repo = Repository(session)
    if await repo.get_invoice(invoice_id) is None:
        raise not_found("invoice")
    return [CorrectionView.model_validate(c) for c in await repo.get_corrections(invoice_id)]


@router.post(
    "/{invoice_id}/approve",
    response_model=InvoiceResult,
    responses={
        **ERRORS,
        409: {"model": ERRORS[404]["model"], "description": "Errors remain and no override reason"},
    },
    summary="Approve after review",
)
async def approve_invoice(
    invoice_id: uuid.UUID,
    body: ApproveRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> InvoiceResult:
    """Clears needs_review. Refused (409) while error-level issues remain, unless an
    override_reason is given; it is stored. If a later correction or re-processing adds a
    NEW error, needs_review is set again and this approval is cleared."""
    repo = Repository(session)
    invoice = await repo.get_invoice(invoice_id)
    if invoice is None:
        raise not_found("invoice")
    try:
        issues = await approve(repo, invoice, body.reviewed_by, body.override_reason)
    except ApprovalRefused as exc:
        raise ApiError(409, "unresolved_errors", str(exc)) from exc
    await session.commit()
    return invoice_result(invoice, await repo.get_line_items(invoice.id), issues)
