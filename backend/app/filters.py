"""Filters shared by GET /documents and GET /search (so both behave the same)."""

from dataclasses import dataclass
from datetime import date

from sqlalchemy import Select

from app.extraction.postprocess import normalise_supplier_key
from app.models import Document, DocumentStatus, Invoice


@dataclass(frozen=True)
class DocumentFilters:
    status: DocumentStatus | None = None
    needs_review: bool | None = None
    supplier: str | None = None  # matched loosely: "acme" finds "ACME Supplies Ltd"
    date_from: date | None = None  # invoice date, inclusive
    date_to: date | None = None

    def apply(self, query: Select) -> Select:
        """Add WHERE clauses. The query must already join Document and (outer) Invoice."""
        if self.status:
            query = query.where(Document.status == self.status)
        if self.needs_review is not None:
            query = query.where(Invoice.needs_review.is_(self.needs_review))
        if self.supplier and (key := normalise_supplier_key(self.supplier)):
            query = query.where(Invoice.supplier_key.contains(key, autoescape=True))
        if self.date_from:
            query = query.where(Invoice.invoice_date >= self.date_from)
        if self.date_to:
            query = query.where(Invoice.invoice_date <= self.date_to)
        return query
