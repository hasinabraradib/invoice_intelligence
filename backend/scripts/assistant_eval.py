"""Run docs/assistant-eval.md against the REAL LLM provider and report the score.

Builds the synthetic corpus in a throw-away database (so other documents cannot change the
answers), processes it with the real pipeline, asks each question through the real agent,
and checks the number and the citation. Prints a table and a JSON summary.

    python scripts/assistant_eval.py            # see `make assistant-eval`
    python scripts/assistant_eval.py --keep     # keep the eval database afterwards
"""

import argparse
import asyncio
import hashlib
import json
import re
import sys
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # make `app` importable

BACKEND_DIR = Path(__file__).resolve().parents[1]
EVAL_FILE = BACKEND_DIR.parent / "docs" / "assistant-eval.md"

# The corpus: (filename, pages), each page a list of text lines.
CORPUS: list[tuple[str, list[list[str]]]] = [
    (
        "eval-acme-march.pdf",
        [
            [
                "INVOICE  ACME Supplies Ltd",
                "Invoice No: INV-1001",
                "Invoice Date: 2026-03-02",
                "Office chairs  4  300.00  1,200.00",
                "Currency: BDT",
                "Total: 1,200.00 BDT",
            ]
        ],
    ),
    (
        "eval-acme-late-march.pdf",
        [
            [
                "INVOICE  ACME Supplies Ltd",
                "Invoice No: INV-1002",
                "Invoice Date: 2026-03-20",
                "Desks  2  400.00  800.00",
                "Currency: BDT",
                "Total: 800.00 BDT",
            ]
        ],
    ),
    (
        "eval-globex.pdf",
        [
            [
                "INVOICE  Globex Corp",
                "Invoice No: GX-77",
                "Invoice Date: 2026-02-11",
                "Consulting  1  450.50  450.50",
                "Currency: USD",
                "Total: 450.50 USD",
            ]
        ],
    ),
    (
        "eval-power.pdf",
        [
            [
                "INVOICE  Dhaka Power Co",
                "Invoice No: DP-3",
                "Invoice Date: 2026-03-05",
                "Due Date: 2026-03-25",
                "Monthly electricity bill for the office",
                "Currency: BDT",
                "Total: 3,400.00 BDT",
            ]
        ],
    ),
    (
        "eval-batch.pdf",
        [
            [
                "INVOICE  Initech",
                "Invoice No: IN-9",
                "Invoice Date: 2026-01-15",
                "Software licence  1  99.99  99.99",
                "Currency: USD",
                "Total: 99.99 USD",
                "Page 1 of 1",
            ],
            [
                "INVOICE  Umbrella Trading",
                "Invoice No: UT-5",
                "Invoice Date: 2026-03-28",
                "Safety equipment  10  150.00  1,500.00",
                "Currency: BDT",
                "Page 1 of 2",
            ],
            [
                "Umbrella Trading, continued",
                "Installation  1  500.00  500.00",
                "Total: 2,000.00 BDT",
                "Page 2 of 2",
            ],
        ],
    ),
]


@dataclass
class Case:
    number: int
    question: str
    expected: str
    citation: str  # "file.pdf:page" or "-"


def load_cases(path: Path = EVAL_FILE) -> list[Case]:
    cases = []
    for line in path.read_text().splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 4 and cells[0].isdigit():
            cases.append(Case(int(cells[0]), cells[1], cells[2], cells[3]))
    return cases


def _amounts(text: str) -> list[Decimal]:
    found = []
    for token in re.findall(r"\d[\d,]*(?:\.\d+)?", text):
        try:
            found.append(Decimal(token.replace(",", "")))
        except InvalidOperation:
            continue
    return found


def value_ok(expected: str, answer: str) -> bool:
    if expected == "NONE":  # must say nothing was found, without inventing an amount
        said_none = re.search(
            r"\b(no|none|not find|didn't find|did not find|0(\.00)?)\b", answer, re.IGNORECASE
        )
        invented = [a for a in _amounts(answer) if a not in (Decimal(0), Decimal(2026)) and a > 31]
        return bool(said_none) and not invented
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", expected):  # a date, in whatever format it is written
        from app.extraction.postprocess import parse_date

        return any(parse_date(text)[0] == expected for text in _DATE_TEXT.findall(answer))
    if re.fullmatch(r"\d[\d,]*(\.\d+)?", expected):
        return Decimal(expected.replace(",", "")) in _amounts(answer)
    return expected.lower() in answer.lower()


# Dates as an answer may write them: 2026-03-25, 25 March 2026, March 25, 2026, 25/03/2026.
_DATE_TEXT = re.compile(
    r"\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4}"
    r"|\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+\.?,?\s+\d{4}"
    r"|[A-Za-z]+\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}"
)


def citation_ok(expected: str, citations: list[dict]) -> bool:
    if expected == "-":
        return True
    filename, page = expected.rsplit(":", 1)
    return any(c.get("filename") == filename and str(c.get("page")) == page for c in citations)


def make_pdf(path: Path, pages: list[list[str]]) -> None:
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path))
    for lines in pages:
        for i, line in enumerate(lines):
            c.drawString(50, 800 - 20 * i, line)
        c.showPage()
    c.save()


async def run(keep: bool) -> dict:
    from alembic.config import Config
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    from alembic import command
    from app.assistant.agent import Event, run_turn
    from app.config import get_settings
    from app.db import create_engine, create_sessionmaker
    from app.extraction.llm import get_llm_client
    from app.models import Conversation
    from app.pipeline import process_document
    from app.repository import Repository
    from app.search.embedder import get_embedder

    settings = get_settings()
    llm = get_llm_client(settings)  # raises if no provider is configured
    name = f"assistant_eval_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(
        settings.database_url, poolclass=NullPool, isolation_level="AUTOCOMMIT"
    )
    async with admin.connect() as conn:
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = settings.database_url.rsplit("/", 1)[0] + f"/{name}"
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.attributes["database_url"] = url
    await asyncio.to_thread(command.upgrade, config, "head")

    engine = create_engine(url)
    sessions = create_sessionmaker(engine)
    embedder = get_embedder(settings)
    results = []
    try:
        async with sessions() as session:
            for filename, pages in CORPUS:  # build the corpus with the real pipeline
                path = Path(settings.upload_dir) / f"eval-{uuid.uuid4().hex}"
                path.parent.mkdir(parents=True, exist_ok=True)
                make_pdf(path, pages)
                data = path.read_bytes()
                doc = await Repository(session).create_document(
                    filename=filename,
                    sha256=hashlib.sha256(data).hexdigest(),
                    mime_type="application/pdf",
                    size_bytes=len(data),
                    storage_path=str(path),
                )
                await session.commit()
                outcome = await process_document(doc.id, session, llm, settings, embedder=embedder)
                print(f"corpus: {filename}: {outcome.status}", file=sys.stderr)

            for case in load_cases():
                conversation = Conversation(title=f"eval {case.number}")
                session.add(conversation)
                await session.commit()
                seen: list[Event] = []

                async def emit(event: Event, seen=seen) -> None:
                    seen.append(event)

                final = await run_turn(
                    session, conversation, case.question, [], llm, settings, embedder, emit
                )
                citations = final.meta.get("citations", [])
                results.append(
                    {
                        "number": case.number,
                        "question": case.question,
                        "expected": case.expected,
                        "answer": final.content,
                        "value_ok": value_ok(case.expected, final.content),
                        "citation_ok": citation_ok(case.citation, citations),
                        "tools": [u["name"] for u in final.meta.get("tools_used", [])],
                    }
                )
    finally:
        await engine.dispose()
        if not keep:
            async with admin.connect() as conn:
                await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await admin.dispose()
    return {
        "provider": llm.name,
        "model": getattr(llm, "model", None),
        "questions": len(results),
        "right_number": sum(r["value_ok"] for r in results),
        "right_citation": sum(r["citation_ok"] for r in results),
        "both_right": sum(r["value_ok"] and r["citation_ok"] for r in results),
        # Corpus extraction + all questions; multiply by the provider's price for cost.
        **llm.usage.as_dict(),
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--keep", action="store_true", help="keep the eval database")
    parser.add_argument("--out", type=Path, help="also write the full report (all answers) here")
    args = parser.parse_args()
    try:
        report = asyncio.run(run(args.keep))
    except ValueError as exc:
        print(f"Configure the LLM first: {exc}")
        return 2
    for r in report["results"]:
        mark = lambda ok: "ok " if ok else "NO "  # noqa: E731
        print(
            f"{r['number']:>2} number:{mark(r['value_ok'])} citation:{mark(r['citation_ok'])} "
            f"{r['question'][:55]:<55} tools={','.join(r['tools'])}"
        )
    print(
        f"\nright number: {report['right_number']}/{report['questions']}  "
        f"right citation: {report['right_citation']}/{report['questions']}  "
        f"both: {report['both_right']}/{report['questions']}  "
        f"tokens in/out: {report['input_tokens']}/{report['output_tokens']}"
    )
    print(json.dumps({k: v for k, v in report.items() if k != "results"}))
    if args.out:
        args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
