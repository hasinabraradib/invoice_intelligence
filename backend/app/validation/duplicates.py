"""Duplicate detection. Only FLAGS duplicates (as issues); it never blocks or deletes."""

import uuid
from datetime import date
from decimal import Decimal
from typing import Protocol

from app.validation.rules import ValidationIssue


class DuplicateLookup(Protocol):
    """The repository methods this module needs (lets tests use a simple fake)."""

    async def find_invoices_by_supplier_and_number(
        self, supplier_key: str, invoice_number: str, exclude_invoice_id: uuid.UUID | None
    ) -> list[uuid.UUID]: ...

    async def find_invoices_by_supplier_date_total(
        self,
        supplier_key: str,
        invoice_date: date,
        total: Decimal,
        exclude_invoice_id: uuid.UUID | None,
    ) -> list[uuid.UUID]: ...


async def find_duplicate_issues(
    repo: DuplicateLookup,
    *,
    invoice_id: uuid.UUID | None,
    supplier_key: str | None,
    invoice_number: str | None,
    invoice_date: date | None,
    total: Decimal | None,
) -> list[ValidationIssue]:
    """Same supplier + invoice number -> error; same supplier, date and total (not already
    caught by the number check) -> warning. Results point at the other DOCUMENT ids;
    only the invoice itself is excluded, so a copy inside the same batch PDF is found too."""
    if not supplier_key:
        return []
    issues: list[ValidationIssue] = []
    same_number: list[uuid.UUID] = []
    if invoice_number:
        same_number = await repo.find_invoices_by_supplier_and_number(
            supplier_key, invoice_number.strip(), invoice_id
        )
        if same_number:
            issues.append(
                ValidationIssue(
                    code="DUPLICATE_INVOICE",
                    severity="error",
                    field="invoice_number",
                    message="another document has the same supplier and invoice number",
                    actual=", ".join(map(str, same_number)),
                )
            )
    if invoice_date and total is not None:
        similar = [
            other
            for other in await repo.find_invoices_by_supplier_date_total(
                supplier_key, invoice_date, total, invoice_id
            )
            if other not in same_number
        ]
        if similar:
            issues.append(
                ValidationIssue(
                    code="POSSIBLE_DUPLICATE",
                    severity="warning",
                    field="total",
                    message="another document has the same supplier, date and total",
                    actual=", ".join(map(str, similar)),
                )
            )
    return issues
