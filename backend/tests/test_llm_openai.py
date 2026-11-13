"""OpenAIClient against a fake HTTP server (httpx.MockTransport): no real API calls."""

import json

import httpx
import pytest

from app.config import Settings
from app.extraction.llm import (
    LLMError,
    LLMInvalidJSON,
    OpenAIClient,
    ToolSpec,
    get_llm_client,
)
from app.extraction.schema import llm_json_schema
from app.extraction.service import extract_invoice
from app.parsing.models import FileKind, PageText, ParsedDocument

URL = "https://api.openai.com/v1/chat/completions"
SCHEMA = {"type": "object", "properties": {}, "required": [], "additionalProperties": False}


def _openai(responses: list[httpx.Response]):
    """A client whose HTTP calls are answered in order by `responses` (last one repeats)."""
    requests, sleeps = [], []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return responses[min(len(requests), len(responses)) - 1]

    http = httpx.Client(transport=httpx.MockTransport(handler))
    llm = OpenAIClient("sk-test", "test-model", http_client=http, sleep=sleeps.append)
    return llm, requests, sleeps


def _reply(message: dict, usage: tuple[int, int] = (0, 0)) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "choices": [{"index": 0, "message": {"role": "assistant", **message}}],
            "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1]},
        },
    )


def _text(content: str, usage: tuple[int, int] = (0, 0)) -> httpx.Response:
    return _reply({"content": content, "refusal": None}, usage)


def test_complete_json_request_shape_and_usage():
    llm, requests, _ = _openai([_text('{"total": "5.00"}', usage=(120, 30))])

    assert llm.complete_json("sys", "user", SCHEMA) == {"total": "5.00"}
    request = requests[0]
    assert str(request.url) == URL
    assert request.headers["authorization"] == "Bearer sk-test"
    assert "sk-test" not in str(request.url)  # key never in the URL
    body = json.loads(request.content)
    assert body["model"] == "test-model" and body["temperature"] == 0
    assert body["reasoning_effort"] == "none"
    assert body["messages"] == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "user"},
    ]
    assert body["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "invoice_extraction", "schema": SCHEMA, "strict": True},
    }
    assert "tools" not in body
    assert llm.usage.as_dict() == {
        "llm_calls": 1,
        "input_tokens": 120,
        "output_tokens": 30,
        "cached_input_tokens": 0,
    }


def test_429_is_retried_honouring_retry_after():
    llm, requests, sleeps = _openai(
        [
            httpx.Response(
                429, headers={"retry-after": "3"}, json={"error": {"message": "rate limit"}}
            ),
            httpx.Response(429, json={"error": {"message": "rate limit"}}),
            _text("{}", usage=(10, 2)),
        ]
    )

    assert llm.complete_json("sys", "user", SCHEMA) == {}
    assert len(requests) == 3
    assert sleeps[0] == 3.0  # the provider's own wait
    assert 1.0 <= sleeps[1] <= 2.0  # then exponential backoff with jitter
    assert llm.usage.calls == 1  # rate-limited attempts are not billed


def test_persistent_429_fails_as_retryable():
    llm, requests, _ = _openai([httpx.Response(429, json={})])
    with pytest.raises(LLMError, match="429 after 4 attempts") as info:
        llm.complete_json("sys", "user", SCHEMA)
    assert info.value.retryable and len(requests) == 4


def test_bad_key_is_not_retried_and_error_is_short():
    llm, requests, _ = _openai(
        [httpx.Response(401, json={"error": {"message": "Incorrect API key provided"}})]
    )
    with pytest.raises(LLMError, match="HTTP 401: Incorrect API key") as info:
        llm.complete_json("sys", "user", SCHEMA)
    assert not info.value.retryable and len(requests) == 1


def test_invalid_json_reply_raises_llm_invalid_json():
    llm, _, _ = _openai([_text('{"total": "5.0')])  # e.g. cut off at max tokens
    with pytest.raises(LLMInvalidJSON):
        llm.complete_json("sys", "user", SCHEMA)


def test_extraction_repairs_invalid_json_with_a_second_call():
    good = {
        name: {"value": None, "status": "missing", "evidence": None, "page": None}
        for name in llm_json_schema()["properties"]
        if name != "line_items"
    }
    good["total"] = {
        "value": "1,320.00",
        "status": "found",
        "evidence": "Total: 1,320.00",
        "page": 1,
    }
    good["line_items"] = []
    llm, requests, _ = _openai([_text("{not json"), _text(json.dumps(good))])
    doc = ParsedDocument(
        file_kind=FileKind.PDF,
        page_count=1,
        pages=[PageText(page_number=1, text="INVOICE\nTotal: 1,320.00", source="text_layer")],
    )

    result = extract_invoice(doc, llm)

    assert result.ok and result.llm_calls == 2 and len(requests) == 2
    assert str(result.fields["total"].value) == "1320.00"
    repair_prompt = json.loads(requests[1].content)["messages"][1]["content"]
    assert "previous reply could not be used" in repair_prompt
    assert json.loads(requests[1].content)["response_format"]["json_schema"]["strict"] is True


def test_refusal_is_a_permanent_error():
    llm, _, _ = _openai([_reply({"content": None, "refusal": "I can't help with that."})])
    with pytest.raises(LLMError, match="OpenAI refused") as info:
        llm.complete_json("sys", "user", SCHEMA)
    assert not info.value.retryable


def test_empty_choices_is_an_error():
    llm, _, _ = _openai([httpx.Response(200, json={"choices": []})])
    with pytest.raises(LLMError, match="OpenAI returned no answer"):
        llm.complete_json("sys", "user", SCHEMA)


TOOLS = [
    ToolSpec(
        "sum_invoices",
        "Sum totals",
        {"type": "object", "properties": {"supplier": {"type": "string"}}, "required": []},
    )
]
HISTORY = [
    {"role": "system", "content": "rules"},
    {"role": "user", "content": "How much for ACME?"},
    {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": "call_1", "name": "sum_invoices", "arguments": {"supplier": "acme"}}],
    },
    {"role": "tool", "tool_call_id": "call_1", "name": "sum_invoices", "content": '{"total": "5"}'},
]


def test_tool_request_shape_and_history_conversion():
    llm, requests, _ = _openai([_text("ACME billed 5.00.", usage=(300, 12))])

    reply = llm.chat_with_tools(HISTORY, TOOLS)

    assert reply.text == "ACME billed 5.00." and reply.tool_calls == []
    body = json.loads(requests[0].content)
    assert body["temperature"] == 0 and body["tool_choice"] == "auto"
    assert body["reasoning_effort"] == "none"
    assert "response_format" not in body
    assert body["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "sum_invoices",
                "description": "Sum totals",
                "parameters": TOOLS[0].parameters,
            },
        }
    ]
    assert body["messages"][2] == {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "sum_invoices", "arguments": '{"supplier": "acme"}'},
            }
        ],
    }
    assert body["messages"][3] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": '{"total": "5"}',
    }
    assert llm.usage.input_tokens == 300 and llm.usage.output_tokens == 12


def test_parallel_tool_calls_are_parsed_and_bad_arguments_become_none():
    message = {
        "content": None,
        "refusal": None,
        "tool_calls": [
            {
                "id": "call_a",
                "type": "function",
                "function": {"name": "sum_invoices", "arguments": '{"supplier": "acme"}'},
            },
            {
                "id": "call_b",
                "type": "function",
                "function": {"name": "sum_invoices", "arguments": '{"supplier": '},
            },
            {"id": "call_c", "type": "function", "function": {"name": "count", "arguments": ""}},
        ],
    }
    llm, _, _ = _openai([httpx.Response(503, json={}), _reply(message)])

    reply = llm.chat_with_tools(HISTORY[:2], TOOLS)

    assert reply.text == ""
    assert [(c.id, c.name, c.arguments) for c in reply.tool_calls] == [
        ("call_a", "sum_invoices", {"supplier": "acme"}),
        ("call_b", "sum_invoices", None),  # not JSON: reported back to the model, no crash
        ("call_c", "count", {}),
    ]
    assert reply.tool_calls[1].raw_arguments == '{"supplier": '


def test_get_llm_client_uses_openai_api_key_and_default_model():
    llm = get_llm_client(Settings(llm_provider="openai", openai_api_key="sk-x", llm_model=""))
    assert isinstance(llm, OpenAIClient) and llm.model == "gpt-6-luna"
    assert llm.reasoning_effort == "none"
    custom = get_llm_client(
        Settings(
            llm_provider="openai",
            openai_api_key="sk-x",
            llm_model="gpt-4.1-nano",
            openai_reasoning_effort="low",
        )
    )
    assert custom.model == "gpt-4.1-nano" and custom.reasoning_effort == "low"
    # LLM_API_KEY is for Gemini/Groq; OpenAI must not silently use another provider's key.
    with pytest.raises(ValueError, match="OPENAI_API_KEY is not set"):
        get_llm_client(Settings(llm_provider="openai", llm_api_key="k", openai_api_key=""))


def test_openai_key_is_hidden_in_settings_repr():
    settings = Settings(llm_provider="openai", openai_api_key="sk-very-secret")
    assert "sk-very-secret" not in repr(settings)


def _rejects(param: str) -> httpx.Response:
    """OpenAI's 400 for an unsupported parameter value (shape copied from a real reply)."""
    return httpx.Response(
        400,
        json={
            "error": {
                "message": f"Unsupported value: '{param}' does not support 0 with this model. "
                "Only the default (1) value is supported.",
                "type": "invalid_request_error",
                "param": param,
                "code": "unsupported_value",
            }
        },
    )


def test_rejected_temperature_is_dropped_once_and_remembered():
    llm, requests, sleeps = _openai([_rejects("temperature"), _text("{}"), _text("{}")])

    assert llm.complete_json("sys", "user", SCHEMA) == {}
    first, second = (json.loads(r.content) for r in requests)
    assert first["temperature"] == 0
    assert "temperature" not in second and second["reasoning_effort"] == "none"
    assert sleeps == []  # not a rate limit: resent at once

    llm.chat_with_tools([{"role": "user", "content": "hi"}], TOOLS)
    assert len(requests) == 3 and "temperature" not in json.loads(requests[2].content)
    assert llm.dropped_params == {"temperature"} and llm.usage.calls == 2


def test_rejected_reasoning_effort_is_dropped_for_non_reasoning_models():
    unsupported = httpx.Response(
        400,
        json={
            "error": {
                "message": "Unrecognized request argument supplied: reasoning_effort",
                "param": "reasoning_effort",
            }
        },
    )
    llm, requests, _ = _openai([unsupported, _text("{}")])

    assert llm.complete_json("sys", "user", SCHEMA) == {}
    second = json.loads(requests[1].content)
    assert "reasoning_effort" not in second and second["temperature"] == 0


def test_both_parameters_can_be_dropped_then_other_400s_still_fail():
    llm, requests, _ = _openai(
        [_rejects("temperature"), _rejects("reasoning_effort"), _rejects("temperature")]
    )
    # Third reply names a parameter that is already gone: a real error, not a loop.
    with pytest.raises(LLMError, match="HTTP 400") as info:
        llm.complete_json("sys", "user", SCHEMA)
    assert len(requests) == 3 and info.value.status_code == 400 and not info.value.retryable


def test_other_bad_requests_are_not_resent():
    bad = httpx.Response(400, json={"error": {"message": "Invalid schema for response_format"}})
    llm, requests, _ = _openai([bad])
    with pytest.raises(LLMError, match="Invalid schema"):
        llm.complete_json("sys", "user", SCHEMA)
    assert len(requests) == 1 and llm.dropped_params == set()


def test_reasoning_effort_can_be_turned_off():
    seen: list[httpx.Request] = []
    transport = httpx.MockTransport(lambda request: seen.append(request) or _text("{}"))
    http = httpx.Client(transport=transport)
    llm = OpenAIClient("sk-test", "m", http_client=http, reasoning_effort=None)

    llm.complete_json("sys", "user", SCHEMA)

    assert "reasoning_effort" not in json.loads(seen[0].content)
