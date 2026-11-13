"""text_chunks with HNSW index; drop unused page_texts.embedding

Revision ID: 9c4e1a7b2d55
Revises: 7b1d2c9e4f10
Create Date: 2026-11-13
"""

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa

from alembic import op

revision: str = "9c4e1a7b2d55"
down_revision: str | None = "7b1d2c9e4f10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "text_chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.vector.VECTOR(dim=384), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("document_id", "page_number", "chunk_index"),
    )
    op.create_index("ix_text_chunks_document_id", "text_chunks", ["document_id"])
    # HNSW: approximate nearest-neighbour search; vector_cosine_ops = cosine distance.
    op.create_index(
        "ix_text_chunks_embedding", "text_chunks", ["embedding"], postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    # Embeddings now live per chunk; this per-page column was never used.
    op.drop_column("page_texts", "embedding")


def downgrade() -> None:
    op.add_column("page_texts", sa.Column(
        "embedding", pgvector.sqlalchemy.vector.VECTOR(dim=384), nullable=True))
    op.drop_index("ix_text_chunks_embedding", table_name="text_chunks")
    op.drop_index("ix_text_chunks_document_id", table_name="text_chunks")
    op.drop_table("text_chunks")
