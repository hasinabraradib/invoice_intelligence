"""Generate a synthetic batch PDF (default 1000 pages) and time the full pipeline on it.

Prints seconds per page, per-stage timings and segmentation accuracy against the known
answer, as JSON (for the evaluation report). Runs against DATABASE_URL and deletes the test
document afterwards unless --keep is given.

    python scripts/batch_benchmark.py --pages 1000              # fake LLM: pipeline cost
    python scripts/batch_benchmark.py --pages 50 --llm real     # real provider (costs money)
    python scripts/batch_benchmark.py --pages 100 --scanned     # image pages -> OCR path

Synthetic invoices are 1-4 pages long. Every first page has an "INVOICE" heading and an
invoice number; ~70% of multi-page invoices print "Page k of N", the rest have unmarked
continuation pages (these are the "unclear" pages that go to the LLM classifier).
"""

import argparse
import asyncio
import hashlib
import io
import json
import random
import re
import resource
import sys
import tempfile
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # make `app` importable

from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.utils import ImageReader  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import create_engine, create_sessionmaker  # noqa: E402
from app.extraction.llm import FakeLLMClient, get_llm_client  # noqa: E402
from app.models import Document, Invoice, ProcessingJob  # noqa: E402
from app.pipeline import process_document  # noqa: E402
from app.repository import Repository  # noqa: E402
from app.search.embedder import FakeEmbedder, LocalEmbedder  # noqa: E402

SUPPLIERS = ["ACME Supplies Ltd", "Globex Corp", "Initech", "Umbrella Trading", "Dhaka Power Co"]
MISSING = {"value": None, "status": "missing", "evidence": None, "page": None}


def build_batch(pages: int, seed: int) -> tuple[list[list[str]], list[tuple[int, int]]]:
    """Page texts and the true (page_start, page_end) of every invoice."""
    rng = random.Random(seed)  # noqa: S311  (test data, not security; fixed seed = same batch)
    texts: list[list[str]] = []
    truth: list[tuple[int, int]] = []
    number = 1000
    while len(texts) < pages:
        length = min(rng.choice([1, 1, 1, 2, 2, 3, 4]), pages - len(texts))
        marked = length > 1 and rng.random() < 0.7
        supplier = rng.choice(SUPPLIERS)
        number += 1
        start = len(texts) + 1
        for k in range(1, length + 1):
            lines = (
                [f"INVOICE  {supplier}", f"Invoice No: INV-{number}", "Invoice Date: 2026-03-15"]
                if k == 1
                else ["Continued from previous page"]
            )
            lines += [f"Item {i}  {rng.randint(1, 9)}  {rng.randint(1, 500)}.00" for i in range(6)]
            if k == length:
                lines.append(f"Total: {rng.randint(100, 9000)}.00")
            if marked:
                lines.append(f"Page {k} of {length}")
            texts.append(lines)
        truth.append((start, len(texts)))
    return texts, truth


def write_pdf(path: Path, texts: list[list[str]], scanned: bool) -> None:
    c = canvas.Canvas(str(path), pagesize=A4)
    for lines in texts:
        if scanned:  # draw the text into an image: no text layer, so OCR must read it
            from PIL import Image, ImageDraw, ImageFont

            image = Image.new("L", (1240, 1754), 255)
            draw = ImageDraw.Draw(image)
            font = ImageFont.load_default(size=28)
            for i, line in enumerate(lines):
                draw.text((100, 100 + 46 * i), line, fill=0, font=font)
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            c.drawImage(ImageReader(io.BytesIO(buffer.getvalue())), 0, 0, *A4)
        else:
            for i, line in enumerate(lines):
                c.drawString(50, 800 - 18 * i, line)
        c.showPage()
    c.save()


def fake_reply(system: str, user: str) -> dict:
    """A plausible model answer quoting the segment's own text (keeps validation realistic)."""
    if "split a batch" in system:  # page classifier: unmarked pages are continuations
        pages = [int(n) for n in re.findall(r"=== PAGE (\d+) ===", user)]
        return {"pages": [{"page": n, "starts_new_invoice": False} for n in pages]}
    page = int(re.search(r"=== PAGE (\d+)", user)[1])

    def found(pattern: str, label: str):
        match = re.search(pattern, user)
        if not match:
            return MISSING
        return {
            "value": match[1].strip(),
            "status": "found",
            "evidence": f"{label}{match[1].strip()}",
            "page": page,
        }

    reply = {n: MISSING for n in ("due_date", "customer_name", "currency", "subtotal", "tax")}
    return reply | {
        "invoice_number": found(r"Invoice No: (\S+)", "Invoice No: "),
        "invoice_date": found(r"Invoice Date: (\S+)", "Invoice Date: "),
        "supplier_name": found(r"INVOICE\s+(.+)", ""),
        "total": found(r"Total: (\S+)", "Total: "),
        "line_items": [],
    }


async def run(args, path: Path, data: bytes, truth, generate_seconds: float) -> dict:
    settings = get_settings()
    settings.ocr_enabled = args.scanned or settings.ocr_enabled
    llm = get_llm_client(settings) if args.llm == "real" else FakeLLMClient(fake_reply)
    embedder = {
        "off": None,
        "fake": FakeEmbedder(),
        "local": LocalEmbedder(settings.embedding_model),
    }[args.embed]
    engine = create_engine(settings.database_url)
    sessions = create_sessionmaker(engine)
    stage_times: dict[str, float] = {}
    async with sessions() as session:
        doc = await Repository(session).create_document(
            filename=path.name,
            sha256=hashlib.sha256(data).hexdigest(),
            mime_type="application/pdf",
            size_bytes=len(data),
            storage_path=str(path),
        )
        await session.commit()
        doc_id = doc.id

        # Record when each stage starts (wrap the job update the pipeline already does).
        original = Repository.update_job
        marks: list[tuple[str, float]] = []

        async def timed(self, job_id, **values):
            if values.get("stage") and (not marks or marks[-1][0] != values["stage"]):
                marks.append((values["stage"], time.perf_counter()))
            return await original(self, job_id, **values)

        Repository.update_job = timed
        started = time.perf_counter()
        outcome = await process_document(doc_id, session, llm, settings, embedder=embedder)
        total = time.perf_counter() - started
        Repository.update_job = original
        for (name, at), (_, nxt) in zip(marks, [*marks[1:], ("end", started + total)], strict=True):
            stage_times[name] = round(stage_times.get(name, 0) + nxt - at, 2)

        found = [
            (i.page_start, i.page_end)
            for i in (
                await session.scalars(
                    select(Invoice)
                    .where(Invoice.document_id == doc_id)
                    .order_by(Invoice.segment_index)
                )
            ).all()
        ]
        job = await session.scalar(select(ProcessingJob).where(ProcessingJob.document_id == doc_id))
        segments = job.segments or []
        if not args.keep:
            await session.delete(await session.get(Document, doc_id))
            await session.commit()
    await engine.dispose()

    exact = len(set(found) & set(truth))
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss  # KiB on Linux, bytes on macOS
    return {
        "pages": args.pages,
        "scanned": args.scanned,
        "llm": args.llm,
        "embed": args.embed,
        "segment_concurrency": settings.segment_concurrency,
        "status": str(outcome.status),
        "error": outcome.error,
        "invoices_true": len(truth),
        "invoices_found": len(found),
        "segments_exactly_right": exact,
        "segmentation_precision": round(exact / len(found), 4) if found else 0,
        "segmentation_recall": round(exact / len(truth), 4),
        "pages_decided_by_llm_or_default": sum(
            1 for s in segments for note in s.get("notes", []) if "page" in note
        ),
        "generate_seconds": round(generate_seconds, 2),
        "process_seconds": round(total, 2),
        "seconds_per_page": round(total / args.pages, 4),
        "pages_per_minute": round(args.pages / total * 60, 1),
        "stage_seconds": stage_times,
        "peak_rss_mb": round((peak if sys.platform == "darwin" else peak * 1024) / 1e6, 1),
        **llm.usage.as_dict(),  # zero with the fake LLM
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pages", type=int, default=1000)
    parser.add_argument("--llm", choices=["fake", "real"], default="fake")
    parser.add_argument("--embed", choices=["off", "fake", "local"], default="off")
    parser.add_argument("--scanned", action="store_true", help="image-only pages (OCR path)")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--keep", action="store_true", help="keep the document in the database")
    args = parser.parse_args()

    texts, truth = build_batch(args.pages, args.seed)
    workdir = Path(tempfile.mkdtemp(dir=get_settings().upload_dir))
    path = workdir / f"benchmark-{uuid.uuid4().hex}.pdf"
    started = time.perf_counter()
    write_pdf(path, texts, args.scanned)
    generate_seconds = time.perf_counter() - started
    try:
        report = asyncio.run(run(args, path, path.read_bytes(), truth, generate_seconds))
    finally:
        if not args.keep:
            path.unlink(missing_ok=True)
            workdir.rmdir()
    print(json.dumps(report, indent=2))
    return 0 if report["status"] in ("processed", "needs_review") else 1


if __name__ == "__main__":
    raise SystemExit(main())
