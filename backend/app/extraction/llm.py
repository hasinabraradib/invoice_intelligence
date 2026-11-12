"""LLM clients that return JSON. Called over plain HTTPS with httpx (no vendor SDKs):
fewer dependencies, and we control timeouts and retries ourselves.

Security: the API key travels in a request header (never in the URL, which could be
logged), and we never log prompts or responses.
"""

import json
import logging
import random
import re
import time
from collections.abc import Callable
from typing import Any, Protocol

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

DEFAULT_MODELS = {"gemini": "gemini-2.5-flash", "groq": "openai/gpt-oss-120b"}


class LLMError(Exception):
    """The provider could not give us an answer (network, HTTP error, blocked reply)."""


class LLMInvalidJSON(LLMError):
    """The provider answered, but not with a JSON object."""


class LLMClient(Protocol):
    name: str

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        """Send the prompts, return the parsed JSON object (raise LLMError otherwise)."""
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
                raise LLMError(f"network error after {attempt} attempts") from exc
            sleep(_backoff(attempt, None, base_delay, max_delay))
            continue
        if response.status_code == 429 or response.status_code >= 500:
            if attempt == max_attempts:
                raise LLMError(f"HTTP {response.status_code} after {attempt} attempts")
            logger.warning("LLM HTTP %d, retrying (attempt %d)", response.status_code, attempt)
            sleep(_backoff(attempt, response.headers.get("retry-after"), base_delay, max_delay))
            continue
        if response.is_error:
            raise LLMError(f"HTTP {response.status_code}: {_error_message(response)}")
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
    ) -> None:
        if not api_key:
            raise ValueError("LLM_API_KEY is required")
        self._api_key = api_key
        self.model = model
        self._max_attempts = max_attempts
        self._sleep = sleep
        self._http = http_client or httpx.Client(timeout=httpx.Timeout(timeout))

    def _post(self, url: str, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
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
        try:
            parts = data["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError, TypeError) as exc:
            reason = (data.get("promptFeedback") or {}).get("blockReason", "no candidates")
            raise LLMError(f"Gemini returned no answer ({reason})") from exc
        return parse_json_object("".join(part.get("text", "") for part in parts))


class GroqClient(_HttpClient):
    """Groq's OpenAI-compatible chat API with json_schema structured output."""

    name = "groq"
    URL = "https://api.groq.com/openai/v1/chat/completions"

    def complete_json(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        body = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "invoice_extraction", "schema": schema},
            },
        }
        data = self._post(self.URL, {"Authorization": f"Bearer {self._api_key}"}, body)
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("Groq returned no answer") from exc
        return parse_json_object(content)


class FakeLLMClient:
    """For tests: replays scripted replies and records every call.

    Each reply can be a dict (returned), a str (parsed like a real reply, so invalid JSON
    raises LLMInvalidJSON), an Exception (raised), or a function(system, user) -> reply.
    """

    name = "fake"
    model = "fake"

    def __init__(self, *replies: Any) -> None:
        self._replies = list(replies)
        self.calls: list[dict[str, Any]] = []

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
    """Build the client chosen by LLM_PROVIDER."""
    classes = {"gemini": GeminiClient, "groq": GroqClient}
    if settings.llm_provider not in classes:
        raise ValueError("LLM_PROVIDER is 'none'; set it to 'gemini' or 'groq'")
    if settings.llm_api_key is None:
        raise ValueError("LLM_API_KEY is not set")
    return classes[settings.llm_provider](
        settings.llm_api_key.get_secret_value(),
        settings.llm_model or DEFAULT_MODELS[settings.llm_provider],
        timeout=settings.llm_timeout_seconds,
        max_attempts=settings.llm_max_attempts,
    )
