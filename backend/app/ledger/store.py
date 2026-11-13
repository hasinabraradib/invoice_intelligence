"""Save ledger rows and (re)apply column mappings. Database side of app.ledger.mapping."""

import uuid
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ledger.mapping import LEDGER_FIELDS, is_ledger, map_columns, row_values
from app.models import Document, LedgerRow
from app.parsing.models import ParsedSheet


def _clean(value: Any) -> Any:
    return value.replace("\x00", "") if isinstance(value, str) else value


def _apply(row: LedgerRow, mapping: dict[str, str | None], ledger: bool) -> None:
    values = row_values(row.cells, mapping) if ledger else None
    for name in (
        "invoice_number",
        "invoice_number_key",
        "invoice_date",
        "supplier_name",
        "supplier_key",
        "total",
        "tax",
        "currency",
    ):
        setattr(row, name, _clean(getattr(values, name)) if values else None)
    row.problems = values.problems if values else []


async def save_ledgers(
    session: AsyncSession, document: Document, sheets: list[ParsedSheet]
) -> set[int]:
    """Store every sheet row (raw cells + mapped values for ledger sheets) and the mapping.
    Manual mappings from an earlier run are kept. Returns the page numbers of ledger sheets
    (sheets are numbered 1, 2, ... like their page texts). Caller commits."""
    previous = (document.ledger_mapping or {}).get("sheets", {})
    await session.execute(delete(LedgerRow).where(LedgerRow.document_id == document.id))
    entries: dict[str, dict[str, Any]] = {}
    ledger_pages: set[int] = set()
    for page, sheet in enumerate(sheets, start=1):
        manual = previous.get(sheet.name, {})
        if manual.get("source") == "manual":
            columns = {k: v for k, v in manual["columns"].items() if v in sheet.columns}
            ledger = manual["is_ledger"]
        else:
            columns = map_columns(sheet.columns)
            ledger = is_ledger(columns, len(sheet.rows))
        entries[sheet.name] = {
            "page": page,
            "is_ledger": ledger,
            "source": manual.get("source", "auto"),
            "columns": {name: columns.get(name) for name in LEDGER_FIELDS},
            "available_columns": sheet.columns,
            "row_count": len(sheet.rows),
        }
        if ledger:
            ledger_pages.add(page)
        numbers = sheet.row_numbers or list(range(1, len(sheet.rows) + 1))
        for cells, number in zip(sheet.rows, numbers, strict=False):
            row = LedgerRow(
                document_id=document.id,
                sheet_name=sheet.name,
                row_number=number,
                cells={k: _clean(v) for k, v in cells.items()},
            )
            _apply(row, columns, ledger)
            session.add(row)
    document.ledger_mapping = {"sheets": entries}
    await session.flush()
    return ledger_pages


class MappingError(ValueError):
    pass


async def set_mapping(
    session: AsyncSession,
    document: Document,
    sheet: str,
    columns: dict[str, str | None],
    ledger: bool | None,
) -> dict[str, Any]:
    """A user's correction: re-map the stored rows of one sheet (no re-parsing). Fields not
    given keep their current column; None clears a field. Caller commits."""
    sheets = dict((document.ledger_mapping or {}).get("sheets", {}))
    if sheet not in sheets:
        raise MappingError(f"Sheet {sheet!r} does not exist in this document.")
    entry = dict(sheets[sheet])
    unknown = [c for c in columns.values() if c is not None and c not in entry["available_columns"]]
    if unknown:
        raise MappingError(f"Unknown column(s): {', '.join(map(repr, unknown))}.")
    merged = {**entry["columns"], **columns}
    entry.update(
        columns=merged,
        source="manual",
        is_ledger=is_ledger(merged, entry["row_count"]) if ledger is None else ledger,
    )
    sheets[sheet] = entry
    document.ledger_mapping = {"sheets": sheets}  # new dict: SQLAlchemy sees the change
    rows = await session.scalars(
        select(LedgerRow).where(LedgerRow.document_id == document.id, LedgerRow.sheet_name == sheet)
    )
    for row in rows:
        _apply(row, merged, entry["is_ledger"])
    await session.flush()
    return entry


def ledger_pages(document: Document) -> set[int]:
    """Page numbers of the ledger sheets (empty: not a ledger document)."""
    sheets = (document.ledger_mapping or {}).get("sheets", {})
    return {entry["page"] for entry in sheets.values() if entry.get("is_ledger")}


async def ledger_rows(session: AsyncSession, document_id: uuid.UUID) -> list[LedgerRow]:
    """Rows of the document's LEDGER sheets only, in sheet/row order."""
    document = await session.get(Document, document_id)
    ledger_sheets = {
        name
        for name, entry in (
            (document.ledger_mapping or {}).get("sheets", {}) if document else {}
        ).items()
        if entry["is_ledger"]
    }
    if not ledger_sheets:
        return []
    rows = await session.scalars(
        select(LedgerRow)
        .where(LedgerRow.document_id == document_id, LedgerRow.sheet_name.in_(ledger_sheets))
        .order_by(LedgerRow.sheet_name, LedgerRow.row_number)
    )
    return list(rows)
