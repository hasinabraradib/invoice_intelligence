"""Build test files in code, so no binary fixtures are committed to git.

Each helper writes a file to the given path and returns that path.
"""

import io
from pathlib import Path

import openpyxl
import xlwt
from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

INVOICE_LINES = [
    "ACME Supplies Ltd - Invoice INV-2024-001",
    "Invoice date: 15 January 2024    Due date: 14 February 2024",
    "Bill to: Example Customer GmbH, Hauptstrasse 1, Berlin",
    "Bolts M8 x 100      3 x 1.50 EUR      4.50 EUR",
    "Total due: 4.50 EUR",
]


def text_pdf(path: Path, pages: int = 2) -> Path:
    c = canvas.Canvas(str(path), pagesize=A4)
    for number in range(1, pages + 1):
        y = 800
        for line in INVOICE_LINES:
            c.drawString(50, y, line)
            y -= 20
        c.drawString(50, y, f"Page {number} of {pages}")
        c.showPage()
    c.save()
    return path


def pdf_with_image_page(path: Path) -> Path:
    """Page 1 has text; page 2 contains only a picture (like a scanned page)."""
    c = canvas.Canvas(str(path), pagesize=A4)
    for i, line in enumerate(INVOICE_LINES):
        c.drawString(50, 800 - 20 * i, line)
    c.showPage()
    picture = Image.new("RGB", (400, 300), "white")
    buffer = io.BytesIO()
    picture.save(buffer, format="PNG")
    c.drawImage(ImageReader(io.BytesIO(buffer.getvalue())), 50, 400, width=400, height=300)
    c.showPage()
    c.save()
    return path


def encrypted_pdf(path: Path) -> Path:
    c = canvas.Canvas(str(path), encrypt="s3cret")  # reportlab sets a user password
    c.drawString(50, 800, "Confidential invoice")
    c.save()
    return path


def truncated_pdf(path: Path) -> Path:
    full = text_pdf(path.with_name("full.pdf")).read_bytes()
    path.write_bytes(full[: len(full) // 2])  # cut in half: header ok, structure gone
    return path


def image(path: Path, fmt: str, frames: int = 1) -> Path:
    pictures = [Image.new("RGB", (60, 40), color) for color in ("white", "gray", "black")]
    pictures = (pictures * frames)[:frames]
    pictures[0].save(path, format=fmt, save_all=frames > 1, append_images=pictures[1:])
    return path


def invoice_xlsx(path: Path) -> Path:
    """Two sheets. 'Invoice' has title rows and a merged title cell above the header."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Invoice"
    rows = {
        1: ["ACME Supplies Ltd"],
        2: ["Invoice no:", "INV-2024-001"],
        # row 3 left empty on purpose
        4: ["Description", "Qty", "Unit price", "Amount"],  # header: row 4 -> index 3
        5: ["  Bolts M8  ", 3, "1.50", "4.50"],  # padded text gets stripped
        # row 6 empty: gets dropped
        7: ["Nuts M8", 10, "0.10", "1.00"],
    }
    for row_number, values in rows.items():  # explicit rows (ws.append skips blank rows)
        for column, value in enumerate(values, start=1):
            ws.cell(row=row_number, column=column, value=value)
    ws.merge_cells("A1:D1")
    notes = wb.create_sheet("Notes")
    notes.append(["Key", "Value"])
    notes.append(["Payment terms", "30 days"])
    wb.create_sheet("Empty")
    wb.save(path)
    return path


def duplicate_columns_xlsx(path: Path) -> Path:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Item", "Amount", "Amount", None, "Tax"])  # column D empty everywhere
    ws.append(["Bolt", "1.00", "2.00", None, "0.19"])
    wb.save(path)
    return path


def invoice_xls(path: Path) -> Path:
    """Legacy Excel 97-2003 workbook (BIFF8), written with xlwt."""
    book = xlwt.Workbook()
    sheet = book.add_sheet("Invoice")
    sheet.write_merge(0, 0, 0, 2, "ACME Supplies Ltd")  # merged title A1:C1
    for col, value in enumerate(["Description", "Qty", "Amount"]):
        sheet.write(2, col, value)  # header on row 3 -> index 2
    sheet.write(3, 0, "Bolts M8")
    sheet.write(3, 1, 3)
    sheet.write(3, 2, "4.50")
    other = book.add_sheet("Summary")
    other.write(0, 0, "Total")
    other.write(0, 1, "Currency")
    other.write(1, 0, "4.50")
    other.write(1, 1, "EUR")
    book.save(str(path))
    return path


def semicolon_csv_with_bom(path: Path) -> Path:
    text = (
        "ACME Supplies Ltd\n"  # title line with a single field
        "Description;Qty;Unit price;Amount\n"
        '"Bolts; galvanised";3;1,50;4,50\n'  # quoted field containing the delimiter
        'Nuts "M8";10;0,10;1,00\n'
        ";;;\n"  # empty row
    )
    path.write_bytes(text.encode("utf-8-sig"))  # utf-8-sig = UTF-8 with a BOM
    return path


# ---------------------------------------------------------------- OCR images

OCR_LINES = [
    "ACME Supplies Ltd",
    "Invoice No: INV-2041",
    "Invoice Date: 15 March 2024",
    "Office chairs      4 x 300.00      1,200.00",
    "Delivery                             120.00",
    "Total: 1,320.00",
]


def invoice_image(lines: list[str] | None = None, font_size: int = 36) -> Image.Image:
    """A clean, invoice-like page drawn with Pillow's bundled scalable font."""
    from PIL import ImageDraw, ImageFont

    page = Image.new("L", (1700, 900), 255)
    draw = ImageDraw.Draw(page)
    font = ImageFont.load_default(size=font_size)
    for i, line in enumerate(lines or OCR_LINES):
        draw.text((100, 100 + i * int(font_size * 1.9)), line, fill=0, font=font)
    return page


def noisy(page: Image.Image, sigma: float, seed: int = 0) -> Image.Image:
    """Add Gaussian noise (like a poor scan). Seeded, so tests are repeatable."""
    import numpy as np

    rng = np.random.default_rng(seed)
    pixels = np.asarray(page, dtype=np.float32) + rng.normal(0, sigma, (page.height, page.width))
    return Image.fromarray(np.clip(pixels, 0, 255).astype("uint8"))


def rotated(page: Image.Image, degrees: float) -> Image.Image:
    return page.rotate(degrees, expand=True, fillcolor=255, resample=Image.Resampling.BICUBIC)


def scanned_pdf_mixed(path: Path) -> Path:
    """Page 1: real text layer. Page 2: only a picture of an invoice (a 'scan')."""
    c = canvas.Canvas(str(path), pagesize=A4)
    for i, line in enumerate(INVOICE_LINES):
        c.drawString(50, 800 - 20 * i, line)
    c.showPage()
    buffer = io.BytesIO()
    invoice_image().save(buffer, format="PNG")
    width, height = A4
    c.drawImage(
        ImageReader(buffer), 20, height - 380, width=width - 40, height=(width - 40) * 9 / 17
    )
    c.showPage()
    c.save()
    return path
