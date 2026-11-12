"""Spreadsheet parsing: every sheet, header detection, raw strings, warnings, caps."""

import pytest

from app.parsing.errors import CorruptFile, EmptyFile
from app.parsing.models import ParseLimits
from app.parsing.spreadsheet_parser import parse_csv, parse_xls, parse_xlsx
from tests import factories


def test_xlsx_reads_all_sheets_and_finds_header_below_title_rows(tmp_path):
    doc = parse_xlsx(factories.invoice_xlsx(tmp_path / "invoice.xlsx"))

    assert [s.name for s in doc.sheets] == ["Invoice", "Notes"]
    assert any("'Empty' is empty" in w for w in doc.warnings)

    invoice = doc.sheets[0]
    assert invoice.header_row_index == 3  # Excel row 4
    assert invoice.columns == ["Description", "Qty", "Unit price", "Amount"]
    assert invoice.preamble_rows == [["ACME Supplies Ltd"], ["Invoice no:", "INV-2024-001"]]
    assert invoice.rows == [  # whitespace stripped, empty row dropped, all values strings
        {"Description": "Bolts M8", "Qty": "3", "Unit price": "1.50", "Amount": "4.50"},
        {"Description": "Nuts M8", "Qty": "10", "Unit price": "0.10", "Amount": "1.00"},
    ]
    assert any("1 merged cell range" in w for w in invoice.warnings)

    notes = doc.sheets[1]
    assert notes.header_row_index == 0
    assert notes.rows == [{"Key": "Payment terms", "Value": "30 days"}]
    assert notes.warnings == []


def test_xlsx_duplicate_columns_and_empty_column(tmp_path):
    sheet = parse_xlsx(factories.duplicate_columns_xlsx(tmp_path / "dup.xlsx")).sheets[0]

    assert sheet.columns == ["Item", "Amount", "Amount_2", "Tax"]  # empty column D dropped
    assert sheet.rows == [{"Item": "Bolt", "Amount": "1.00", "Amount_2": "2.00", "Tax": "0.19"}]
    assert any("Duplicate column names" in w and "Amount" in w for w in sheet.warnings)


def test_xls_reads_all_sheets(tmp_path):
    doc = parse_xls(factories.invoice_xls(tmp_path / "legacy.xls"))

    assert [s.name for s in doc.sheets] == ["Invoice", "Summary"]
    invoice = doc.sheets[0]
    assert invoice.header_row_index == 2
    assert invoice.columns == ["Description", "Qty", "Amount"]
    # xls stores numbers as floats; integral ones come back without ".0"
    assert invoice.rows == [{"Description": "Bolts M8", "Qty": "3", "Amount": "4.50"}]
    assert any("merged cell" in w for w in invoice.warnings)
    assert doc.sheets[1].rows == [{"Total": "4.50", "Currency": "EUR"}]


def test_semicolon_csv_with_bom(tmp_path):
    doc = parse_csv(factories.semicolon_csv_with_bom(tmp_path / "invoice.csv"))

    sheet = doc.sheets[0]
    assert sheet.header_row_index == 1
    assert sheet.columns == ["Description", "Qty", "Unit price", "Amount"]  # no BOM in name
    assert sheet.preamble_rows == [["ACME Supplies Ltd"]]
    assert sheet.rows == [
        {"Description": "Bolts; galvanised", "Qty": "3", "Unit price": "1,50", "Amount": "4,50"},
        {"Description": 'Nuts "M8"', "Qty": "10", "Unit price": "0,10", "Amount": "1,00"},
    ]
    assert doc.warnings == []


def test_cp1252_csv_is_decoded(tmp_path):
    path = tmp_path / "latin.csv"
    lines = ["Artikel,Menge,Preis"] + [f"Größe {i} Müller Straße,{i},{i}€" for i in range(30)]
    path.write_bytes("\n".join(lines).encode("cp1252"))

    doc = parse_csv(path)

    assert doc.sheets[0].rows[0] == {
        "Artikel": "Größe 0 Müller Straße",
        "Menge": "0",
        "Preis": "0€",
    }
    assert any("decoded as" in w for w in doc.warnings)


def test_tab_csv_and_caps(tmp_path):
    path = tmp_path / "wide.tsv"
    header = "\t".join(f"col{i}" for i in range(8))
    body = "\n".join("\t".join(f"v{r}{c}" for c in range(8)) for r in range(20))
    path.write_text(header + "\n" + body + "\n", encoding="utf-8")

    sheet = parse_csv(path, ParseLimits(max_sheet_rows=5, max_sheet_columns=3)).sheets[0]

    assert sheet.columns == ["col0", "col1", "col2"]
    assert len(sheet.rows) == 5
    assert any("columns" in w for w in sheet.warnings)
    assert any("data rows" in w for w in sheet.warnings)


def test_no_header_row_uses_generic_column_names(tmp_path):
    path = tmp_path / "numbers.csv"
    path.write_text("1,2,3\n4,5,6\n", encoding="utf-8")

    sheet = parse_csv(path).sheets[0]

    assert sheet.header_row_index is None
    assert sheet.columns == ["column_1", "column_2", "column_3"]
    assert sheet.rows[0] == {"column_1": "1", "column_2": "2", "column_3": "3"}


def test_csv_with_only_empty_rows_is_empty(tmp_path):
    path = tmp_path / "blank.csv"
    path.write_text(";;\n;;\n", encoding="utf-8")
    with pytest.raises(EmptyFile):
        parse_csv(path)


def test_garbage_ole_file_is_corrupt(tmp_path):
    path = tmp_path / "fake.xls"
    path.write_bytes(bytes.fromhex("D0CF11E0A1B11AE1") + b"\x00" * 600)
    with pytest.raises(CorruptFile):
        parse_xls(path)
