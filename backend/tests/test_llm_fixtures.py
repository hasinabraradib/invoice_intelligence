"""Replay sanitised REAL provider replies (tests/fixtures/llm/<provider>/, recorded with
`make capture-fixtures` on synthetic text) through the clients, so mock tests match what
the APIs actually return. No network."""

import json
from pathlib import Path

import httpx
import pytest

from app.assistant.tools import specs
from app.extraction.llm import OpenAIClient
from app.extraction.schema import InvoiceExtraction, llm_json_schema

FIXTURES = Path(__file__).parent / "fixtures" / "llm"
CLIENTS = {"openai": OpenAIClient}
PROVIDERS = sorted(p.name for p in FIXTURES.iterdir() if p.name in CLIENTS)


def _load(provider: str, name: str) -> dict:
    return json.loads((FIXTURES / provider / f"{name}.json").read_text())


def _client(provider: str, fixture: dict):
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json=fixture["response"])

    http = httpx.Client(transport=httpx.MockTransport(handler))
    return CLIENTS[provider]("test-key", fixture["model"], http_client=http), sent


def test_fixtures_exist_for_at_least_one_provider():
    assert PROVIDERS, "run `make capture-fixtures` with a configured provider"


@pytest.mark.parametrize("provider", PROVIDERS)
def test_real_extraction_reply_parses_into_the_schema(provider):
    fixture = _load(provider, "extraction")
    llm, sent = _client(provider, fixture)

    data = llm.complete_json("sys", "user", llm_json_schema())
    extraction = InvoiceExtraction.model_validate(data)

    assert extraction.invoice_number.value == "NW-2026-0417"
    assert extraction.total.status == "found" and "12,535.00" in extraction.total.value
    assert len(extraction.line_items) == 2
    # What we send today is what the real API accepted when the fixture was recorded.
    assert sorted(json.loads(sent[0].content)) == fixture["request_keys"]
    usage = fixture["response"]["usage"]
    assert llm.usage.input_tokens == usage["prompt_tokens"] > 0
    assert llm.usage.cached_input_tokens == usage["prompt_tokens_details"]["cached_tokens"]


@pytest.mark.parametrize("provider", PROVIDERS)
def test_real_tool_call_reply_parses(provider):
    fixture = _load(provider, "tool_call")
    llm, sent = _client(provider, fixture)

    reply = llm.chat_with_tools([{"role": "user", "content": "USD total?"}], specs())

    assert reply.text == ""  # content is null on a tool-call turn
    assert [(c.name, c.arguments) for c in reply.tool_calls] == [("sum_invoices", {})]
    assert reply.tool_calls[0].id == "call_fixture_1"
    assert sorted(json.loads(sent[0].content)) == fixture["request_keys"]


@pytest.mark.parametrize("provider", PROVIDERS)
def test_real_final_answer_reply_parses(provider):
    fixture = _load(provider, "final_answer")
    llm, _ = _client(provider, fixture)

    reply = llm.chat_with_tools([{"role": "user", "content": "USD total?"}], specs())

    assert reply.tool_calls == []
    assert "302.00" in reply.text and "PE-2211" in reply.text


def test_fixtures_hold_no_secrets():
    for path in FIXTURES.rglob("*.json"):
        text = path.read_text()
        assert "Bearer" not in text and "sk-" not in text and "api_key" not in text.lower()
