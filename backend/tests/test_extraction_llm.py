"""LLM clients against a fake HTTP server (httpx.MockTransport): no real API calls."""

import json

import httpx
import pytest

from app.config import Settings
from app.extraction.llm import (
    GeminiClient,
    GroqClient,
    LLMError,
    LLMInvalidJSON,
    get_llm_client,
)

SCHEMA = {"type": "object", "properties": {}}


def _client(cls, responses):
    """Build a client whose HTTP calls are answered by `responses`, recording requests."""
    requests, sleeps = [], []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        status, body = responses[min(len(requests), len(responses)) - 1]
        return httpx.Response(status, json=body)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    return cls("secret-key", "test-model", http_client=http, sleep=sleeps.append), requests, sleeps


def _gemini_ok(payload: dict) -> tuple[int, dict]:
    return 200, {"candidates": [{"content": {"parts": [{"text": json.dumps(payload)}]}}]}


def test_gemini_retries_429_twice_then_succeeds():
    client, requests, sleeps = _client(
        GeminiClient, [(429, {"error": {"message": "slow down"}})] * 2 + [_gemini_ok({"a": 1})]
    )

    assert client.complete_json("sys", "user", SCHEMA) == {"a": 1}
    assert len(requests) == 3 and len(sleeps) == 2
    assert 0.5 <= sleeps[0] <= 1.0 and 1.0 <= sleeps[1] <= 2.0  # exponential + jitter
    body = json.loads(requests[0].content)
    assert body["generationConfig"]["temperature"] == 0
    assert body["generationConfig"]["responseJsonSchema"] == SCHEMA
    assert requests[0].headers["x-goog-api-key"] == "secret-key"
    assert "secret-key" not in str(requests[0].url)  # key never in the URL
    assert "test-model:generateContent" in str(requests[0].url)


def test_gives_up_after_four_attempts_on_5xx():
    client, requests, sleeps = _client(GeminiClient, [(503, {})])
    with pytest.raises(LLMError, match="503 after 4 attempts"):
        client.complete_json("sys", "user", SCHEMA)
    assert len(requests) == 4 and len(sleeps) == 3


def test_client_errors_are_not_retried():
    client, requests, _ = _client(GeminiClient, [(400, {"error": {"message": "bad schema"}})])
    with pytest.raises(LLMError, match="HTTP 400: bad schema"):
        client.complete_json("sys", "user", SCHEMA)
    assert len(requests) == 1


def test_invalid_json_reply_raises_llm_invalid_json():
    client, _, _ = _client(
        GeminiClient, [(200, {"candidates": [{"content": {"parts": [{"text": "{oops"}]}}]})]
    )
    with pytest.raises(LLMInvalidJSON):
        client.complete_json("sys", "user", SCHEMA)


def test_groq_request_shape_and_fenced_json():
    reply = {"choices": [{"message": {"content": '```json\n{"total": 5}\n```'}}]}
    client, requests, _ = _client(GroqClient, [(200, reply)])

    assert client.complete_json("sys", "user", SCHEMA) == {"total": 5}
    body = json.loads(requests[0].content)
    assert body["temperature"] == 0 and body["model"] == "test-model"
    assert body["response_format"]["type"] == "json_schema"
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert requests[0].headers["authorization"] == "Bearer secret-key"


def test_get_llm_client_selects_provider_and_requires_key():
    assert isinstance(get_llm_client(Settings(llm_provider="groq", llm_api_key="k")), GroqClient)
    gemini = get_llm_client(Settings(llm_provider="gemini", llm_api_key="k", llm_model=""))
    assert isinstance(gemini, GeminiClient) and gemini.model == "gemini-2.5-flash"
    with pytest.raises(ValueError, match="not set"):
        get_llm_client(Settings(llm_provider="gemini", llm_api_key=""))
    with pytest.raises(ValueError, match="none"):
        get_llm_client(Settings(llm_provider="none"))


def test_token_usage_is_summed_over_successful_calls_only():
    ok = _gemini_ok({"a": 1})
    ok[1]["usageMetadata"] = {
        "promptTokenCount": 100,
        "candidatesTokenCount": 20,
        "thoughtsTokenCount": 5,  # billed as output
    }
    client, _, _ = _client(GeminiClient, [(429, {}), ok, ok])
    client.complete_json("sys", "user", SCHEMA)
    client.complete_json("sys", "user", SCHEMA)
    assert client.usage.as_dict() == {"llm_calls": 2, "input_tokens": 200, "output_tokens": 50}

    reply = {
        "choices": [{"message": {"content": "{}"}}],
        "usage": {"prompt_tokens": 7, "completion_tokens": 3},
    }
    groq, _, _ = _client(GroqClient, [(200, reply)])
    groq.complete_json("sys", "user", SCHEMA)
    assert (groq.usage.input_tokens, groq.usage.output_tokens) == (7, 3)


def test_missing_usage_counts_the_call_with_zero_tokens():
    client, _, _ = _client(GroqClient, [(200, {"choices": [{"message": {"content": "{}"}}]})])
    client.complete_json("sys", "user", SCHEMA)
    assert client.usage.as_dict() == {"llm_calls": 1, "input_tokens": 0, "output_tokens": 0}
