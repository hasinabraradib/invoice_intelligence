"""Search: keyword (Postgres full text), semantic (pgvector) and hybrid (RRF of both)."""

from app.search.service import SearchHit, SearchMode, SemanticUnavailable, search

__all__ = ["SearchHit", "SearchMode", "SemanticUnavailable", "search"]
