"""PATCH /invoices/{id}: human corrections, with an audit trail and re-validation."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ERRORS, ApiError, not_found
from app.api.schemas import InvoicePatch, InvoiceResult
from app.api.views import invoice_result
from app.config import Settings, get_settings
from app.db import get_session
from app.invoices import apply_corrections, revalidate
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
