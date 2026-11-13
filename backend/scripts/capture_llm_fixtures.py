"""Record real provider replies as sanitised test fixtures (see `make capture-fixtures`).

Makes three real calls with the configured provider, using the app's own prompts and tool
specs on SYNTHETIC invoice text only:

    extraction.json     complete_json on a synthetic invoice (structured output)
    tool_call.json      an assistant turn that should call sum_invoices
    final_answer.json   the next turn, after a synthetic tool result

Each file holds the provider's JSON reply with ids and timestamps replaced, plus the
request keys the provider accepted. No headers are stored, so the API key cannot leak
into a fixture. Tests replay these replies through a mock HTTP server.

    python scripts/capture_llm_fixtures.py --out /fixtures
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # make `app` importable

from app.assistant.prompt import system_prompt, wrap_tool_result  # noqa: E402
from app.assistant.tools import specs  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.extraction.llm import _HttpClient, get_llm_client  # noqa: E402
from app.extraction.prompt import SourcePage, build_prompts  # noqa: E402
from app.extraction.schema import llm_json_schema  # noqa: E402

INVOICE = """INVOICE   Northwind Office Supply Ltd (synthetic test data)
Invoice No: NW-2026-0417
Invoice Date: 2026-10-03
Bill To: Example Trading Co.
A4 paper (box)           10        450.00  4,500.00
Toner cartridge           2      3,200.00  6,400.00
Subtotal: 10,900.00
VAT 15%: 1,635.00
Total: BDT 12,535.00"""

QUESTION = "What is the total of all our USD invoices?"
TOOL_RESULT = {
    "totals_by_currency": [
        {"currency": "USD", "invoices": 1, "total": "302.00", "invoices_without_total": 0}
    ],
    "invoices_included": [
        {
            "invoice_number": "PE-2211",
            "filename": "synthetic-invoice.xlsx",
            "invoice": 1,
            "page": 1,
            "supplier": "Padma Electric Co.",
            "date": "2026-09-17",
            "total": "302.00",
            "currency": "USD",
        }
    ],
}

# Top-level fields that identify one real call; replaced so fixtures are stable.
VOLATILE = {"id": "fixture-response-id", "created": 0, "system_fingerprint": "fp_fixture"}


def sanitise(response: dict[str, Any]) -> dict[str, Any]:
    clean = {k: VOLATILE.get(k, v) for k, v in response.items() if k != "x_groq"}
    calls = 0
    for choice in clean.get("choices") or []:
        for call in (choice.get("message") or {}).get("tool_calls") or []:
            calls += 1
            call["id"] = f"call_fixture_{calls}"
    return clean


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    settings = get_settings()
    llm = get_llm_client(settings)  # raises with a clear message if not configured
    if not isinstance(llm, _HttpClient):
        print("needs a real provider (LLM_PROVIDER gemini, groq or openai)")
        return 2

    exchanges: list[dict[str, Any]] = []

    def record(response: httpx.Response) -> None:
        response.read()
        exchanges.append(
            {
                "status": response.status_code,
                "request_keys": sorted(json.loads(response.request.content)),
                "response": response.json(),
            }
        )

    llm._http.event_hooks["response"].append(record)  # noqa: SLF001 (test tooling)

    def save(name: str) -> None:
        # Only the successful exchange; rate-limited attempts are retried by the client.
        ok = [e for e in exchanges if e["status"] == 200][-1]
        fixture = {
            "provider": llm.name,
            "model": llm.model,
            "request_keys": ok["request_keys"],
            "response": sanitise(ok["response"]),
        }
        path = args.out / llm.name / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(fixture, indent=2, ensure_ascii=False) + "\n")
        print(f"saved {path} ({len(path.read_text())} bytes)")
        exchanges.clear()

    system, user = build_prompts([SourcePage(1, INVOICE)])
    llm.complete_json(system, user, llm_json_schema())
    save("extraction")

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt("2026-11-14")},
        {"role": "user", "content": QUESTION},
    ]
    reply = llm.chat_with_tools(messages, specs())
    save("tool_call")
    if not reply.tool_calls:
        print("the model answered without a tool call; fixture kept, but check it")
        return 1
    call = reply.tool_calls[0]
    messages.append(
        {
            "role": "assistant",
            "content": reply.text,
            "tool_calls": [{"id": call.id, "name": call.name, "arguments": call.arguments}],
        }
    )
    messages.append(
        {
            "role": "tool",
            "tool_call_id": call.id,
            "name": call.name,
            "content": wrap_tool_result(call.name, TOOL_RESULT),
        }
    )
    llm.chat_with_tools(messages, specs())
    save("final_answer")
    print("usage:", json.dumps(llm.usage.as_dict()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
