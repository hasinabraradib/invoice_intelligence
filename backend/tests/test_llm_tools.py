"""chat_with_tools for Gemini and Groq against a fake HTTP server."""

import json

import httpx

from app.extraction.llm import ChatReply, FakeLLMClient, GeminiClient, GroqClient, ToolSpec

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
        "tool_calls": [
            {"id": "c1", "name": "sum_invoices", "arguments": {"supplier": "acme"}},
            {"id": "c2", "name": "count_invoices", "arguments": {}},
        ],
    },
    {"role": "tool", "tool_call_id": "c1", "name": "sum_invoices", "content": '{"total": "5"}'},
    {"role": "tool", "tool_call_id": "c2", "name": "count_invoices", "content": '{"count": 2}'},
]


def client(cls, responses):
    requests, sleeps = [], []

    def handler(request):
        requests.append(request)
        status, body = responses[min(len(requests), len(responses)) - 1]
        return httpx.Response(status, json=body)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    return cls("key", "model", http_client=http, sleep=sleeps.append), requests, sleeps


def test_gemini_tools_request_and_function_call_parsing():
    reply = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"text": "Let me check."},
                        {"functionCall": {"name": "sum_invoices", "args": {"supplier": "acme"}}},
                    ]
                }
            }
        ]
    }
    llm, requests, _ = client(GeminiClient, [(429, {}), (200, reply)])

    result = llm.chat_with_tools(HISTORY, TOOLS)

    assert len(requests) == 2  # the 429 was retried
    body = json.loads(requests[-1].content)
    assert body["generationConfig"]["temperature"] == 0
    assert body["systemInstruction"]["parts"][0]["text"] == "rules"
    assert body["tools"][0]["functionDeclarations"][0]["name"] == "sum_invoices"
    roles = [c["role"] for c in body["contents"]]
    assert roles == ["user", "model", "user"]  # both tool results merged into one turn
    assert len(body["contents"][2]["parts"]) == 2
    assert body["contents"][1]["parts"][0]["functionCall"]["args"] == {"supplier": "acme"}
    assert result.text == "Let me check."
    [call] = result.tool_calls
    assert call.name == "sum_invoices" and call.arguments == {"supplier": "acme"} and call.id


def test_groq_tools_request_and_bad_arguments():
    reply = {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "t1",
                            "type": "function",
                            "function": {
                                "name": "sum_invoices",
                                "arguments": '{"supplier": "acme"}',
                            },
                        },
                        {
                            "id": "t2",
                            "type": "function",
                            "function": {"name": "count_invoices", "arguments": "{not json"},
                        },
                    ],
                }
            }
        ]
    }
    llm, requests, _ = client(GroqClient, [(200, reply)])

    result = llm.chat_with_tools(HISTORY, TOOLS)

    body = json.loads(requests[0].content)
    assert body["temperature"] == 0 and body["tool_choice"] == "auto"
    assert body["tools"][0]["function"]["name"] == "sum_invoices"
    assistant = body["messages"][2]
    assert assistant["tool_calls"][0]["function"]["arguments"] == '{"supplier": "acme"}'
    assert body["messages"][3] == {
        "role": "tool",
        "tool_call_id": "c1",
        "content": '{"total": "5"}',
    }
    good, bad = result.tool_calls
    assert good.arguments == {"supplier": "acme"}
    assert bad.arguments is None and bad.raw_arguments == "{not json"  # no crash


def test_fake_client_scripts_chat_turns():
    fake = FakeLLMClient(
        chat=[
            ChatReply(text="one"),
            lambda messages, tools: ChatReply(
                text=f"{len(messages)} messages, tools: {[t.name for t in tools]}"
            ),
        ]
    )
    assert fake.chat_with_tools([], TOOLS).text == "one"
    assert fake.chat_with_tools([{"role": "user", "content": "x"}], TOOLS).text == (
        "1 messages, tools: ['sum_invoices']"
    )
    assert len(fake.chat_calls) == 2
