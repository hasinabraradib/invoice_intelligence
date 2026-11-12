# Sample invoices (not committed)

Put **3 real invoices** here to try the system by hand. Everything in this folder except
this README is ignored by git, because real invoices contain personal and financial data.

| File | What it should be | What to expect from `POST /documents` |
|------|-------------------|----------------------------------------|
| e.g. `text-invoice.pdf` | A digital PDF (you can select its text in a viewer) | `detected_kind: "pdf"`, `pages_needing_ocr: 0` |
| e.g. `scanned-invoice.jpg` / `.png` / `.tiff` / scanned `.pdf` | A photo or scan | every page counted in `pages_needing_ocr` |
| e.g. `invoice.xlsx` / `.xls` / `.csv` | A spreadsheet export | `sheet_count >= 1` |

Upload them all in one request:

```bash
curl -s -X POST http://localhost:8000/documents \
  -F "files=@samples/text-invoice.pdf" \
  -F "files=@samples/scanned-invoice.jpg" \
  -F "files=@samples/invoice.xlsx" | python3 -m json.tool
```

Tip: remove or blur personal data first, or use invoices you are allowed to share.
