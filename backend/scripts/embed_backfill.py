"""Build the semantic index for documents processed before semantic search existed.

Usage (see `make embed-backfill`):  python scripts/embed_backfill.py
Safe to run again: documents that already have chunks are skipped, and indexing a
document replaces its chunks instead of adding more.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # make `app` importable

from app.config import get_settings  # noqa: E402
from app.db import create_engine, create_sessionmaker  # noqa: E402
from app.search.embedder import get_embedder  # noqa: E402
from app.search.indexing import documents_missing_chunks, index_document  # noqa: E402


async def main() -> int:
    settings = get_settings()
    embedder = get_embedder(settings)
    if embedder is None:
        print("EMBEDDINGS_ENABLED is false; nothing to do.")
        return 1
    engine = create_engine(settings.database_url)
    sessions = create_sessionmaker(engine)
    done = failed = 0
    async with sessions() as session:
        todo = await documents_missing_chunks(session)
        print(f"{len(todo)} document(s) need embeddings")
        for document_id in todo:
            try:
                count = await index_document(
                    session,
                    document_id,
                    embedder,
                    chunk_tokens=settings.embed_chunk_tokens,
                    overlap_tokens=settings.embed_chunk_overlap,
                )
                await session.commit()
                done += 1
                print(f"  {document_id}: {count} chunk(s)")
            except Exception as exc:  # keep going; report at the end
                await session.rollback()
                failed += 1
                print(f"  {document_id}: FAILED ({type(exc).__name__})")
    await engine.dispose()
    print(f"done: {done} indexed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
