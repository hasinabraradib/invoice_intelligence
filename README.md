# Invoice Intelligence

## Overview

_To be written._

## Architecture

_To be written._

## Quick start

Requirements: Docker (with Docker Compose v2) and `make`.

```bash
# 1. Create your local config (only placeholders, edit if needed)
cp .env.example .env

# 2. Build and start everything: api, worker, postgres (pgvector), redis
make up

# 3. Check that the app, database and Redis are all reachable
curl -s http://localhost:8000/health
# -> {"status":"ok","app":{...},"database":{"status":"ok",...},"redis":{"status":"ok",...}}

# Interactive API docs: http://localhost:8000/docs

# 4. Run tests and lint (inside the backend container, Python 3.12)
make test
make lint

# 5. Stop everything (your database data is kept in a Docker volume)
make down
```

`/health` returns HTTP 200 when everything is reachable and HTTP 503 (`"status": "degraded"`)
when Postgres or Redis is down.

### Upload and parse documents

```bash
# One or more files per request (PDF, PNG, JPEG, TIFF, XLSX, XLS, CSV)
curl -s -X POST http://localhost:8000/documents \
  -F "files=@samples/text-invoice.pdf" \
  -F "files=@samples/invoice.xlsx" | python3 -m json.tool
```

Each file gets its own result (`status_code` 200, or 415 unsupported type, 413 too large,
422 corrupt/empty/password-protected), so one bad file never fails the others.
The file type is detected from the file's content, not its name.
See [samples/README.md](samples/README.md) for trying it with your own invoices.

### OCR (scans and photos)

Pages without a usable text layer (scanned PDFs, PNG/JPEG/TIFF) are OCR'd with Tesseract
inside the container. English (`eng`) and Bengali (`ben`) are installed; choose with
`OCR_LANGS` in `.env` (`eng`, `ben` or `eng+ben`; more languages = slower). Pages with a
mean word confidence below `OCR_MIN_CONFIDENCE` (default 60) get a "low OCR confidence"
warning.

```bash
# Inspect OCR text, confidence and timing for one of your sample files
make ocr-sample FILE=samples/scanned-invoice.jpg LANGS=eng+ben
```

### Run tests locally (optional, without Docker)

```bash
cd backend
uv venv --python 3.12 .venv && uv pip install -e '.[dev]'
.venv/bin/pytest            # fast tests (OCR tests skip if tesseract is missing)
.venv/bin/pytest -m slow    # slow tests: 1000-page PDF memory, very noisy OCR
```

## API

_To be written._

## Tests

_To be written._

## Evaluation

_To be written._

## Design decisions

_To be written._

## Limitations

_To be written._

## AI usage

See [AI_USAGE.md](AI_USAGE.md).
