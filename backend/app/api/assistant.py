"""Chat with the assistant. Answers stream as Server-Sent Events:

    event: tool_call       data: {"name": "sum_invoices", "arguments": {...}}
    event: tool_result     data: {"name": "...", "ok": true, "summary": "..."}
    event: pending_action  data: {"id": "...", "summary": "Change the total of ...", ...}
    event: token           data: {"text": "The "}
    event: citations       data: [{"document_id": "...", "filename": "...", "page": 2, ...}]
    event: done            data: {"message_id": "...", "tools_used": [...]}
    event: error           data: {"message": "..."}

Closing the connection ("Stop generating") stops the turn; finished steps stay saved.
"""

import asyncio
import json
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_assistant_llm, get_embedder, get_queue, get_sessionmaker
from app.api.errors import ERRORS, ApiError, not_found
from app.api.schemas import (
    ActionView,
    ConversationCreate,
    ConversationDetail,
    ConversationView,
    MessageCreate,
    MessageView,
    Page,
    Stats,
)
from app.assistant import actions as action_service
from app.assistant.agent import Event, run_turn
from app.assistant.tools import stats
from app.config import Settings, get_settings
from app.db import get_session
from app.models import AssistantAction, ChatMessage, Conversation

router = APIRouter(tags=["assistant"])
Session = Annotated[AsyncSession, Depends(get_session)]


def _sse(event: Event) -> str:
    return f"event: {event.type}\ndata: {json.dumps(event.data, default=str)}\n\n"


def _action_view(action: AssistantAction) -> ActionView:
    return ActionView(
        id=action.id,
        tool=action.tool,
        summary=action.summary,
        status=action_service.effective_status(action),
        expires_at=action.expires_at,
        result=action.result,
        error=action.error,
    )


@router.get("/stats", response_model=Stats, summary="Counts for the greeting and dashboard")
async def get_stats(session: Session) -> Stats:
    return Stats(**await stats(session))


@router.post("/assistant/conversations", response_model=ConversationView, status_code=201)
async def create_conversation(body: ConversationCreate, session: Session) -> ConversationView:
    conversation = Conversation(title=body.title)
    session.add(conversation)
    await session.commit()
    return ConversationView.model_validate(conversation, from_attributes=True)


@router.get("/assistant/conversations", response_model=Page[ConversationView])
async def list_conversations(
    session: Session,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Page[ConversationView]:
    total = await session.scalar(select(func.count()).select_from(Conversation))
    rows = await session.scalars(
        select(Conversation)
        .order_by(Conversation.updated_at.desc(), Conversation.id)
        .limit(limit)
        .offset(offset)
    )
    items = [ConversationView.model_validate(c, from_attributes=True) for c in rows]
    return Page[ConversationView](items=items, total=total or 0, limit=limit, offset=offset)


@router.get(
    "/assistant/conversations/{conversation_id}",
    response_model=ConversationDetail,
    responses=ERRORS,
)
async def get_conversation(conversation_id: uuid.UUID, session: Session) -> ConversationDetail:
    conversation = await session.get(Conversation, conversation_id)
    if conversation is None:
        raise not_found("conversation")
    rows = await session.scalars(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation_id)
        .order_by(ChatMessage.position)
    )
    all_actions = {
        str(a.id): a
        for a in await session.scalars(
            select(AssistantAction).where(AssistantAction.conversation_id == conversation_id)
        )
    }
    messages = []
    for m in rows:
        if m.role not in ("user", "assistant") or (m.role == "assistant" and m.tool_calls):
            continue  # tool steps are kept for the trace, shown via tools_used
        meta = m.meta or {}
        messages.append(
            MessageView(
                id=m.id,
                role=m.role,
                content=m.content,
                created_at=m.created_at,
                citations=meta.get("citations", []),
                tools_used=meta.get("tools_used", []),
                attachments=meta.get("attachments", []),
                actions=[
                    _action_view(all_actions[a["id"]])
                    for a in meta.get("actions", [])
                    if a["id"] in all_actions
                ],
            )
        )
    return ConversationDetail(
        **ConversationView.model_validate(conversation, from_attributes=True).model_dump(),
        messages=messages,
    )


@router.post(
    "/assistant/conversations/{conversation_id}/messages",
    responses=ERRORS,
    response_class=StreamingResponse,
    summary="Send a message; the answer streams back as Server-Sent Events",
)
async def send_message(
    conversation_id: uuid.UUID,
    body: MessageCreate,
    request: Request,
    session: Session,
    sessionmaker: Annotated[Any, Depends(get_sessionmaker)],
    llm: Annotated[Any, Depends(get_assistant_llm)],
    embedder: Annotated[Any, Depends(get_embedder)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> StreamingResponse:
    if await session.get(Conversation, conversation_id) is None:
        raise not_found("conversation")
    attachments = [a.model_dump(mode="json") for a in body.attachments]
    events: asyncio.Queue[Event | None] = asyncio.Queue()

    async def emit(event: Event) -> None:
        await events.put(event)

    async def work() -> None:
        # Its own session: the request's session closes before the stream ends.
        async with sessionmaker() as turn_session:
            try:
                conversation = await turn_session.get(Conversation, conversation_id)
                await run_turn(
                    turn_session,
                    conversation,
                    body.content,
                    attachments,
                    llm,
                    settings,
                    embedder,
                    emit,
                )
            except Exception as exc:  # never leave the client waiting on a broken stream
                await emit(Event("error", {"message": f"internal error ({type(exc).__name__})"}))
            finally:
                await events.put(None)

    async def stream():
        task = asyncio.create_task(work())
        try:
            while (event := await events.get()) is not None:
                yield _sse(event)
        finally:
            task.cancel()  # the client went away ("Stop generating"): stop the turn

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post(
    "/assistant/actions/{action_id}/confirm",
    response_model=ActionView,
    responses=ERRORS,
    summary="Run a change the assistant proposed",
)
async def confirm_action(
    action_id: uuid.UUID,
    session: Session,
    queue: Annotated[Any, Depends(get_queue)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ActionView:
    try:
        action = await action_service.confirm(session, queue, settings, action_id)
    except action_service.ActionError as exc:
        raise ApiError(exc.status, exc.code, exc.message) from exc
    return _action_view(action)


@router.post(
    "/assistant/actions/{action_id}/cancel",
    response_model=ActionView,
    responses=ERRORS,
    summary="Drop a proposed change",
)
async def cancel_action(action_id: uuid.UUID, session: Session) -> ActionView:
    try:
        action = await action_service.cancel(session, action_id)
    except action_service.ActionError as exc:
        raise ApiError(exc.status, exc.code, exc.message) from exc
    return _action_view(action)
