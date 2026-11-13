"""Build a document's semantic index: page texts -> chunks -> embeddings -> text_chunks."""

import uuid

import anyio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Document, DocumentStatus, PageText, TextChunk
from app.search.chunking import chunk_pages
from app.search.embedder import DIM, Embedder


async def index_document(
    session: AsyncSession,
    document_id: uuid.UUID,
    embedder: Embedder,
    *,
    chunk_tokens: int = 500,
    overlap_tokens: int = 50,
) -> int:
    """Replace this document's chunks (idempotent: re-running never adds duplicates).
    Reads the page texts already saved for it. Returns the number of chunks. The caller
    commits."""
    rows = await session.execute(
        select(PageText.page_number, PageText.text)
        .where(PageText.document_id == document_id)
        .order_by(PageText.page_number)
    )
    chunks = chunk_pages([(r.page_number, r.text) for r in rows], chunk_tokens, overlap_tokens)
    # The model is CPU-heavy and synchronous: run it in a thread.
    vectors = await anyio.to_thread.run_sync(embedder.embed, [c.text for c in chunks])
    if len(vectors) != len(chunks) or any(len(v) != DIM for v in vectors):
        raise ValueError("embedder returned vectors of the wrong number or size")

    await session.execute(delete(TextChunk).where(TextChunk.document_id == document_id))
    session.add_all(
        TextChunk(
            document_id=document_id,
            page_number=c.page_number,
            chunk_index=c.chunk_index,
            text=c.text,
            embedding=v,
        )
        for c, v in zip(chunks, vectors, strict=True)
    )
    await session.flush()
    return len(chunks)


async def documents_missing_chunks(session: AsyncSession) -> list[uuid.UUID]:
    """Processed documents with page text but no chunks (e.g. processed before step 5)."""
    has_chunks = select(TextChunk.document_id).where(TextChunk.document_id == Document.id)
    has_pages = select(PageText.document_id).where(PageText.document_id == Document.id)
    rows = await session.scalars(
        select(Document.id)
        .where(
            Document.status.in_([DocumentStatus.PROCESSED, DocumentStatus.NEEDS_REVIEW]),
            has_pages.exists(),
            ~has_chunks.exists(),
        )
        .order_by(Document.created_at)
    )
    return list(rows)
