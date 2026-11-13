"""Generate the synthetic labelled evaluation dataset (see eval/README.md).

    python eval/dataset/generate.py            # inside the backend image: `make eval-dataset`

Writes, with a fixed seed so every run is byte-identical:
    eval/dataset/files/       the documents (git-ignored; regenerate with this script)
    eval/dataset/labels/      ground truth, one JSON per invoice (committed)
    eval/dataset/manifest.json files, splits, segments, duplicates, cross-checks, sha256s

All suppliers, customers and numbers are invented. Every document says it is synthetic.
Ground truth is what is PRINTED: a deliberately wrong total is labelled with the wrong
value, plus the validation flag the system should raise. Expected flags are written from
each document's planted defects, not by running the app's rules (that would be circular).
"""

import csv
import hashlib
import io
import json
import random
import re
import time
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pypdfium2 as pdfium
from openpyxl import Workbook
from PIL import Image, ImageFilter
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

SEED = 20261114
ROOT = Path(__file__).resolve().parent
FILES, LABELS = ROOT / "files", ROOT / "labels"
CENT = Decimal("0.01")
FOOTER = "Synthetic test document generated for evaluation - not a real invoice."

SUPPLIERS = [
    ("Meghna Traders Ltd", "BDT"),
    ("Padma Electric Co.", "BDT"),
    ("Karnaphuli Foods Ltd", "BDT"),
    ("Jamuna Paper Mills", "BDT"),
    ("Northwind Office Supply Ltd", "USD"),
    ("Bluefin Logistics Inc.", "USD"),
    ("Cobalt Software LLC", "USD"),
    ("Rheinland Maschinen GmbH", "EUR"),
    ("Lisboa Textiles Lda", "EUR"),
    ("Alpine Instruments AG", "EUR"),
]
CUSTOMERS = [
    "Example Trading Co.",
    "Sample Retail Ltd",
    "Demo Hospital Trust",
    "Test Garments Ltd",
    "Placeholder Bank PLC",
]
CATALOGUE = [
    ("A4 paper (box of 5 reams)", (8, 30)),
    ("Toner cartridge", (40, 120)),
    ("Steel bolts M8", (0.2, 2)),
    ("LED panel 60x60", (15, 60)),
    ("Installation service", (50, 400)),
    ("Rice 25kg bag", (20, 45)),
    ("Office chair", (60, 300)),
    ("Network switch 24-port", (90, 400)),
    ("Freight charges", (30, 500)),
    ("Software licence (annual)", (100, 2000)),
    ("Cotton fabric (metre)", (2, 9)),
    ("Safety gloves (pair)", (1, 6)),
    ("Maintenance visit", (40, 250)),
    ("Calibration certificate", (60, 180)),
]
SYMBOL = {"BDT": "Tk", "USD": "$", "EUR": "€"}


@dataclass
class Line:
    description: str
    quantity: Decimal
    unit_price: Decimal
    line_total: Decimal


@dataclass
class Invoice:
    id: str
    invoice_number: str
    invoice_date: date
    due_date: date | None
    supplier_name: str
    customer_name: str | None
    currency: str | None  # None: no currency printed anywhere
    lines: list[Line]
    tax_rate: Decimal | None  # None: no tax line printed
    show_subtotal: bool = True
    total_error: Decimal = Decimal(0)  # planted: printed total is off by this much
    subtotal_error: Decimal = Decimal(0)  # planted: printed subtotal is off
    date_format: str = "iso"
    number_style: str = "en"  # "en" 1,234.50 | "eu" 1.234,50 | "plain" 1234.50
    currency_style: str = "code"  # code | symbol
    notes: list[str] = field(default_factory=list)

    # ---- printed values (what the ground truth is)
    @property
    def lines_sum(self) -> Decimal:
        return sum((x.line_total for x in self.lines), Decimal(0))

    @property
    def subtotal(self) -> Decimal | None:
        return (self.lines_sum + self.subtotal_error) if self.show_subtotal else None

    @property
    def tax(self) -> Decimal | None:
        if self.tax_rate is None:
            return None
        return (self.lines_sum * self.tax_rate).quantize(CENT, ROUND_HALF_UP)

    @property
    def total(self) -> Decimal:
        return self.lines_sum + (self.tax or Decimal(0)) + self.total_error


# ---------------------------------------------------------------- formatting


def money(value: Decimal, style: str) -> str:
    text = f"{value:,.2f}"
    if style == "eu":
        return text.replace(",", "_").replace(".", ",").replace("_", ".")
    if style == "plain":
        return f"{value:.2f}"
    return text


def qty_text(q: Decimal) -> str:
    return f"{q:f}".rstrip("0").rstrip(".") if "." in f"{q:f}" else f"{q:f}"


MONTHS = "January February March April May June July August September October November December"


def fmt_date(d: date, style: str) -> str:
    month = MONTHS.split()[d.month - 1]
    return {
        "iso": d.isoformat(),
        "dmy_text": f"{d.day:02d} {month[:3]} {d.year}",
        "mdy_long": f"{month} {d.day}, {d.year}",
        "dmy_dots": f"{d.day:02d}.{d.month:02d}.{d.year}",
        "dmy_slash": f"{d.day:02d}/{d.month:02d}/{d.year}",  # day > 12: unambiguous
        "ambiguous": f"{d.day:02d}/{d.month:02d}/{d.year}",  # day <= 12: could be either
    }[style]


NUMERIC_FORMATS = ("dmy_dots", "dmy_slash", "ambiguous")


def is_ambiguous(d: date | None, style: str) -> bool:
    """Numeric day/month dates are ambiguous when both parts could be the month."""
    return d is not None and style in NUMERIC_FORMATS and d.day <= 12 and d.day != d.month


def ambiguous_fields(inv: Invoice) -> list[str]:
    return [
        name
        for name, d in (("invoice_date", inv.invoice_date), ("due_date", inv.due_date))
        if is_ambiguous(d, inv.date_format)
    ]


def amount_label(inv: Invoice, value: Decimal) -> str:
    text = money(value, inv.number_style)
    if inv.currency is None:
        return text
    return f"{SYMBOL[inv.currency]} {text}" if inv.currency_style == "symbol" else text


# ---------------------------------------------------------------- dataset design


def build_invoices(rng: random.Random) -> list[Invoice]:
    invoices: list[Invoice] = []
    day0 = date(2025, 9, 1)
    formats = ["iso", "dmy_text", "mdy_long", "dmy_dots", "dmy_slash"]
    prefixes = ["INV-{y}-{n:04d}", "{s}/{n:04d}", "{s}-{n}", "{y}/BD/{n:04d}", "No. {n}"]
    for i in range(40):
        supplier, currency = SUPPLIERS[i % len(SUPPLIERS)]
        initials = "".join(w[0] for w in supplier.split()[:2]).upper()
        inv_date = day0 + timedelta(days=rng.randrange(0, 400))
        n_lines = rng.choice([1, 2, 2, 3, 3, 4, 5, 6])
        lines = []
        for item, (lo, hi) in rng.sample(CATALOGUE, n_lines):
            qty = Decimal(rng.choice([1, 1, 2, 3, 4, 5, 10, 12, 20, 50, 200]))
            if "metre" in item and rng.random() < 0.5:
                qty = Decimal("12.5")
            unit = Decimal(str(round(rng.uniform(lo, hi), 2))).quantize(CENT)
            lines.append(Line(item, qty, unit, (qty * unit).quantize(CENT)))
        number = prefixes[i % len(prefixes)].format(y=inv_date.year, n=100 + i * 7, s=initials)
        inv = Invoice(
            id=f"inv-{i + 1:02d}",
            invoice_number=number,
            invoice_date=inv_date,
            due_date=inv_date + timedelta(days=rng.choice([14, 30, 45])),
            supplier_name=supplier,
            customer_name=CUSTOMERS[i % len(CUSTOMERS)],
            currency=currency,
            lines=lines,
            tax_rate=Decimal(rng.choice(["0.05", "0.075", "0.15", "0.19"])),
            date_format=formats[i % len(formats)],
            currency_style="symbol" if i % 3 == 0 else "code",
        )
        invoices.append(inv)

    # Planted variations, at fixed positions so the design is easy to read.
    def plant(i: int, note: str, **changes: Any) -> None:
        for key, value in changes.items():
            setattr(invoices[i], key, value)
        invoices[i].notes.append(note)

    for i in (2, 9, 17, 25, 33, 38):
        plant(i, "no due date", due_date=None)
    for i in (4, 13, 21, 30):
        plant(i, "no customer printed", customer_name=None)
    for i in (6, 15, 27):
        plant(i, "no tax line", tax_rate=None)
    for i in (11, 29):
        plant(i, "no subtotal line", show_subtotal=False)
    plant(19, "no currency printed anywhere", currency=None)
    plant(35, "no currency printed anywhere", currency=None)
    plant(7, "wrong total (subtotal + tax != total)", total_error=Decimal("100.00"))
    plant(23, "wrong total (subtotal + tax != total)", total_error=Decimal("-12.50"))
    plant(14, "wrong subtotal (line items do not add up)", subtotal_error=Decimal("50.00"))
    plant(29, "wrong total with no subtotal (lines do not add up)", total_error=Decimal("7.00"))
    for i in (3, 12, 26, 37):  # day <= 12 and day != month: genuinely ambiguous
        d = invoices[i].invoice_date
        invoices[i].invoice_date = date(d.year, d.month if d.month != 4 else 5, 4)
        if invoices[i].due_date:
            invoices[i].due_date = invoices[i].invoice_date + timedelta(days=30)
        plant(i, "ambiguous invoice date (dd/mm vs mm/dd)", date_format="ambiguous")
    plant(31, "due date before invoice date")
    invoices[31].due_date = invoices[31].invoice_date - timedelta(days=5)
    for i in (8, 28):
        plant(i, "European number style 1.234,50", number_style="eu", currency_style="code")
        invoices[i].supplier_name, invoices[i].currency = SUPPLIERS[7 + i % 3]
    plant(18, "plain numbers without thousands separators", number_style="plain")
    for inv in invoices:  # numeric formats meant to be readable get a day > 12
        d = inv.invoice_date
        if inv.date_format in ("dmy_dots", "dmy_slash") and d.day <= 12:
            inv.invoice_date = d.replace(day=13 + d.day % 15)
            if inv.due_date:
                inv.due_date += inv.invoice_date - d
    return invoices


def expected_flags(inv: Invoice) -> list[dict[str, str]]:
    """Flags a perfect reading of this document should raise (from its planted design)."""
    flags = []
    for name in ambiguous_fields(inv):
        flags.append({"code": "AMBIGUOUS_DATE", "field": name})
        if name == "invoice_date":  # a required date stays empty until a human confirms it
            flags.append({"code": "MISSING_REQUIRED", "field": "invoice_date"})
    for name, value in (
        ("customer_name", inv.customer_name),
        ("currency", inv.currency),
        ("subtotal", inv.subtotal),
    ):
        if value is None:
            flags.append({"code": "MISSING_RECOMMENDED", "field": name})
    if inv.subtotal is not None and inv.tax is not None and inv.subtotal + inv.tax != inv.total:
        flags.append({"code": "TOTAL_MISMATCH", "field": "total"})
    # Lines must add up to the subtotal, or (no subtotal printed) lines + tax to the total.
    # A correct invoice without a subtotal line is NOT flagged here, even if a rule would.
    if inv.subtotal is not None:
        if inv.lines_sum != inv.subtotal:
            flags.append({"code": "LINE_ITEMS_SUM_MISMATCH", "field": "subtotal"})
    elif inv.lines_sum + (inv.tax or Decimal(0)) != inv.total:
        flags.append({"code": "LINE_ITEMS_SUM_MISMATCH", "field": "total"})
    # Only when both dates can be read: an ambiguous one is not compared.
    if inv.due_date and inv.due_date < inv.invoice_date and not ambiguous_fields(inv):
        flags.append({"code": "DUE_BEFORE_INVOICE_DATE", "field": "due_date"})
    return flags


def truth(inv: Invoice) -> dict[str, Any]:
    s = lambda v: None if v is None else str(v)  # noqa: E731
    return {
        "invoice_number": inv.invoice_number,
        "invoice_date": inv.invoice_date.isoformat(),
        "due_date": s(inv.due_date and inv.due_date.isoformat()),
        "supplier_name": inv.supplier_name,
        "customer_name": inv.customer_name,
        "currency": inv.currency,
        "subtotal": s(inv.subtotal),
        "tax": s(inv.tax),
        "total": str(inv.total),
    }


# ---------------------------------------------------------------- PDF rendering

FONTS = [("Helvetica", "Helvetica-Bold"), ("Times-Roman", "Times-Bold"), ("DejaVu", "DejaVu-Bold")]


def register_fonts() -> None:
    base = Path("/usr/share/fonts/truetype/dejavu")
    pdfmetrics.registerFont(TTFont("DejaVu", str(base / "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont("DejaVu-Bold", str(base / "DejaVuSans-Bold.ttf")))


def header_lines(inv: Invoice, date_label: str = "Invoice Date") -> list[tuple[str, str]]:
    rows = [
        ("Invoice No", inv.invoice_number),
        (date_label, fmt_date(inv.invoice_date, inv.date_format)),
    ]
    if inv.due_date:
        rows.append(("Due Date", fmt_date(inv.due_date, inv.date_format)))
    if inv.customer_name:
        rows.append(("Bill To", inv.customer_name))
    if inv.currency and inv.currency_style == "code":
        rows.append(("Currency", inv.currency))
    return rows


def totals_lines(inv: Invoice) -> list[tuple[str, str]]:
    rows = []
    if inv.subtotal is not None:
        rows.append(("Subtotal", amount_label(inv, inv.subtotal)))
    if inv.tax is not None:
        pct = f"{(inv.tax_rate * 100).normalize():f}%"
        rows.append((f"VAT {pct}", amount_label(inv, inv.tax)))
    label = f"Total ({inv.currency})" if inv.currency and inv.currency_style == "code" else "Total"
    rows.append((label, amount_label(inv, inv.total)))
    return rows


def draw_invoice(
    c: canvas.Canvas,
    inv: Invoice,
    layout: int,
    pages: list[list[Line]],
    *,
    title: str = "INVOICE",
    number_label: str = "Invoice No",
    date_label: str = "Invoice Date",
    page_marks: bool = True,
) -> None:
    """Draw one invoice over len(pages) pages; line items split as given."""
    regular, bold = FONTS[layout % len(FONTS)]
    width, height = A4
    for p, page_lines in enumerate(pages, start=1):
        y = height - 60
        if p == 1:
            c.setFont(bold, 18)
            if layout == 1:
                c.drawRightString(width - 50, y, title)
                c.setFont(bold, 13)
                c.drawString(50, y, inv.supplier_name)
            else:
                c.drawString(50, y, f"{title}   {inv.supplier_name}")
            y -= 34
            c.setFont(regular, 11)
            for label, value in header_lines(inv, date_label):
                label = number_label if label == "Invoice No" else label
                if layout == 2:
                    c.drawString(330, y, f"{label}:")
                    c.drawString(430, y, value)
                else:
                    c.drawString(50, y, f"{label}: {value}")
                y -= 18
            y -= 14
        else:
            c.setFont(regular, 11)
            c.drawString(
                50, y, f"{inv.supplier_name} - {number_label} {inv.invoice_number} (continued)"
            )
            y -= 30
        c.setFont(bold, 11)
        cols = (50, 330, 410, width - 50)
        c.drawString(cols[0], y, "Description")
        c.drawRightString(cols[1] + 30, y, "Qty")
        c.drawRightString(cols[2] + 60, y, "Unit price")
        c.drawRightString(cols[3], y, "Amount")
        if layout == 0:
            c.line(50, y - 5, width - 50, y - 5)
        y -= 22
        c.setFont(regular, 11)
        for line in page_lines:
            c.drawString(cols[0], y, line.description)
            c.drawRightString(cols[1] + 30, y, qty_text(line.quantity))
            c.drawRightString(cols[2] + 60, y, money(line.unit_price, inv.number_style))
            c.drawRightString(cols[3], y, money(line.line_total, inv.number_style))
            y -= 20
        if p == len(pages):
            y -= 14
            for label, value in totals_lines(inv):
                c.setFont(bold if label.startswith("Total") else regular, 11)
                c.drawString(330, y, label)
                c.drawRightString(width - 50, y, value)
                y -= 20
        c.setFont(regular, 8)
        c.drawString(50, 40, FOOTER)
        if page_marks and len(pages) > 1:
            c.drawRightString(width - 50, 40, f"Page {p} of {len(pages)}")
        c.showPage()


def pdf_bytes(draw) -> bytes:
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4, invariant=1)  # invariant: no timestamps, stable bytes
    draw(c)
    c.save()
    return buffer.getvalue()


def scanned_images(pdf: bytes, rng: random.Random) -> list[Image.Image]:
    """Rasterise at 200 dpi, then degrade like a phone photo or office scan."""
    images = []
    doc = pdfium.PdfDocument(pdf)
    for page in doc:
        img = page.render(scale=200 / 72).to_pil().convert("L")
        angle = rng.uniform(2, 5) * rng.choice([-1, 1])
        img = img.rotate(angle, expand=True, fillcolor=255, resample=Image.BICUBIC)
        img = img.filter(ImageFilter.GaussianBlur(rng.uniform(0.5, 1.1)))
        noise = np.random.default_rng(rng.randrange(1 << 30)).normal(
            0, rng.uniform(6, 14), (img.height, img.width)
        )
        arr = np.clip(np.asarray(img, dtype=np.float32) + noise, 0, 255).astype("uint8")
        images.append(Image.fromarray(arr))
    doc.close()
    return images


def jpeg(img: Image.Image, quality: int) -> bytes:
    out = io.BytesIO()
    img.save(out, "JPEG", quality=quality)
    return out.getvalue()


# ---------------------------------------------------------------- spreadsheets


FIXED_TIME = datetime(2026, 1, 1)
PDF_TIME = time.gmtime(FIXED_TIME.replace(tzinfo=UTC).timestamp())  # Pillow wants struct_time


def stable_xlsx(wb: Workbook) -> bytes:
    """openpyxl stamps the save time into docProps/core.xml and the zip entries; pin both."""
    wb.properties.created = wb.properties.modified = FIXED_TIME
    raw = io.BytesIO()
    wb.save(raw)
    out = io.BytesIO()
    with zipfile.ZipFile(raw) as src, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            data = src.read(info.filename)
            if info.filename == "docProps/core.xml":
                data = re.sub(
                    rb"(<dcterms:modified[^>]*>)[^<]*", rb"\g<1>2026-01-01T00:00:00Z", data
                )
            dst.writestr(zipfile.ZipInfo(info.filename, date_time=(2026, 1, 1, 0, 0, 0)), data)
    return out.getvalue()


def xlsx_bytes(inv: Invoice) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Invoice"
    ws.append(["INVOICE", inv.supplier_name])
    ws.append(["Invoice No", inv.invoice_number])
    ws.append(["Invoice Date", inv.invoice_date])  # a real date cell
    if inv.due_date:
        ws.append(["Due Date", inv.due_date])
    if inv.customer_name:
        ws.append(["Bill To", inv.customer_name])
    if inv.currency:
        ws.append(["Currency", inv.currency])
    ws.append([])
    ws.append(["Description", "Qty", "Unit Price", "Amount"])
    for line in inv.lines:
        ws.append(
            [line.description, float(line.quantity), float(line.unit_price), float(line.line_total)]
        )
    ws.append([])
    for label, value in (("Subtotal", inv.subtotal), ("VAT", inv.tax), ("Total", inv.total)):
        if value is not None:
            ws.append([label, None, None, float(value)])
    ws.append([FOOTER])
    return stable_xlsx(wb)


def csv_bytes(inv: Invoice) -> bytes:
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(["Invoice No", inv.invoice_number])
    w.writerow(["Supplier", inv.supplier_name])
    w.writerow(["Invoice Date", fmt_date(inv.invoice_date, inv.date_format)])
    if inv.due_date:
        w.writerow(["Due Date", fmt_date(inv.due_date, inv.date_format)])
    if inv.customer_name:
        w.writerow(["Bill To", inv.customer_name])
    if inv.currency:
        w.writerow(["Currency", inv.currency])
    w.writerow(["Description", "Qty", "Unit Price", "Amount"])
    for line in inv.lines:
        w.writerow(
            [
                line.description,
                qty_text(line.quantity),
                money(line.unit_price, "plain"),
                money(line.line_total, "plain"),
            ]
        )
    for label, value in (("Subtotal", inv.subtotal), ("VAT", inv.tax), ("Total", inv.total)):
        if value is not None:
            w.writerow([label, "", "", money(value, "plain")])
    w.writerow([FOOTER, "", "", ""])
    return out.getvalue().encode("utf-8")


def ledger_bytes(rows: list[list[Any]]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Purchases ledger"
    ws.append(["Invoice No", "Invoice Date", "Supplier", "Currency", "VAT", "Total"])
    for row in rows:
        ws.append(row)
    return stable_xlsx(wb)


# ---------------------------------------------------------------- assembly


def main() -> None:
    rng = random.Random(SEED)  # noqa: S311 (reproducible test data, not crypto)
    register_fonts()
    invoices = build_invoices(rng)
    FILES.mkdir(parents=True, exist_ok=True)
    LABELS.mkdir(parents=True, exist_ok=True)
    for old in [*FILES.iterdir(), *LABELS.iterdir()]:
        old.unlink()

    # File type per invoice (fixed design); the duplicate's twin is added below.
    kinds = (
        ["pdf"] * 15
        + ["scan_jpg"] * 7
        + ["scan_pdf"] * 2
        + ["xlsx"] * 5
        + ["csv"] * 5
        + ["batch"] * 6
    )
    # Ambiguous dates only where the text is printed (Excel stores real dates).
    order = list(range(40))
    rng.shuffle(order)
    kind_of: dict[int, str] = {}
    pool = list(kinds)
    rng.shuffle(pool)
    for i in sorted(order, key=lambda i: invoices[i].date_format != "ambiguous"):
        choice = next(
            k for k in pool if not (invoices[i].date_format == "ambiguous" and k == "xlsx")
        )
        pool.remove(choice)
        kind_of[i] = choice
    # Fixed roles: the PO invoice and the duplicate original are digital PDFs.
    po_idx = next(i for i in range(40) if kind_of[i] == "pdf" and not invoices[i].notes)
    dup_idx = next(
        i for i in range(40) if kind_of[i] == "pdf" and not invoices[i].notes and i != po_idx
    )
    batch_idx = [i for i in range(40) if kind_of[i] == "batch"]

    # Split: stratified ~40% dev; batch, ledger and the duplicate pair are test only.
    split: dict[int, str] = {}
    for kind in sorted(set(kinds)):
        members = [i for i in range(40) if kind_of[i] == kind]
        rng.shuffle(members)
        n_dev = 0 if kind == "batch" else round(len(members) * 0.4)
        for n, i in enumerate(members):
            split[i] = "dev" if n < n_dev else "test"
    split[po_idx], split[dup_idx] = "dev", "test"

    files: list[dict[str, Any]] = []
    labels: dict[str, dict[str, Any]] = {}

    def add_file(
        name: str,
        data: bytes,
        file_type: str,
        file_split: str,
        ids: list[str],
        segments: list[list[int]] | None = None,
        role: str = "invoice",
    ) -> None:
        (FILES / name).write_bytes(data)
        files.append(
            {
                "file": name,
                "file_type": file_type,
                "split": file_split,
                "role": role,
                "invoices": ids,
                "expected_segments": segments,
                "sha256": hashlib.sha256(data).hexdigest(),
                "size_bytes": len(data),
            }
        )

    def label(
        inv: Invoice,
        name: str,
        file_type: str,
        file_split: str,
        segment: list[int] | None = None,
        extra_flags: list[dict] | None = None,
        duplicate_of: str | None = None,
        label_id: str | None = None,
    ) -> str:
        lid = label_id or inv.id
        labels[lid] = {
            "id": lid,
            "file": name,
            "file_type": file_type,
            "split": file_split,
            "segment": segment,
            "fields": truth(inv),
            "line_items": [{k: str(v) for k, v in asdict(x).items()} for x in inv.lines],
            "ambiguous_fields": ambiguous_fields(inv),
            "expected_flags": expected_flags(inv) + (extra_flags or []),
            "duplicate_of": duplicate_of,
            "notes": inv.notes,
        }
        return lid

    for i, inv in enumerate(invoices):
        kind = kind_of[i]
        if kind == "batch":
            continue
        layout = i % 3
        if kind in ("pdf", "scan_jpg", "scan_pdf"):
            pdf = pdf_bytes(
                lambda c, inv=inv, layout=layout: draw_invoice(c, inv, layout, [inv.lines])
            )
            if kind == "pdf":
                name, data, ftype = f"{inv.id}.pdf", pdf, "pdf_digital"
            elif kind == "scan_jpg":
                name, data, ftype = (
                    f"{inv.id}.jpg",
                    jpeg(scanned_images(pdf, rng)[0], rng.randrange(45, 76)),
                    "image_scan",
                )
            else:
                imgs = [
                    Image.open(io.BytesIO(jpeg(im, rng.randrange(45, 76))))
                    for im in scanned_images(pdf, rng)
                ]
                out = io.BytesIO()
                imgs[0].save(
                    out,
                    "PDF",
                    resolution=200,
                    save_all=True,
                    append_images=imgs[1:],
                    creationDate=PDF_TIME,
                    modDate=PDF_TIME,
                )
                name, data, ftype = f"{inv.id}-scanned.pdf", out.getvalue(), "pdf_scanned"
        elif kind == "xlsx":
            name, data, ftype = f"{inv.id}.xlsx", xlsx_bytes(inv), "xlsx"
        else:
            name, data, ftype = f"{inv.id}.csv", csv_bytes(inv), "csv"
        add_file(name, data, ftype, split[i], [label(inv, name, ftype, split[i])])

    # Duplicate pair: a scanned copy of a digital invoice, uploaded after the original.
    dup = invoices[dup_idx]
    dup_pdf = pdf_bytes(lambda c: draw_invoice(c, dup, dup_idx % 3, [dup.lines]))
    copy_name = f"{dup.id}-copy.jpg"
    copy_id = label(
        dup,
        copy_name,
        "image_scan",
        "test",
        label_id=f"{dup.id}-copy",
        extra_flags=[{"code": "DUPLICATE_INVOICE", "field": "invoice_number"}],
        duplicate_of=dup.id,
    )
    add_file(
        copy_name,
        jpeg(scanned_images(dup_pdf, rng)[0], 60),
        "image_scan",
        "test",
        [copy_id],
        role="duplicate_copy",
    )

    # Batch PDF: 6 invoices on 9 pages; two multi-page ones say "Page k of N", one doesn't.
    page_plan = [1, 2, 1, 3, 1, 1]
    batch = [invoices[i] for i in batch_idx]
    segments, start = [], 1
    for inv, n in zip(batch, page_plan, strict=True):
        while len(inv.lines) < n * 2:  # enough lines to spill over n pages
            extra = rng.choice(CATALOGUE)[0]
            inv.lines.append(
                Line(f"{extra} (extra)", Decimal(1), Decimal("10.00"), Decimal("10.00"))
            )
        segments.append([start, start + n - 1])
        start += n

    def draw_batch(c: canvas.Canvas) -> None:
        for k, (inv, n) in enumerate(zip(batch, page_plan, strict=True)):
            per = -(-len(inv.lines) // n)
            chunks = [inv.lines[j * per : (j + 1) * per] for j in range(n)]
            draw_invoice(c, inv, k % 3, chunks, page_marks=(k != 3))

    batch_name = "batch-6-invoices.pdf"
    batch_ids = [
        label(inv, batch_name, "pdf_batch", "test", segment=seg)
        for inv, seg in zip(batch, segments, strict=True)
    ]
    add_file(batch_name, pdf_bytes(draw_batch), "pdf_batch", "test", batch_ids, segments)

    # Ledger for the batch, with planted mismatches.
    b = batch
    ledger_rows, findings = [], []
    for k, inv in enumerate(b):
        total, inv_date = inv.total, inv.invoice_date
        if k == 1:
            total += Decimal("100.00")
            findings.append(
                {"kind": "amount_mismatch", "field": "total", "invoice_number": inv.invoice_number}
            )
        if k == 2:
            inv_date += timedelta(days=3)
            findings.append(
                {
                    "kind": "date_mismatch",
                    "field": "invoice_date",
                    "invoice_number": inv.invoice_number,
                }
            )
        if k == 4:
            findings.append(
                {"kind": "missing_in_ledger", "field": None, "invoice_number": inv.invoice_number}
            )
            continue
        ledger_rows.append(
            [
                inv.invoice_number,
                inv_date,
                inv.supplier_name,
                inv.currency,
                float(inv.tax) if inv.tax is not None else None,
                float(total),
            ]
        )
    ledger_rows.append(
        ["INV-2026-9999", date(2026, 6, 30), b[0].supplier_name, b[0].currency, 15.0, 115.0]
    )
    findings.append({"kind": "missing_in_pdf", "field": None, "invoice_number": "INV-2026-9999"})
    add_file(
        "ledger-batch.xlsx", ledger_bytes(ledger_rows), "xlsx_ledger", "test", [], role="ledger"
    )

    # Purchase order for the PO invoice: same supplier/date/total, line 2 differs in
    # quantity and unit price (same line total).
    inv = invoices[po_idx]
    freight = Line("Express courier service", Decimal(10), Decimal("45.00"), Decimal("450.00"))
    inv.lines = [inv.lines[0], freight, *inv.lines[2:]]
    po = Invoice(
        **{**{f: getattr(inv, f) for f in Invoice.__dataclass_fields__}, "id": f"{inv.id}-po"}
    )
    po.invoice_number = "PO-" + "".join(ch for ch in inv.invoice_number if ch.isdigit())[-4:]
    po.due_date = None
    po.lines = [
        inv.lines[0],
        Line("Express courier service", Decimal(9), Decimal("50.00"), Decimal("450.00")),
        *inv.lines[2:],
    ]
    # The invoice PDF was already written above; rewrite it with the (possibly longer) lines.
    labels[inv.id]["line_items"] = [{k: str(v) for k, v in asdict(x).items()} for x in inv.lines]
    labels[inv.id]["fields"] = truth(inv)
    labels[inv.id]["expected_flags"] = expected_flags(inv)
    inv_pdf = pdf_bytes(lambda c: draw_invoice(c, inv, po_idx % 3, [inv.lines]))
    files[:] = [f for f in files if f["file"] != f"{inv.id}.pdf"]
    add_file(f"{inv.id}.pdf", inv_pdf, "pdf_digital", "dev", [inv.id])
    po_pdf = pdf_bytes(
        lambda c: draw_invoice(
            c,
            po,
            po_idx % 3,
            [po.lines],
            title="PURCHASE ORDER",
            number_label="PO No",
            date_label="Date",
        )
    )
    add_file(
        f"{inv.id}-purchase-order.pdf",
        po_pdf,
        "pdf_purchase_order",
        "dev",
        [],
        role="purchase_order",
    )
    po_findings = [
        {
            "kind": "line_item_mismatch",
            "field": "line_items[2].quantity",
            "invoice_number": inv.invoice_number,
        },
        {
            "kind": "line_item_mismatch",
            "field": "line_items[2].unit_price",
            "invoice_number": inv.invoice_number,
        },
    ]

    # Dev-split cover for the no-subtotal line-sum rule, added after the first evaluation
    # found the bug on the test split. Its own RNG keeps every earlier file byte-identical.
    extra_rng = random.Random(SEED + 1)  # noqa: S311 (reproducible test data)
    for n, (tax_rate, note) in enumerate(
        ((Decimal("0.15"), "no subtotal line, tax printed"), (None, "no subtotal line, no tax")),
        start=41,
    ):
        supplier, currency = SUPPLIERS[n % len(SUPPLIERS)]
        lines = []
        for item, (lo, hi) in extra_rng.sample(CATALOGUE, 3):
            qty = Decimal(extra_rng.choice([1, 2, 5, 10]))
            unit = Decimal(str(round(extra_rng.uniform(lo, hi), 2))).quantize(CENT)
            lines.append(Line(item, qty, unit, (qty * unit).quantize(CENT)))
        inv_date = date(2026, 3, 1) + timedelta(days=extra_rng.randrange(0, 200))
        inv = Invoice(
            id=f"inv-{n}",
            invoice_number=f"INV-{inv_date.year}-{900 + n}",
            invoice_date=inv_date,
            due_date=inv_date + timedelta(days=30),
            supplier_name=supplier,
            customer_name=CUSTOMERS[n % len(CUSTOMERS)],
            currency=currency,
            lines=lines,
            tax_rate=tax_rate,
            show_subtotal=False,
            notes=[note],
        )
        name = f"{inv.id}.pdf"
        pdf = pdf_bytes(lambda c, inv=inv, n=n: draw_invoice(c, inv, n % 3, [inv.lines]))
        add_file(name, pdf, "pdf_digital", "dev", [label(inv, name, "pdf_digital", "dev")])

    for lid, data in labels.items():
        (LABELS / f"{lid}.json").write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    manifest = {
        "seed": SEED,
        "note": "Synthetic data only. Ground truth = printed values. Rebuild: make eval-dataset.",
        "invoice_count": len(labels),
        "files": sorted(files, key=lambda f: f["file"]),
        "duplicates": [{"original": dup.id, "copy": copy_id}],
        "cross_checks": [
            {
                "a": batch_name,
                "b": "ledger-batch.xlsx",
                "mode": "invoices_vs_ledger",
                "split": "test",
                "expected_findings": findings,
            },
            {
                "a": f"{inv.id}.pdf",
                "b": f"{inv.id}-purchase-order.pdf",
                "mode": "invoices_vs_invoices",
                "split": "dev",
                "expected_findings": po_findings,
            },
        ],
    }
    (ROOT / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    by_type: dict[str, int] = {}
    for lab in labels.values():
        by_type[lab["file_type"]] = by_type.get(lab["file_type"], 0) + 1
    splits = {s: sum(1 for x in labels.values() if x["split"] == s) for s in ("dev", "test")}
    print(
        f"{len(labels)} labelled invoices in {len(files)} files; by type {by_type}; split {splits}"
    )


if __name__ == "__main__":
    main()
