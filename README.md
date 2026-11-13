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

### Web UI

`make up` also starts the frontend: **http://localhost:3000**.

| Page | What it is for |
|---|---|
| `/` Documents | Drag-and-drop upload with per-file progress, then a live table (it polls only while something is processing), with filters, reprocess and "Retry all failed" |
| `/documents/{id}` Review | Page preview on the left; every field on the right with status, confidence, evidence (click it to jump to the page) and inline editing; line items, issues, duplicates, history, approve and reprocess |
| `/review` Queue | Documents needing review, oldest first; "Next in queue" walks through them |
| `/search` | Hybrid, keyword or semantic search with the same filters; results open the review page at the matching page |

```bash
make frontend        # rebuild and restart only the UI container
make frontend-dev    # hot-reload dev server on :3000 (API must be running)
make frontend-check  # lint + typecheck + unit tests
make e2e             # browser smoke test against the running stack (skips if it is down,
                     # or if no LLM is configured)
cd frontend && API_URL=http://localhost:8000 npm run gen:api   # regenerate API types
```

`NEXT_PUBLIC_API_URL` (default `http://localhost:8000`) is the API address *as the browser
sees it*. It's baked in when the image is built. Screenshots: [docs/screenshots/](docs/screenshots/).

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

### Batch documents (many invoices in one PDF)

A scanned batch of about 1000 pages is split into one **segment per invoice** before
extraction:

1. **Rules first.** Each page's top lines are checked for an "INVOICE" heading, an invoice
   number (a different number means a new invoice), and "Page k of N" markers (page 1 starts
   an invoice; later pages continue it, so a 2-page invoice stays one).
2. **LLM only for unclear pages.** Pages with no clue, or with clues that disagree, are sent
   to the LLM in batches of 20, headers only (~400 characters each): "does this page start a
   new invoice?". Without an answer, a page joins the previous invoice. Merging two invoices
   gets caught in review; splitting one would scatter its fields.
3. **Per-invoice extraction**, concurrently (`SEGMENT_CONCURRENCY`, default 4, so you stay
   within your provider's rate limit). Each invoice is validated, duplicate-checked (including
   against other invoices in the same batch) and saved as soon as it finishes.
4. **Resumable.** If some invoices fail with a temporary error, the job's retry reuses the
   saved page texts (no re-OCR) and the stored boundaries, and redoes only the unfinished
   invoices.

Re-processing is idempotent. Invoices whose page range is unchanged keep their row and their
corrections. If the boundaries change, corrections move to the overlapping new invoice, which
is flagged `SEGMENT_CHANGED` for review. Spreadsheets are one invoice per sheet.

`GET /documents/{id}/result` returns `{"invoices": [...]}` in page order; the review page has
an invoice switcher that jumps the page viewer to each invoice.

**Measured** with `make batch-benchmark` on a synthetic 1000-page batch (502 invoices of 1-4
pages, a fixed seed, and a fake LLM, so this is the pipeline's own cost without provider time):

| Run | Invoices split exactly right | Total | Per page |
|---|---|---|---|
| 1000 text pages | 502 / 502 | 3.5 s | 0.004 s |
| 1000 text pages + local embeddings | 502 / 502 | 25.2 s | 0.025 s |
| 50 scanned pages (OCR) | 27 / 27 | 45 s | 0.90 s (≈ 15 min per 1000) |

With a real LLM, add the provider's time: about one extraction call per invoice, run 4 at a
time, plus about one classifier call per 20 unclear pages. On these synthetic pages the rules
are exact; real scans are messier, so check the review queue.

### Search modes

`GET /search?q=...&mode=hybrid` (default). Every mode returns **one hit per document** (its
best page or chunk) with the page number, a snippet, a score and `matched_by`.

| Mode | How | Good at | Weak at |
|---|---|---|---|
| `keyword` | Postgres full-text search on page text (`tsvector`, ranked by `ts_rank`) | exact things: invoice numbers, supplier names, codes | synonyms ("power" will not find "electricity") |
| `semantic` | nearest text chunks by cosine distance of local sentence embeddings (all-MiniLM-L6-v2 + pgvector HNSW) | meaning: "power charges" finds an electricity bill | exact codes and numbers |
| `hybrid` | both lists merged with **reciprocal rank fusion** (k=60) | both at once | nothing worth noting; the default |

**Why hybrid by default:** invoice searches mix both kinds ("ACME electricity March",
"INV-2041"). The two scores are on different scales, so RRF combines *ranks* instead of raw
scores. A document near the top of both lists beats one that is first in only one.
`matched_by` says which side found it (`keyword`, `semantic` or `both`). If embeddings are
off or failing, hybrid falls back to keyword-only and `mode=semantic` returns 503.

Keyword snippets highlight matches with `<mark>`; semantic snippets are plain text. Both are
HTML-escaped. The embedding model runs locally (no API key). It downloads once (~90 MB) into
the `models` Docker volume. Documents processed before semantic search existed can be
indexed with:

```bash
make embed-backfill   # safe to re-run: skips documents that already have chunks
```

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
| `GET` | `/documents/{id}/file` | The original file, inline, with the detected content type |
| `GET` | `/documents/{id}/pages` | Pages (or sheets) with their text |
| `GET` | `/documents/{id}/pages/{n}/image` | PNG preview of a page or frame (cached; 404 for spreadsheets) |
| `GET` | `/invoices/{id}/corrections` | Audit history of human changes |
| `POST` | `/invoices/{id}/approve` | Approve after review; 409 while errors remain unless `override_reason` is given |
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
