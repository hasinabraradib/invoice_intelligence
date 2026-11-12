# Invoice Intelligence

## Overview

_To be written._

## Architecture

_Full write-up to come._ Database tables and the reason for each are documented in
[docs/schema.md](docs/schema.md).

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

### Background worker

Processing (parse, OCR, AI extraction, validation) runs in the **arq worker**, not in the
API request. `make up` starts it with everything else; on its own:

```bash
make worker        # (re)build and start only the worker
make worker-logs   # follow its logs
```

Uploads return `202 Accepted` at once with a `job_id`; poll the status endpoint until the
document is `processed`, `needs_review` or `failed`. Temporary problems (LLM rate limits or
outages, timeouts, database hiccups) are retried with exponential backoff, up to
`JOB_MAX_ATTEMPTS`. Permanent ones (corrupt file, password protected...) fail at once.
Jobs left "running" by a crashed worker are requeued when the worker restarts.

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

### AI extraction (try it on a sample)

Set `LLM_PROVIDER=gemini` or `groq` and `LLM_API_KEY` in `.env`, then:

```bash
make extract-sample FILE=samples/text-invoice.pdf   # prints fields, status, evidence, confidence
```

This sends the document's text to that provider, so only use invoices you may share.
Tests never call a real provider.

### Database

`make up` applies the Alembic migrations automatically (`alembic upgrade head`). Uploading
the same file twice stores it once: the response returns the existing `document_id` with
`"duplicate": true`. Duplicate invoices are detected by supplier plus a normalised invoice
number (`INV-001`, `inv 001` and `INV/001` match).

### Run tests locally (optional, without Docker)

```bash
cd backend
uv venv --python 3.12 .venv && uv pip install -e '.[dev]'
.venv/bin/pytest            # fast tests (OCR and database tests skip without tesseract/Postgres)
.venv/bin/pytest -m slow    # slow tests: 1000-page PDF memory, very noisy OCR
```

## API

Interactive docs: http://localhost:8000/docs. Every error has the same shape:
`{"error": {"code": "not_found", "message": "document not found"}}`. Lists use
`limit`/`offset` and return `{"items": [...], "total": N, "limit": ..., "offset": ...}`.

| Method | Path | What it does |
|---|---|---|
| `POST` | `/documents` | Upload one or more files → 202 with `document_id` + `job_id` per file (a repeat upload returns the existing id with `duplicate: true`) |
| `GET` | `/documents` | List, newest first. Filters: `status`, `needs_review`, `supplier`, `date_from`, `date_to` |
| `GET` | `/documents/{id}` | Metadata, invoice summary and the latest job |
| `GET` | `/documents/{id}/status` | Light polling endpoint: status, stage, `pages_done`/`pages_total`, attempts, error |
| `GET` | `/documents/{id}/result` | Every field with value, status, evidence quote, page, confidence and reasons; line items; validation issues; duplicate links |
| `POST` | `/documents/{id}/reprocess` | Queue again (409 if a job is already queued or running). Human corrections are kept |
| `PATCH` | `/invoices/{id}` | Correct header fields or line items. Audited in `corrections`, then re-validated |
| `GET` | `/search?q=` | Keyword search over page text, ranked, with a `<mark>`-highlighted snippet and page number. Same filters as the list |
| `POST` | `/jobs/retry-failed` | Queue every document whose last job failed; returns how many |
| `GET` | `/health` | App, database and Redis status |

```bash
# Upload, then poll until done
curl -s -X POST http://localhost:8000/documents -F "files=@samples/invoice.pdf"
curl -s http://localhost:8000/documents/<document_id>/status

# Result with evidence, and documents that need a human
curl -s http://localhost:8000/documents/<document_id>/result
curl -s "http://localhost:8000/documents?needs_review=true&limit=10"

# Correct a value (money as a string, dates as YYYY-MM-DD)
curl -s -X PATCH http://localhost:8000/invoices/<invoice_id> \
  -H "Content-Type: application/json" \
  -d '{"corrected_by": "you@example.com", "total": "1320.00", "invoice_date": "2026-03-15"}'

# Search, re-process, retry failures
curl -s "http://localhost:8000/search?q=office%20chairs&supplier=acme"
curl -s -X POST http://localhost:8000/documents/<document_id>/reprocess
curl -s -X POST http://localhost:8000/jobs/retry-failed
```

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
