"""Semantic + hybrid search and the embedding stage, on the test database."""

import hashlib

import pytest
from reportlab.pdfgen import canvas
from sqlalchemy import func, select

import app.models as m
from app.extraction.llm import FakeLLMClient
from app.filters import DocumentFilters
from app.pipeline import process_document
from app.repository import Repository
from app.search import search
from app.search.embedder import FakeEmbedder
from app.search.indexing import documents_missing_chunks, index_document

pytestmark = pytest.mark.anyio


def _f(value, evidence=None):
    return {"value": value, "status": "found", "evidence": evidence or value, "page": 1}


MISSING = {"value": None, "status": "missing", "evidence": None, "page": None}


def invoice(tmp_path, name, supplier, number, day, total, body):
    """A one-page PDF and the matching model reply (every value is really in the text)."""
    lines = [supplier, f"Invoice {number}", f"Invoice date: {day}", body, f"Total: {total}"]
    path = tmp_path / f"{name}.pdf"
    c = canvas.Canvas(str(path))
    for i, line in enumerate(lines):
        c.drawString(50, 800 - 20 * i, line)
    c.save()
    reply = {name: MISSING for name in ("due_date", "customer_name", "currency", "subtotal", "tax")}
    reply |= {
        "invoice_number": _f(number, f"Invoice {number}"),
        "invoice_date": _f(day, f"Invoice date: {day}"),
        "supplier_name": _f(supplier),
        "total": _f(total, f"Total: {total}"),
        "line_items": [],
    }
    return path, reply


async def _ingest(session, path, reply, embedder, settings):
    data = path.read_bytes()
    doc = await Repository(session).create_document(
        filename=path.name,
        sha256=hashlib.sha256(data).hexdigest(),
        mime_type="application/pdf",
        size_bytes=len(data),
        storage_path=str(path),
    )
    await session.commit()
    doc_id = doc.id  # read before processing: a rollback inside it expires `doc`
    outcome = await process_document(
        doc_id, session, FakeLLMClient(reply), settings, embedder=embedder
    )
    return doc_id, outcome


@pytest.fixture
async def corpus(db_session, tmp_path, settings):
    """Three invoices from two suppliers on different dates."""
    embedder = FakeEmbedder()
    docs = {}
    for key, args in {
        "power": (
            "Dhaka Power Co",
            "DP-1",
            "15 January 2026",
            "1,200.00",
            "Electricity bill for the office, zebrawood meter",
        ),
        "chairs": (
            "ACME Supplies Ltd",
            "A-7",
            "3 March 2026",
            "900.00",
            "Office chairs and desks delivered",
        ),
        "paper": (
            "ACME Supplies Ltd",
            "A-8",
            "4 April 2026",
            "50.00",
            "Printer paper, office supplies",
        ),
    }.items():
        path, reply = invoice(tmp_path, key, *args)
        docs[key], _ = await _ingest(db_session, path, reply, embedder, settings)
    return docs


# Bag-of-words fake vectors score lower than a real model: one shared word with a ~20-word
# chunk is ~0.22, so tests with FakeEmbedder use 0.15 (a stray hash collision is ~0.13).
FAKE_MIN_SIMILARITY = 0.15


async def _search(session, q, mode, embedder=None, min_similarity=FAKE_MIN_SIMILARITY, **filters):
    hits, total = await search(
        session,
        q,
        DocumentFilters(**filters),
        limit=20,
        offset=0,
        mode=mode,
        embedder=embedder or FakeEmbedder(),
        min_similarity=min_similarity,
    )
    return hits, total


async def test_reprocessing_replaces_chunks(db_session, tmp_path, settings):
    path, reply = invoice(
        tmp_path, "a", "Dhaka Power Co", "DP-1", "15 January 2026", "1,200.00", "Electricity bill"
    )
    doc_id, outcome = await _ingest(db_session, path, reply, FakeEmbedder(), settings)
    first = (await db_session.scalars(select(m.TextChunk.id))).all()

    await process_document(
        doc_id, db_session, FakeLLMClient(reply), settings, embedder=FakeEmbedder()
    )

    second = (await db_session.scalars(select(m.TextChunk.id))).all()
    assert outcome.status == m.DocumentStatus.PROCESSED
    assert len(first) == len(second) == 1 and set(first).isdisjoint(second)  # replaced
    chunk = await db_session.scalar(select(m.TextChunk))
    assert chunk.page_number == 1 and "Electricity bill" in chunk.text


async def test_embedding_failure_keeps_the_result(db_session, tmp_path, settings):
    path, reply = invoice(
        tmp_path, "a", "Dhaka Power Co", "DP-1", "15 January 2026", "1,200.00", "Electricity bill"
    )
    doc_id, outcome = await _ingest(db_session, path, reply, FakeEmbedder(fail=True), settings)

    document = await db_session.get(m.Document, doc_id, populate_existing=True)
    job = await db_session.scalar(select(m.ProcessingJob))
    assert outcome.status == document.status == m.DocumentStatus.PROCESSED
    assert job.status == m.JobStatus.SUCCEEDED and job.stage == "embedding"
    assert job.error.startswith("warning: semantic search index not updated")
    assert await db_session.scalar(select(func.count()).select_from(m.Invoice)) == 1
    assert await db_session.scalar(select(func.count()).select_from(m.TextChunk)) == 0
    hits, _ = await _search(db_session, "electricity", "keyword")
    assert [h.document_id for h in hits] == [doc_id]  # keyword search still works


async def test_matched_by_and_snippets(db_session, corpus):
    hits, _ = await _search(db_session, "zebrawood", "hybrid")
    assert hits[0].document_id == corpus["power"] and hits[0].matched_by == "both"
    assert "<mark>zebrawood</mark>" in hits[0].snippet  # keyword snippet preferred

    # Keyword needs ALL words; the semantic side still finds the shared word.
    # 1 of 2 query words shared: ~0.14 with the fake embedder, so a lower bar here.
    hits, total = await _search(db_session, "zebrawood unicorn", "hybrid", min_similarity=0.1)
    assert total == 1 and hits[0].matched_by == "semantic"
    assert "<mark>" not in hits[0].snippet and "Electricity bill" in hits[0].snippet
    assert (await _search(db_session, "zebrawood unicorn", "keyword"))[1] == 0


async def test_one_hit_per_document_best_first(db_session, corpus):
    hits, total = await _search(db_session, "office", "semantic")
    assert total == len({h.document_id for h in hits}) == 3
    assert hits == sorted(hits, key=lambda h: -h.score)
    assert all(h.matched_by == "semantic" and 0 < h.score <= 1 for h in hits)


@pytest.mark.parametrize("mode", ["keyword", "semantic", "hybrid"])
async def test_filters_work_in_every_mode(db_session, corpus, mode):
    async def found(**filters):
        hits, _ = await _search(db_session, "office", mode, **filters)
        return {h.document_id for h in hits}

    assert await found() == set(corpus.values())
    assert await found(supplier="acme") == {corpus["chairs"], corpus["paper"]}
    assert await found(supplier="dhaka power") == {corpus["power"]}
    from datetime import date

    assert await found(date_from=date(2026, 3, 1), date_to=date(2026, 3, 31)) == {corpus["chairs"]}
    assert await found(status=m.DocumentStatus.FAILED) == set()


async def test_pagination_after_grouping(db_session, corpus):
    first, total = await search(
        db_session,
        "office",
        DocumentFilters(),
        limit=2,
        offset=0,
        mode="hybrid",
        embedder=FakeEmbedder(),
        min_similarity=FAKE_MIN_SIMILARITY,
    )
    rest, _ = await search(
        db_session,
        "office",
        DocumentFilters(),
        limit=2,
        offset=2,
        mode="hybrid",
        embedder=FakeEmbedder(),
        min_similarity=FAKE_MIN_SIMILARITY,
    )
    assert total == 3 and len(first) == 2 and len(rest) == 1
    assert {h.document_id for h in first}.isdisjoint({h.document_id for h in rest})


async def test_api_modes_and_unavailable_semantic(api, run_job, tmp_path):
    path, reply = invoice(
        tmp_path, "a", "Dhaka Power Co", "DP-1", "15 January 2026", "1,200.00", "Electricity bill"
    )
    uploaded = (
        await api.post("/documents", files=[("files", ("a.pdf", path.read_bytes()))])
    ).json()["results"][0]
    await run_job(uploaded["job_id"], FakeLLMClient(reply))

    for mode in ("keyword", "semantic", "hybrid"):
        page = (await api.get("/search", params={"q": "electricity", "mode": mode})).json()
        assert page["total"] == 1, mode
        hit = page["items"][0]
        assert hit["document_id"] == uploaded["document_id"] and hit["page_number"] == 1
        assert hit["supplier_name"] == "Dhaka Power Co" and hit["invoice_number"] == "DP-1"
    assert (await api.get("/search", params={"q": "x", "mode": "fuzzy"})).status_code == 422


async def test_semantic_mode_without_embeddings_is_503_hybrid_falls_back(api, run_job, tmp_path):
    from app.api.deps import get_embedder

    api._transport.app.dependency_overrides[get_embedder] = lambda: None
    path, reply = invoice(
        tmp_path, "a", "Dhaka Power Co", "DP-1", "15 January 2026", "1,200.00", "Electricity bill"
    )
    uploaded = (
        await api.post("/documents", files=[("files", ("a.pdf", path.read_bytes()))])
    ).json()["results"][0]
    await run_job(uploaded["job_id"], FakeLLMClient(reply))

    response = await api.get("/search", params={"q": "electricity", "mode": "semantic"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "semantic_unavailable"
    hybrid = (await api.get("/search", params={"q": "electricity"})).json()
    assert hybrid["total"] == 1 and hybrid["items"][0]["matched_by"] == "keyword"


async def test_backfill_finds_and_indexes_old_documents(db_session, tmp_path, settings):
    path, reply = invoice(
        tmp_path, "old", "Dhaka Power Co", "DP-1", "15 January 2026", "1,200.00", "Electricity bill"
    )
    doc_id, _ = await _ingest(db_session, path, reply, None, settings)  # no embedder: old

    assert await documents_missing_chunks(db_session) == [doc_id]
    assert await index_document(db_session, doc_id, FakeEmbedder()) == 1
    await db_session.commit()
    assert await documents_missing_chunks(db_session) == []


@pytest.mark.slow
async def test_real_model_finds_meaning_not_just_words(db_session, tmp_path, settings):
    """The point of semantic search: no shared words, same meaning."""
    from app.search.embedder import LocalEmbedder

    embedder = LocalEmbedder(settings.embedding_model)
    power_path, power = invoice(
        tmp_path,
        "power",
        "Dhaka Utilities",
        "U-1",
        "1 May 2026",
        "3,400.00",
        "Monthly electricity bill for the warehouse",
    )
    chair_path, chairs = invoice(
        tmp_path,
        "chairs",
        "ACME Supplies Ltd",
        "A-1",
        "2 May 2026",
        "900.00",
        "Ergonomic office chairs, 4 pieces",
    )
    power_id, _ = await _ingest(db_session, power_path, power, embedder, settings)
    await _ingest(db_session, chair_path, chairs, embedder, settings)

    from app.config import Settings

    real = Settings.model_fields["semantic_min_similarity"].default  # production: 0.20
    semantic, _ = await _search(db_session, "power charges", "semantic", embedder, real)
    keyword, keyword_total = await _search(db_session, "power charges", "keyword", embedder, real)

    assert semantic[0].document_id == power_id
    assert semantic[0].score > (semantic[1].score if len(semantic) > 1 else 0)
    assert keyword_total == 0  # neither "power" nor "charges" appears in any document
