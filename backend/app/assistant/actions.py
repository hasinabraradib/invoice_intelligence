"""Run (or cancel) an action the assistant proposed, only after the user confirms it.

Confirmed actions go through the same service functions as the UI, so corrections land in
the audit trail like any other (corrected_by = "assistant (confirmed by user)").
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import ActionStatus, AssistantAction, ChatMessage, Invoice

CONFIRMED_BY = "assistant (confirmed by user)"


class ActionError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def effective_status(action: AssistantAction) -> ActionStatus:
    """Pending actions past their expiry count as expired (even before anyone touches them)."""
    if action.status == ActionStatus.PENDING and datetime.now(UTC) > action.expires_at:
        return ActionStatus.EXPIRED
    return action.status


async def _pending(session: AsyncSession, action_id: uuid.UUID) -> AssistantAction:
    action = await session.get(AssistantAction, action_id, with_for_update=True)
    if action is None:
        raise ActionError(404, "not_found", "action not found")
    status = effective_status(action)
    if status == ActionStatus.EXPIRED and action.status == ActionStatus.PENDING:
        action.status, action.decided_at = ActionStatus.EXPIRED, datetime.now(UTC)
        await session.commit()
    if status != ActionStatus.PENDING:
        raise ActionError(409, f"action_{status}", f"This action is {status}; ask again.")
    return action


async def _note(session: AsyncSession, action: AssistantAction, text: str) -> None:
    """Record the outcome in the conversation, so the history (and the model) knows."""
    last = await session.scalar(
        select(func.max(ChatMessage.position)).where(
            ChatMessage.conversation_id == action.conversation_id
        )
    )
    session.add(
        ChatMessage(
            conversation_id=action.conversation_id,
            position=(last or 0) + 1,
            role="assistant",
            content=text,
            meta={"action": {"id": str(action.id), "status": str(action.status)}},
        )
    )


async def cancel(session: AsyncSession, action_id: uuid.UUID) -> AssistantAction:
    action = await _pending(session, action_id)
    action.status, action.decided_at = ActionStatus.CANCELLED, datetime.now(UTC)
    await _note(session, action, f"Cancelled: {action.summary}")
    await session.commit()
    return action


async def confirm(
    session: AsyncSession, queue, settings: Settings, action_id: uuid.UUID
) -> AssistantAction:
    action = await _pending(session, action_id)
    tool, args = action.tool, dict(action.arguments)
    try:
        result = await _run(session, queue, settings, tool, args)
    except ActionError:
        raise
    except Exception as exc:
        await session.rollback()
        action = await session.get(AssistantAction, action_id)
        action.status, action.decided_at = ActionStatus.FAILED, datetime.now(UTC)
        action.error = str(exc)[:500] or type(exc).__name__
        await _note(session, action, f"Could not do it: {action.error}")
        await session.commit()
        raise ActionError(409, "action_failed", action.error) from exc
    action.status, action.decided_at, action.result = (
        ActionStatus.CONFIRMED,
        datetime.now(UTC),
        result,
    )
    await _note(session, action, f"Done: {action.summary}")
    await session.commit()
    return action


async def _run(
    session: AsyncSession, queue, settings: Settings, tool: str, args: dict[str, Any]
) -> dict[str, Any]:
    from app.api.schemas import InvoicePatch
    from app.crosscheck.service import CrossCheckError, queue_cross_check
    from app.invoices import ApprovalRefused, apply_corrections, approve, revalidate
    from app.repository import JobInProgress, Repository

    repo = Repository(session)
    if tool in ("correct_field", "approve_invoice"):
        invoice = await session.get(Invoice, uuid.UUID(args["invoice_id"]))
        if invoice is None:
            raise ValueError("the invoice no longer exists")
        if tool == "correct_field":
            name = args["field"]
            value = getattr(InvoicePatch(corrected_by=CONFIRMED_BY, **{name: args["value"]}), name)
            await apply_corrections(repo, invoice, {name: value}, {}, CONFIRMED_BY)
            await revalidate(repo, invoice, settings)
            return {"invoice_id": str(invoice.id), "field": name, "value": _text(value)}
        try:
            await approve(repo, invoice, CONFIRMED_BY, args.get("override_reason"))
        except ApprovalRefused as exc:
            raise ValueError(str(exc)) from exc
        return {"invoice_id": str(invoice.id), "approved": True}
    if tool == "reprocess_document":
        try:
            job = await repo.create_reprocess_job(
                uuid.UUID(args["document_id"]), only_failed=bool(args.get("only_failed"))
            )
        except JobInProgress as exc:
            raise ValueError(str(exc)) from exc
        await session.commit()
        await queue.enqueue(job.id)
        return {"document_id": args["document_id"], "job_id": str(job.id)}
    if tool == "run_cross_check":
        try:
            check = await queue_cross_check(
                session, queue, uuid.UUID(args["document_a_id"]), uuid.UUID(args["document_b_id"])
            )
        except CrossCheckError as exc:
            raise ValueError(exc.message) from exc
        return {"cross_check_id": str(check.id)}
    raise ValueError(f"unknown action {tool}")


def _text(value: Any) -> str | None:
    return None if value is None else str(value)
