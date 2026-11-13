"""Filters shared by GET /documents and GET /search (so both behave the same).

A document can hold many invoices, so the invoice filters mean:
- documents list: the document has at least one matching invoice (EXISTS);
- search: the invoice on the matching page matches (the query joins that invoice).
"""

from dataclasses import dataclass
from datetime import date

from sqlalchemy import ColumnElement, Select, and_, exists, not_, select

from app.extraction.postprocess import normalise_supplier_key
from app.models import Document, DocumentStatus, Invoice


@dataclass(frozen=True)
class DocumentFilters:
    status: DocumentStatus | None = None
    needs_review: bool | None = None
    supplier: str | None = None  # matched loosely: "acme" finds "ACME Supplies Ltd"
    date_from: date | None = None  # invoice date, inclusive
    date_to: date | None = None

    def _invoice_conditions(self) -> list[ColumnElement[bool]]:
        conditions = []
        if self.supplier and (key := normalise_supplier_key(self.supplier)):
            conditions.append(Invoice.supplier_key.contains(key, autoescape=True))
        if self.date_from:
            conditions.append(Invoice.invoice_date >= self.date_from)
        if self.date_to:
            conditions.append(Invoice.invoice_date <= self.date_to)
        return conditions

    def apply(self, query: Select) -> Select:
        """For queries over documents (one row per document)."""
        if self.status:
            query = query.where(Document.status == self.status)
        on_document = Invoice.document_id == Document.id
        if conditions := self._invoice_conditions():
            query = query.where(exists(select(Invoice.id).where(on_document, *conditions)))
        if self.needs_review is True:
            query = query.where(exists(select(Invoice.id).where(on_document, Invoice.needs_review)))
        elif self.needs_review is False:  # has invoices, and none needs review
            query = query.where(
                exists(select(Invoice.id).where(on_document)),
                not_(exists(select(Invoice.id).where(on_document, Invoice.needs_review))),
            )
        return query

    def apply_joined(self, query: Select) -> Select:
        """For queries that already join ONE invoice per row (search hits)."""
        if self.status:
            query = query.where(Document.status == self.status)
        if self.needs_review is not None:
            query = query.where(Invoice.needs_review.is_(self.needs_review))
        conditions = self._invoice_conditions()
        return query.where(and_(*conditions)) if conditions else query
