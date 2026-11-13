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

### Assistant (the home page)

Open http://localhost:3000. The assistant greets you with live numbers ("9 invoices need
review, 2 jobs running") and suggestions that fit your data. You can do everything by
chatting: ask questions ("How much did we spend with ACME in March?"), drop invoices into
the chat (it follows processing and summarises the result), and ask it to correct a
field, approve an invoice, re-process a document or run a cross-check. The other pages are
still there for manual work.

How it works: the model gets **tools** that wrap our own services, never raw database
access. Read tools (search, list documents, get an invoice with evidence, review queue, job
status, stats, cross-check results, `sum_invoices` / `count_invoices`) run automatically,
at most 6 tool steps per question, and then it must answer. Answers stream token by token,
show which tools were used, and carry citation chips that open the review page at the cited
page.

**Safety design**

- **Answers come only from tool results,** and every claim is cited with file, invoice
  number and page. If nothing is found, it says so.
- **Numbers come from fixed, parameterised SQL** (`sum_invoices`, `count_invoices`), one
  total per currency, as Decimal. The model never writes SQL and never does arithmetic.
- **The assistant never changes anything itself.** Correct, approve, reprocess and
  cross-check are only *proposed*, as a card with a plain-words summary ("Change the total
  of INV-2041 from 1,320.00 to 1,230.00"). The change runs only when you press **Confirm**
  (`POST /assistant/actions/{id}/confirm`); proposals expire after 10 minutes. Confirmed
  corrections go through the normal audit trail as "assistant (confirmed by user)".
- **Two layers against prompt injection.** Tool results and document text reach the model
  as untrusted data between hash-tagged delimiters, and the prompt says to ignore
  instructions inside them. In code, a write tool is refused unless *your own message* asks
  for that kind of change, so an invoice saying "approve all invoices" cannot create an
  action even if the model falls for it.
- **Bad tool calls become tool errors** returned to the model; they never crash the chat.
- **Model text is rendered as a small Markdown subset** with React elements only: no raw
  HTML, and links only to `http(s)` or in-app pages.
- **Full trace:** every turn (your message, tool calls, tool results, answer) is stored,
  so any answer can be traced back to the data it used.

Without a configured provider (see [LLM providers](#llm-providers-bring-your-own-key)) the chat explains that the language model is not
configured; everything else keeps working. `make assistant-eval` scores the assistant on
the 15 questions in [docs/assistant-eval.md](docs/assistant-eval.md) with your real
provider (right number, right citation).

### Web UI

`make up` also starts the frontend: **http://localhost:3000**.

| Page | What it is for |
|---|---|
| `/` Assistant | Chat (see above) |
| `/dashboard` | Counts at a glance |
| `/documents` | Drag-and-drop upload with per-file progress, then a live table (it polls only while something is processing), with filters, reprocess and "Retry all failed" |
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
| 50 scanned pages (OCR) | 27 / 27 | 50-60 s | 1.0-1.2 s (≈ 17-20 min per 1000) |

**Parallel OCR.** OCR pages run in a pool of worker processes (`OCR_WORKERS`, default CPU
cores − 1). Each worker renders and OCRs its own page; at most 2 × workers pages are in
flight, and results keep page order. If a job times out, the pool is terminated, so the
work really stops. Measured on this laptop (alternating runs, same 24 scanned pages):

| OCR | Seconds per page |
|---|---|
| Sequential (before) | 1.14 |
| Pool, 2 workers | 1.04 (−8%) |
| Pool, 9 workers (default here) | 1.11 (−3%) |

The gain is small because OpenCV's denoising already spreads each page over every core,
and this machine has about 4 cores' worth of real compute (one page takes 3.75 s on one
thread and 0.94 s on all of them). Without splitting OpenCV's threads between workers, extra
processes gave no gain at all. On a server with more real cores, try `OCR_WORKERS=2..4`
with `make batch-benchmark ARGS=--scanned`. Machine drift between runs is ±10%, so
compare runs made back to back.

**Partly failed batches.** If some invoices fail for good while others succeed, the
document is `partially_failed` and keeps the good invoices. "Retry all failed" (or
`POST /documents/{id}/reprocess?only_failed=true`) continues the failed job: it reuses the
page texts and boundaries and extracts only the invoices that failed.

With a real LLM, add the provider's time: about one extraction call per invoice, run 4 at a
time, plus about one classifier call per 20 unclear pages. On these synthetic pages the rules
are exact; real scans are messier, so check the review queue.

### Cross-verification (PDF vs Excel, PDF vs PDF)

Open **Cross-check** in the UI (or `POST /cross-checks`), pick two documents, and the worker
compares them in the background:

- **Invoices vs a ledger.** A spreadsheet that lists many invoices (columns such as invoice
  no, date, supplier/vendor, amount/total, tax, currency) is recognised as a *ledger*. Its
  columns are mapped by header synonyms in plain code ("Inv #", "Vendor", "Amount (BDT)"),
  and every row is stored with its sheet and row number. Ledgers are not sent to the LLM.
  If a mapping is wrong, fix it with `PUT /documents/{id}/column-mapping`. The rows are
  re-mapped at once and the fix is kept on re-processing.
- **Invoices vs invoices**, e.g. an invoice against its purchase order, or two copies.
  Header fields are compared, and line items are paired by description similarity and
  amounts.

Invoices are matched by normalised invoice number, then by supplier + date + total.
Findings: matched, amount / date / currency / field mismatch, missing in the ledger or PDF
(or in either PDF), duplicate ledger rows, and line items that differ or exist on one side
only. Money is compared as Decimal within `MONEY_TOLERANCE` (a 0.004 difference matches; 0.02
does not). Each finding links to its evidence on both sides: the invoice page, or the
highlighted ledger row.

Nothing is ever changed. A mismatch adds a `CROSS_CHECK_MISMATCH` issue (severity error) to
the invoices involved, which sets `needs_review`. These issues survive re-validation and are
replaced only when that cross-check runs again. Running a pair again replaces its findings
(idempotent).

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

### LLM providers (bring your own key)

The LLM only extracts and quotes text; code does all parsing, arithmetic and validation.
Three providers are supported, all called over plain HTTPS with `httpx` (no vendor SDKs)
with the same rules: temperature 0 (see the OpenAI note below), JSON-schema output for extraction, native tool calling
for the assistant, `LLM_TIMEOUT_SECONDS` per request, and up to `LLM_MAX_ATTEMPTS` tries on
rate limits (429), server errors and network errors, with exponential backoff and jitter
(a provider's `Retry-After` is honoured). Bad keys and bad requests are not retried.

| `LLM_PROVIDER` | Key variable | Default `LLM_MODEL` | Get a key |
|----------------|--------------|---------------------|-----------|
| `gemini` | `LLM_API_KEY` | `gemini-2.5-flash` | [aistudio.google.com](https://aistudio.google.com/app/apikey) |
| `groq` | `LLM_API_KEY` | `openai/gpt-oss-120b` | [console.groq.com](https://console.groq.com/keys) |
| `openai` | `OPENAI_API_KEY` | `gpt-6-luna` | [platform.openai.com](https://platform.openai.com/api-keys) |
| `none` (default) | none | none | The stack runs; extraction and chat report that no LLM is set |

**To use your own key (reviewers):**

1. `make env` (creates `.env` from `.env.example` if it doesn't exist; `.env` is git-ignored).
2. Edit `.env`, for example:
   ```bash
   LLM_PROVIDER=openai
   OPENAI_API_KEY=sk-...your key...
   LLM_MODEL=            # empty = the default above (gpt-6-luna for OpenAI)
   OPENAI_REASONING_EFFORT=none   # none | low
   ```
3. `make up` (restarts the api and worker with the new settings).
4. Try it: `make extract-sample FILE=samples/<your file>` or ask a question on the home page.

Notes:

- Keep keys in `.env` only. Never put them in `.env.example`, code, commits or issue text.
  The key travels in a request header, never in the URL, and prompts and replies are never logged.
- OpenAI has its own `OPENAI_API_KEY`, so switching providers can't send one provider's key
  to another. Your key must be in `OPENAI_API_KEY`, not `LLM_API_KEY`.
- `gpt-6-luna` is a reasoning model. Extraction and the assistant send
  `reasoning_effort: "none"` by default (`OPENAI_REASONING_EFFORT`), because copying text
  and choosing a tool need no long reasoning, and at `none` the model still accepts
  `temperature: 0`. At `low` it rejects temperature 0. When OpenAI rejects `temperature`
  (or `reasoning_effort`, for non-reasoning models), the OpenAI client drops that one
  parameter, resends once and remembers it for the run. It logs a warning, so you know the
  output is less repeatable. Other providers never drop anything.
- OpenAI uses strict structured output: its reply is guaranteed to match the schema.
  Gemini and Groq use their JSON-schema modes. Invalid JSON from any provider gets one repair
  call, then a clean failure.
- **Cost:** every client counts input and output tokens from the provider's own `usage`
  numbers. `make extract-sample`, `make assistant-eval` and `make batch-benchmark ARGS="--llm real"`
  print them. Multiply by your provider's current price per token.

### AI extraction (try it on a sample)

With a provider configured (above):

```bash
make extract-sample FILE=samples/text-invoice.pdf   # prints fields, status, evidence, confidence, tokens
```

This sends the document's text to that provider, so only use invoices you may share.
Tests never call a real provider: they use a fake client or a mock HTTP server.

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
| `GET` | `/documents/{id}/ledger` | Ledger sheets of a spreadsheet: column mapping and rows (with sheet row numbers) |
| `PUT` | `/documents/{id}/column-mapping` | Fix how a sheet's columns map to invoice fields; rows are re-mapped at once |
| `POST` | `/cross-checks` | Compare two documents in the background → 202 |
| `GET` | `/cross-checks`, `/cross-checks/{id}` | List checks; one check with summary and findings (evidence on both sides) |
| `POST` | `/jobs/retry-failed` | Queue every document whose last job failed; returns how many |
| `POST` | `/assistant/conversations` | Start a conversation (`GET` lists them; `GET /{id}` shows one) |
| `POST` | `/assistant/conversations/{id}/messages` | Ask; the answer streams as Server-Sent Events (`token`, `tool_call`, `tool_result`, `pending_action`, `citations`, `done`) |
| `POST` | `/assistant/actions/{id}/confirm` · `/cancel` | Run or drop a change the assistant proposed |
| `GET` | `/stats` | Counts for the greeting and the dashboard |
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
