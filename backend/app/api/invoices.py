"""PATCH /invoices/{id}: human corrections, with an audit trail and re-validation."""

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ERRORS, ApiError, not_found
from app.api.schemas import ApproveRequest, CorrectionView, InvoicePatch, InvoiceResult
from app.api.views import invoice_result
from app.config import Settings, get_settings
from app.db import get_session
from app.invoices import apply_corrections, error_keys, revalidate
from app.models import Correction, DocumentStatus
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
    issues = await repo.get_issues(invoice.id)
    errors = [i for i in issues if i.severity == "error"]
    reason = (body.override_reason or "").strip() or None
    if errors and reason is None:
        raise ApiError(
            409,
            "unresolved_errors",
            f"{len(errors)} error(s) remain. Fix them, or give an override_reason.",
        )

    invoice.needs_review = False
    invoice.reviewed_by = body.reviewed_by
    invoice.reviewed_at = datetime.now(UTC)
    invoice.review_override_reason = reason
    invoice.approved_error_keys = error_keys(issues)
    session.add(
        Correction(
            invoice_id=invoice.id,
            field="approval",
            old_value=None,
            new_value=f"approved (override: {reason})" if reason else "approved",
            corrected_by=body.reviewed_by,
        )
    )
    await repo.set_document_status(invoice.document_id, DocumentStatus.PROCESSED)
    await session.commit()
    return invoice_result(invoice, await repo.get_line_items(invoice.id), issues)
