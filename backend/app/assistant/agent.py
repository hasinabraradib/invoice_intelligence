"""One assistant turn: the model picks tools, we run them, results go back, repeat.

At most MAX_TOOL_STEPS tool rounds per user message; after that the model must answer with
what it has (it is called without tools). Everything (user text, tool calls, tool results,
the answer) is stored as messages, so each answer can be traced to the data it used.
Events are emitted as they happen, for the Server-Sent Events stream.
"""

import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

import anyio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.assistant.prompt import system_prompt, user_content, wrap_tool_result
from app.assistant.tools import ToolContext, execute, specs
from app.config import Settings
from app.extraction.llm import LLMClient, LLMError, Message, ToolCall
from app.models import ChatMessage, Conversation
from app.search.embedder import Embedder

logger = logging.getLogger(__name__)

MAX_TOOL_STEPS = 6
HISTORY_MESSAGES = 40  # older messages are left out of the model's context


@dataclass
class Event:
    type: str  # token | tool_call | tool_result | pending_action | citations | done | error
    data: Any


Emit = Callable[[Event], Awaitable[None]]


async def _next_position(session: AsyncSession, conversation_id: uuid.UUID) -> int:
    last = await session.scalar(
        select(func.max(ChatMessage.position)).where(ChatMessage.conversation_id == conversation_id)
    )
    return (last or 0) + 1


def _as_model_message(m: ChatMessage) -> Message:
    if m.role == "tool":
        return {
            "role": "tool",
            "tool_call_id": m.tool_call_id,
            "name": m.tool_name,
            "content": m.content,
        }
    if m.role == "assistant":
        return {"role": "assistant", "content": m.content, "tool_calls": m.tool_calls or []}
    return {
        "role": "user",
        "content": user_content(m.content, (m.meta or {}).get("attachments", [])),
    }


async def _history(session: AsyncSession, conversation_id: uuid.UUID) -> list[Message]:
    rows = list(
        await session.scalars(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation_id)
            .order_by(ChatMessage.position.desc())
            .limit(HISTORY_MESSAGES)
        )
    )[::-1]
    while rows and rows[0].role == "tool":  # never start with a result whose call was cut off
        rows.pop(0)
    return [_as_model_message(m) for m in rows]


def _chunks(text: str) -> list[str]:
    """Split the answer into small pieces (words with their spaces) for the token stream."""
    return re.findall(r"\S+\s*|\s+", text)


def _relevant(citations: list[dict], answer: str) -> list[dict]:
    """Keep citations the answer actually mentions (file name or invoice number)."""
    unique = list(
        {(c.get("document_id"), c.get("invoice_id"), c.get("page")): c for c in citations}.values()
    )
    mentioned = [
        c
        for c in unique
        if (c.get("filename") and c["filename"] in answer)
        or (c.get("invoice_number") and c["invoice_number"] in answer)
    ]
    return (mentioned or unique)[:8]


async def run_turn(
    session: AsyncSession,
    conversation: Conversation,
    text: str,
    attachments: list[dict[str, Any]],
    llm: LLMClient,
    settings: Settings,
    embedder: Embedder | None,
    emit: Emit,
) -> ChatMessage:
    position = await _next_position(session, conversation.id)
    session.add(
        ChatMessage(
            conversation_id=conversation.id,
            position=position,
            role="user",
            content=text,
            meta={"attachments": attachments},
        )
    )
    if not conversation.title:
        conversation.title = text.strip()[:80] or "New conversation"
    await session.commit()

    messages: list[Message] = [
        {"role": "system", "content": system_prompt(date.today().isoformat())}
    ]
    messages += await _history(session, conversation.id)
    ctx = ToolContext(
        session=session,
        settings=settings,
        conversation_id=conversation.id,
        user_message=text,
        embedder=embedder,
    )
    tools = specs()
    used: list[dict[str, Any]] = []
    citations: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []

    async def save(**values: Any) -> ChatMessage:
        nonlocal position
        position += 1
        message = ChatMessage(conversation_id=conversation.id, position=position, **values)
        session.add(message)
        await session.commit()  # saved step by step: the trace survives a "Stop"
        return message

    answer = ""
    for step in range(MAX_TOOL_STEPS + 1):
        allow_tools = step < MAX_TOOL_STEPS
        try:
            reply = await anyio.to_thread.run_sync(
                llm.chat_with_tools, messages, tools if allow_tools else []
            )
        except LLMError as exc:
            answer = (
                f"Sorry, I cannot reach the language model right now ({exc}). "
                "The other pages of the app still work."
            )
            await emit(Event("error", {"message": str(exc)}))
            break
        if not (reply.tool_calls and allow_tools):
            answer = reply.text.strip()
            if step == MAX_TOOL_STEPS and reply.tool_calls:
                answer = answer or (
                    "I needed more steps than allowed for one question. "
                    "Could you ask something more specific?"
                )
            break
        calls = [{"id": c.id, "name": c.name, "arguments": c.arguments} for c in reply.tool_calls]
        await save(role="assistant", content=reply.text, tool_calls=calls)
        messages.append({"role": "assistant", "content": reply.text, "tool_calls": calls})
        for call in reply.tool_calls:
            await emit(Event("tool_call", {"name": call.name, "arguments": call.arguments}))
            outcome = await execute(ctx, call)
            await session.commit()  # a proposed action is stored even if the turn stops
            used.append(
                {
                    "name": call.name,
                    "arguments": call.arguments,
                    "ok": outcome.ok,
                    "summary": outcome.summary,
                }
            )
            await emit(
                Event(
                    "tool_result", {"name": call.name, "ok": outcome.ok, "summary": outcome.summary}
                )
            )
            if outcome.action:
                actions.append(outcome.action)
                await emit(Event("pending_action", outcome.action))
            citations += outcome.citations
            content = wrap_tool_result(call.name, {"ok": outcome.ok, **outcome.data})
            await save(role="tool", content=content, tool_call_id=call.id, tool_name=call.name)
            messages.append(
                {"role": "tool", "tool_call_id": call.id, "name": call.name, "content": content}
            )

    if not answer:
        answer = "I could not find anything for that."
    for piece in _chunks(answer):
        await emit(Event("token", {"text": piece}))
    shown = _relevant(citations, answer)
    await emit(Event("citations", shown))
    final = await save(
        role="assistant",
        content=answer,
        meta={"citations": shown, "tools_used": used, "actions": actions},
    )
    await emit(
        Event("done", {"message_id": str(final.id), "tools_used": [u["name"] for u in used]})
    )
    return final


__all__ = ["MAX_TOOL_STEPS", "Event", "ToolCall", "run_turn"]
