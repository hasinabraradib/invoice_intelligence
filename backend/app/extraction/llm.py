"""LLM clients that return JSON. Called over plain HTTPS with httpx (no vendor SDKs):
fewer dependencies, and we control timeouts and retries ourselves.

Security: the API key travels in a request header (never in the URL, which could be
logged), and we never log prompts or responses.
"""

import base64
import contextlib
import json
import logging
import random
import re
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

DEFAULT_MODELS = {
    "gemini": "gemini-2.5-flash",
    "groq": "openai/gpt-oss-120b",
    "openai": "gpt-6-luna",  # reasoning model; with reasoning_effort "none" takes temperature 0
}


class LLMError(Exception):
    """The provider could not give us an answer (network, HTTP error, blocked reply).

    retryable=True means "temporary": rate limit, server error, timeout or network
    trouble. A later job retry may succeed; a bad key or bad request will not.
    """

    def __init__(
        self, message: str, *, retryable: bool = False, status_code: int | None = None
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code  # HTTP status when the provider rejected the request


class LLMInvalidJSON(LLMError):
    """The provider answered, but not with a JSON object."""


@dataclass
class ToolSpec:
    """A function the model may call: name, what it does, and a JSON schema of arguments."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] | None  # None if the model sent arguments that are not JSON
    raw_arguments: str = ""


@dataclass
class ChatReply:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


@dataclass
class TokenUsage:
    """Tokens billed by the provider, summed over successful calls (for cost estimates).

    Failed attempts (429, 5xx) are not billed by the providers, so they are not counted.
    """

    calls: int = 0
    input_tokens: int = 0  # all prompt tokens, cached ones included
    output_tokens: int = 0
    cached_input_tokens: int = 0  # prompt-cache hits (usually billed at a discount)
    # The share of the above spent on page images (the vision fallback), for its cost.
    vision_calls: int = 0
    vision_input_tokens: int = 0
    vision_output_tokens: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "llm_calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "vision_calls": self.vision_calls,
            "vision_input_tokens": self.vision_input_tokens,
            "vision_output_tokens": self.vision_output_tokens,
        }


def _int(value: Any) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


# Chat messages are provider-neutral dicts:
#   {"role": "system" | "user", "content": str}
#   {"role": "assistant", "content": str, "tool_calls": [{"id", "name", "arguments"}]}
#   {"role": "tool", "tool_call_id": str, "name": str, "content": str}
Message = dict[str, Any]


class LLMClient(Protocol):
    name: str
    usage: TokenUsage  # tokens billed so far by this client
    # True if complete_json_with_images works (the model reads page images).
    supports_images: bool

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        """Send the prompts, return the parsed JSON object (raise LLMError otherwise)."""
        ...

    def chat_with_tools(self, messages: list[Message], tools: list[ToolSpec]) -> ChatReply:
        """One model turn: either text, or tool calls for us to run (raise LLMError)."""
        ...


# ---------------------------------------------------------------- retries


def post_with_retries(
    client: httpx.Client,
    url: str,
    *,
    headers: dict[str, str],
    body: dict[str, Any],
    max_attempts: int = 4,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    sleep: Callable[[float], None] = time.sleep,
) -> httpx.Response:
    """POST with retries on rate limits (429), server errors (5xx) and network errors.

    Waits grow exponentially (1s, 2s, 4s...) with random jitter, so many workers that hit a
    rate limit together do not all retry at the same instant. Other 4xx errors (bad key,
    bad request) are not retried: trying again would fail the same way.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            response = client.post(url, headers=headers, json=body)
        except httpx.TransportError as exc:  # includes timeouts
            if attempt == max_attempts:
                raise LLMError(f"network error after {attempt} attempts", retryable=True) from exc
            sleep(_backoff(attempt, None, base_delay, max_delay))
            continue
        if response.status_code == 429 or response.status_code >= 500:
            if attempt == max_attempts:
                raise LLMError(
                    f"HTTP {response.status_code} after {attempt} attempts", retryable=True
                )
            logger.warning("LLM HTTP %d, retrying (attempt %d)", response.status_code, attempt)
            sleep(_backoff(attempt, response.headers.get("retry-after"), base_delay, max_delay))
            continue
        if response.is_error:
            raise LLMError(
                f"HTTP {response.status_code}: {_error_message(response)}",
                status_code=response.status_code,
            )
        return response
    raise AssertionError("unreachable")


def _backoff(attempt: int, retry_after: str | None, base: float, cap: float) -> float:
    if retry_after:
        try:
            return min(cap, float(retry_after))  # the provider told us how long to wait
        except ValueError:
            pass
    delay = min(cap, base * 2 ** (attempt - 1))
    return delay / 2 + random.uniform(0, delay / 2)  # noqa: S311 (jitter, not crypto)


def _error_message(response: httpx.Response) -> str:
    try:
        error = response.json().get("error", {})
        message = error.get("message", "") if isinstance(error, dict) else str(error)
    except ValueError:
        message = ""
    return message[:200] or "request rejected"


def parse_json_object(text: str) -> dict[str, Any]:
    """Parse the model's reply; tolerate a ```json fence around it."""
    cleaned = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", text or "")
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise LLMInvalidJSON(f"reply is not valid JSON ({exc.msg})") from exc
    if not isinstance(data, dict):
        raise LLMInvalidJSON("reply is JSON but not an object")
    return data


# ---------------------------------------------------------------- concurrency

_SLOTS: dict[int, threading.BoundedSemaphore] = {}
_SLOTS_LOCK = threading.Lock()


def _request_slots(limit: int | None) -> threading.BoundedSemaphore | None:
    """One semaphore per process and limit, shared by every client: the worker builds a
    client per job, and jobs run side by side, so a per-client limit would not cap the
    total requests in flight (LLM_MAX_CONCURRENT_REQUESTS)."""
    if not limit:
        return None
    with _SLOTS_LOCK:
        return _SLOTS.setdefault(limit, threading.BoundedSemaphore(limit))


# ---------------------------------------------------------------- providers


class _HttpClient:
    name = "base"

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        timeout: float = 60,
        max_attempts: int = 4,
        http_client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_concurrent_requests: int | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("an API key is required")
        self._api_key = api_key
        self.model = model
        self._max_attempts = max_attempts
        self._sleep = sleep
        self._http = http_client or httpx.Client(timeout=httpx.Timeout(timeout))
        # One client is shared by worker threads, so updates take a lock.
        self.usage = TokenUsage()
        self._usage_lock = threading.Lock()
        self._slots = _request_slots(max_concurrent_requests)

    supports_images = False

    def _record_usage(
        self, input_tokens: Any, output_tokens: Any, cached: Any = 0, *, vision: bool = False
    ) -> None:
        with self._usage_lock:
            self.usage.calls += 1
            self.usage.input_tokens += _int(input_tokens)
            self.usage.output_tokens += _int(output_tokens)
            self.usage.cached_input_tokens += _int(cached)
            if vision:
                self.usage.vision_calls += 1
                self.usage.vision_input_tokens += _int(input_tokens)
                self.usage.vision_output_tokens += _int(output_tokens)

    def _post(self, url: str, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
        # The slot is held through retries, so a rate-limited request doesn't let another
        # one start meanwhile; that is what keeps free tiers from being hammered.
        with self._slots or contextlib.nullcontext():
            response = post_with_retries(
                self._http,
                url,
                headers=headers,
                body=body,
                max_attempts=self._max_attempts,
                sleep=self._sleep,
            )
        try:
            return response.json()
        except ValueError as exc:
            raise LLMError("provider returned a non-JSON HTTP body") from exc


class GeminiClient(_HttpClient):
    """Google Gemini generateContent API with a response JSON schema."""

    name = "gemini"
    BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "temperature": 0,  # same input -> (nearly) same output; no creativity
                "responseMimeType": "application/json",
                "responseJsonSchema": schema,
            },
        }
        data = self._post(
            f"{self.BASE_URL}/{self.model}:generateContent",
            {"x-goog-api-key": self._api_key},
            body,
        )
        self._record_gemini_usage(data)
        try:
            parts = data["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError, TypeError) as exc:
            reason = (data.get("promptFeedback") or {}).get("blockReason", "no candidates")
            raise LLMError(f"Gemini returned no answer ({reason})") from exc
        return parse_json_object("".join(part.get("text", "") for part in parts))

    def _record_gemini_usage(self, data: dict[str, Any]) -> None:
        meta = data.get("usageMetadata") or {}
        # Thinking tokens are billed as output tokens.
        output = _int(meta.get("candidatesTokenCount")) + _int(meta.get("thoughtsTokenCount"))
        self._record_usage(
            meta.get("promptTokenCount"), output, meta.get("cachedContentTokenCount")
        )

    def chat_with_tools(self, messages: list[Message], tools: list[ToolSpec]) -> ChatReply:
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        contents: list[dict[str, Any]] = []
        for m in messages:
            if m["role"] == "user":
                contents.append({"role": "user", "parts": [{"text": m["content"]}]})
            elif m["role"] == "assistant":
                parts = [{"text": m["content"]}] if m.get("content") else []
                parts += [
                    {"functionCall": {"name": c["name"], "args": c.get("arguments") or {}}}
                    for c in m.get("tool_calls") or []
                ]
                contents.append({"role": "model", "parts": parts or [{"text": ""}]})
            elif m["role"] == "tool":
                part = {
                    "functionResponse": {"name": m["name"], "response": {"result": m["content"]}}
                }
                # All results of one model turn go back together, in one user turn.
                if contents and contents[-1].get("_tool_results"):
                    contents[-1]["parts"].append(part)
                else:
                    contents.append({"role": "user", "parts": [part], "_tool_results": True})
        for content in contents:
            content.pop("_tool_results", None)
        body: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": contents,
            "generationConfig": {"temperature": 0},
        }
        if tools:
            body["tools"] = [
                {
                    "functionDeclarations": [
                        {"name": t.name, "description": t.description, "parameters": t.parameters}
                        for t in tools
                    ]
                }
            ]
            body["toolConfig"] = {"functionCallingConfig": {"mode": "AUTO"}}
        data = self._post(
            f"{self.BASE_URL}/{self.model}:generateContent", {"x-goog-api-key": self._api_key}, body
        )
        self._record_gemini_usage(data)
        try:
            parts = data["candidates"][0]["content"].get("parts", [])
        except (KeyError, IndexError, TypeError) as exc:
            reason = (data.get("promptFeedback") or {}).get("blockReason", "no candidates")
            raise LLMError(f"Gemini returned no answer ({reason})") from exc
        reply = ChatReply(text="".join(p.get("text", "") for p in parts))
        for part in parts:
            if call := part.get("functionCall"):
                args = call.get("args")
                reply.tool_calls.append(
                    ToolCall(
                        id=call.get("id") or f"call_{uuid.uuid4().hex[:12]}",
                        name=call.get("name", ""),
                        arguments=args if isinstance(args, dict) else None,
                        raw_arguments=json.dumps(args) if args is not None else "",
                    )
                )
        return reply


class _ChatCompletionsClient(_HttpClient):
    """The OpenAI Chat Completions API shape, which Groq also speaks."""

    URL = ""
    LABEL = ""
    # OpenAI's strict mode guarantees the reply matches the schema; our schemas already
    # meet its rules (every key required, no extra keys).
    STRICT_SCHEMA = False

    def _send(self, body: dict[str, Any], *, vision: bool = False) -> dict[str, Any]:
        """POST, record token usage, return the first choice's message."""
        data = self._post(self.URL, {"Authorization": f"Bearer {self._api_key}"}, body)
        usage = data.get("usage") or {}
        cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
        self._record_usage(
            usage.get("prompt_tokens"), usage.get("completion_tokens"), cached, vision=vision
        )
        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"{self.LABEL} returned no answer") from exc
        if not isinstance(message, dict):
            raise LLMError(f"{self.LABEL} returned no answer")
        if message.get("refusal"):  # safety refusal instead of an answer; retrying won't help
            raise LLMError(f"{self.LABEL} refused to answer")
        return message

    def complete_json_with_images(
        self, system: str, user: str, images: list[bytes], schema: dict[str, Any]
    ) -> dict[str, Any]:
        """Like complete_json, with PNG page images after the text (vision models only)."""
        if not self.supports_images:
            raise LLMError(f"{self.LABEL} model {self.model} cannot read images here")
        json_schema: dict[str, Any] = {"name": "invoice_vision", "schema": schema}
        if self.STRICT_SCHEMA:
            json_schema["strict"] = True
        content: list[dict[str, Any]] = [{"type": "text", "text": user}]
        for png in images:
            url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": url, "detail": "high"}})
        body = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
            "response_format": {"type": "json_schema", "json_schema": json_schema},
        }
        return parse_json_object(self._send(body, vision=True).get("content") or "")

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        json_schema: dict[str, Any] = {"name": "invoice_extraction", "schema": schema}
        if self.STRICT_SCHEMA:
            json_schema["strict"] = True
        body = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_schema", "json_schema": json_schema},
        }
        return parse_json_object(self._send(body).get("content") or "")

    def chat_with_tools(self, messages: list[Message], tools: list[ToolSpec]) -> ChatReply:
        converted: list[dict[str, Any]] = []
        for m in messages:
            if m["role"] == "assistant" and m.get("tool_calls"):
                converted.append(
                    {
                        "role": "assistant",
                        "content": m.get("content") or None,
                        "tool_calls": [
                            {
                                "id": c["id"],
                                "type": "function",
                                "function": {
                                    "name": c["name"],
                                    "arguments": json.dumps(c.get("arguments") or {}),
                                },
                            }
                            for c in m["tool_calls"]
                        ],
                    }
                )
            elif m["role"] == "tool":
                converted.append(
                    {"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]}
                )
            else:
                converted.append({"role": m["role"], "content": m.get("content") or ""})
        body: dict[str, Any] = {"model": self.model, "temperature": 0, "messages": converted}
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]
            body["tool_choice"] = "auto"
        message = self._send(body)
        reply = ChatReply(text=message.get("content") or "")
        for call in message.get("tool_calls") or []:
            raw = (call.get("function") or {}).get("arguments") or ""
            try:
                args = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                args = None  # reported back to the model as a tool error, never a crash
            reply.tool_calls.append(
                ToolCall(
                    id=call.get("id") or f"call_{uuid.uuid4().hex[:12]}",
                    name=(call.get("function") or {}).get("name", ""),
                    arguments=args if isinstance(args, dict) else None,
                    raw_arguments=raw,
                )
            )
        return reply


class GroqClient(_ChatCompletionsClient):
    """Groq's OpenAI-compatible chat API with json_schema structured output."""

    name = "groq"
    LABEL = "Groq"
    URL = "https://api.groq.com/openai/v1/chat/completions"


class OpenAIClient(_ChatCompletionsClient):
    """OpenAI Chat Completions with strict json_schema output and native tool calling.

    Sends `reasoning_effort` (default "none": extraction and tool choice need no long
    reasoning, and gpt-6-luna then still accepts temperature 0). Some models reject
    temperature 0 (gpt-6-luna at effort "low") or reasoning_effort (non-reasoning models).
    When OpenAI answers 400 naming one of these two parameters, it is dropped and the
    request sent once more; the client remembers this for later calls. Any other 400 fails.
    """

    name = "openai"
    LABEL = "OpenAI"
    URL = "https://api.openai.com/v1/chat/completions"
    STRICT_SCHEMA = True
    supports_images = True  # gpt-6-luna and the other current OpenAI models read images
    DROPPABLE = ("temperature", "reasoning_effort")

    def __init__(self, *args: Any, reasoning_effort: str | None = "none", **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.reasoning_effort = reasoning_effort
        self.dropped_params: set[str] = set()

    def _send(self, body: dict[str, Any], *, vision: bool = False) -> dict[str, Any]:
        body = dict(body)
        if self.reasoning_effort:
            body["reasoning_effort"] = self.reasoning_effort
        for _ in range(len(self.DROPPABLE) + 1):
            for param in self.dropped_params:
                body.pop(param, None)
            try:
                return super()._send(body, vision=vision)
            except LLMError as exc:
                param = self._rejected_param(exc, body)
                if param is None:
                    raise
                logger.warning("OpenAI model %s rejects %s; sending without it", self.model, param)
                self.dropped_params.add(param)
        raise AssertionError("unreachable")

    def _rejected_param(self, exc: LLMError, body: dict[str, Any]) -> str | None:
        if exc.status_code != 400:
            return None
        message = str(exc)
        for param in self.DROPPABLE:
            if param in body and param in message:
                return param
        return None


class UnconfiguredLLM:
    """Stands in when no provider is configured. Work that never needs the LLM (ledger
    spreadsheets) still runs; anything that does fails permanently with a clear message."""

    name = "none"
    model = "none"
    supports_images = False

    def __init__(self, reason: str) -> None:
        self.reason = reason
        self.usage = TokenUsage()

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        raise LLMError(self.reason, retryable=False)

    def chat_with_tools(self, messages: list[Message], tools: list[ToolSpec]) -> ChatReply:
        raise LLMError(self.reason, retryable=False)


class FakeLLMClient:
    """For tests: replays scripted replies and records every call.

    Each reply can be a dict (returned), a str (parsed like a real reply, so invalid JSON
    raises LLMInvalidJSON), an Exception (raised), or a function(system, user) -> reply.
    """

    name = "fake"
    model = "fake"

    def __init__(
        self, *replies: Any, chat: list[Any] | None = None, vision: list[Any] | None = None
    ) -> None:
        self._replies = list(replies)
        # Scripted replies to complete_json_with_images (dict, str or Exception); with
        # none, the fake says it cannot read images, like a text-only provider.
        self._vision = list(vision or [])
        self.supports_images = bool(vision)
        self.vision_calls: list[dict[str, Any]] = []
        self.calls: list[dict[str, Any]] = []
        # Scripted chat turns: ChatReply, Exception, or function(messages, tools) -> reply.
        # The last one repeats when the script runs out.
        self._chat = list(chat or [])
        self.chat_calls: list[dict[str, Any]] = []
        self.usage = TokenUsage()  # stays zero: no provider, nothing billed

    def chat_with_tools(self, messages: list[Message], tools: list[ToolSpec]) -> ChatReply:
        self.chat_calls.append(
            {"messages": [dict(m) for m in messages], "tools": [t.name for t in tools]}
        )
        if not self._chat:
            return ChatReply(text="(no scripted reply)")
        reply = self._chat.pop(0) if len(self._chat) > 1 else self._chat[0]
        if callable(reply):
            reply = reply(messages, tools)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def complete_json_with_images(
        self, system: str, user: str, images: list[bytes], schema: dict[str, Any]
    ) -> dict[str, Any]:
        self.vision_calls.append({"system": system, "user": user, "images": images})
        self.usage.vision_calls += 1
        reply = self._vision.pop(0) if len(self._vision) > 1 else self._vision[0]
        if isinstance(reply, Exception):
            raise reply
        return parse_json_object(reply) if isinstance(reply, str) else reply

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"system": system, "user": user, "schema": schema})
        reply = self._replies.pop(0) if len(self._replies) > 1 else self._replies[0]
        if callable(reply):
            reply = reply(system, user)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, str):
            return parse_json_object(reply)
        return reply


def get_llm_client(settings: Settings) -> LLMClient:
    """Build the client chosen by LLM_PROVIDER.

    OpenAI reads its own OPENAI_API_KEY; Gemini and Groq read LLM_API_KEY.
    """
    classes = {"gemini": GeminiClient, "groq": GroqClient, "openai": OpenAIClient}
    if settings.llm_provider not in classes:
        raise ValueError("LLM_PROVIDER is 'none'; set it to 'gemini', 'groq' or 'openai'")
    if settings.llm_provider == "openai":
        key, key_name = settings.openai_api_key, "OPENAI_API_KEY"
    else:
        key, key_name = settings.llm_api_key, "LLM_API_KEY"
    if key is None:
        raise ValueError(f"{key_name} is not set")
    extra: dict[str, Any] = {}
    if settings.llm_provider == "openai":
        extra["reasoning_effort"] = settings.openai_reasoning_effort
    return classes[settings.llm_provider](
        key.get_secret_value(),
        settings.llm_model or DEFAULT_MODELS[settings.llm_provider],
        timeout=settings.llm_timeout_seconds,
        max_attempts=settings.llm_max_attempts,
        max_concurrent_requests=settings.llm_max_concurrent_requests,
        **extra,
    )
