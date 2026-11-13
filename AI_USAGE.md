# AI usage log

How AI tools were used to build this project. Each row is one step.

| Date | Step | Tool | Prompt summary | What I changed by hand |
|------|------|------|----------------|------------------------|
| 2026-11-11 | 1: Project skeleton | Claude Code (Claude Opus 5.5) | Create the repo skeleton: FastAPI app with settings and a real `/health` check (Postgres + Redis), pytest, async Alembic, Docker Compose (api, worker placeholder, pgvector Postgres, Redis with healthchecks), Dockerfile, `.env.example`, `.gitignore`, Makefile, CLAUDE.md, README skeleton. Build, run, test, lint, first commit. | _To fill in after review_ |
| 2026-11-12 | 2: Safe uploads + parsing | Claude Code (Claude Opus 5.5) | Magic-byte file detection, streaming upload storage (size cap, SHA-256, UUID names), per-page PDF text extraction with OCR flags, xlsx/xls/csv parsing with header detection into one ParsedDocument model, POST /documents with per-file errors, tests with in-code fixtures, samples folder. | _To fill in after review_ |
| 2026-11-12 | 3a: OCR | Claude Code (Claude Opus 5.5) | Tesseract (eng+ben) in Docker; OpenCV preprocessing (grayscale, upscale, denoise, deskew, adaptive binarize); OCR engine with word-level confidence and PSM 6/4 fallback; page-by-page OCR pipeline for scanned PDF pages and images wired into parse_document; generated-image tests; ocr_sample script. | _To fill in after review_ |
| 2026-11-12 | 3b: AI extraction | Claude Code (Claude Opus 5.5) | Pydantic schema where every field is value + status + evidence + page; Gemini and Groq clients over httpx (temperature 0, JSON-schema output, timeout, retries with backoff and jitter); prompt with data delimiters and page markers; page chunking with overlap and merge; pure post-processing (grounding, Decimal amounts, ambiguous dates, currency, supplier key, confidence); service with one JSON repair attempt; FakeLLMClient tests; extract_sample script. | _To fill in after review_ |
| 2026-11-13 | 4a: Validation + database | Claude Code (Claude Opus 5.5) | Pure validation rules (Decimal, tolerance, dates, confidence), duplicate detection behind a repository protocol, SQLAlchemy 2 async models and an Alembic migration (pgvector, generated tsvector with GIN), repository, idempotent process_document pipeline, SHA-256 upload dedupe, tests on a throw-away Postgres database, docs/schema.md. | _To fill in after review_ |
| 2026-11-13 | 4b: Worker + REST API | Claude Code (Claude Opus 5.5) | arq worker on the existing Redis with retries only for temporary errors, backoff with jitter, page-scaled job timeouts, stuck-job requeue and OCR progress; full REST API (list/detail/status/result, audited corrections that survive re-processing, reprocess, retry-failed, keyword search with safe snippets), shared error format, CORS; normalised invoice number key; async API tests that run job functions directly. | _To fill in after review_ |
| 2026-11-13 | 5a: Semantic + hybrid search | Claude Code (Claude Opus 5.5) | Local all-MiniLM-L6-v2 embedder (CPU torch, model cached in a volume) behind an Embedder protocol plus a FakeEmbedder; text_chunks table with an HNSW cosine index (migration); per-page chunking with overlap; idempotent embed stage that only warns on failure; keyword/semantic/hybrid search with reciprocal rank fusion, grouped by document; embed-backfill; similarity threshold calibrated by measurement. | _To fill in after review_ |
| 2026-11-13 | 5b: Frontend + review endpoints | Claude Code (Claude Opus 5.5) | Backend: file download, page list, cached PNG previews, corrections history, approve with override rule (new migration; only new errors re-open review). Frontend: Next.js App Router + TypeScript strict + Tailwind + TanStack Query, generated OpenAPI types, upload with XHR progress, documents table with conditional polling, split-view review with inline validated editing, issues/duplicates/history, approve and confirm modals, review queue, search with a safe snippet renderer; Vitest unit tests, Playwright smoke test; Docker service. Checked in a real browser at 375 px and 1280 px. | _To fill in after review_ |
| 2026-11-13 | 5c: Batch documents | Claude Code (Claude Opus 5.5) | Segmentation of multi-invoice PDFs (page-signal rules, LLM classifier only for unclear pages, multi-page invoices kept whole); migration for invoice segments and resumable jobs; per-invoice concurrent extraction that saves each invoice and resumes only unfinished ones; corrections kept across boundary changes with a review issue; API, search and UI for many invoices per document (invoice switcher); synthetic 1000-page benchmark with measured timings and segmentation accuracy. | _To fill in after review_ |
| 2026-11-13 | 5c follow-up: parallel OCR + partial results | Claude Code (Claude Opus 5.5) | OCR in a killable spawn process pool with bounded in-flight pages, page-order results and per-page progress; measured and found OpenCV already used all cores, so OpenCV threads are split per worker (small real gain, documented honestly); partially_failed status (migration) and resume jobs so retry-failed redoes only failed invoices; frontend badge, filter and retry button. | _To fill in after review_ |

## Prompt design (step 3b)

Why the extraction prompt looks the way it does:

- **Copy, don't compute.** The model only copies printed text plus a quote (evidence) and a page
  number. Parsing numbers and dates and checking the maths happens in tested Python code
  (CLAUDE.md rule 2). LLMs are good at finding text and unreliable at arithmetic and
  locale-dependent formats like `03/04/2026`.
- **"missing" is a valid answer.** Every field has a status, and the schema requires every key,
  so the model must say "missing" explicitly instead of leaving a gap it might fill by guessing.
- **Evidence can be checked.** Because the model must quote the document, code can verify each
  value (`is_grounded`). A value whose quote isn't in the document becomes "uncertain", or
  "missing" if nothing supports it. This catches hallucinations without trusting the model.
- **The document is untrusted data.** It sits between `<document-ID>` tags whose ID is a hash of
  the text itself, so the document can't contain its own closing tag. The system prompt says
  instructions inside the data must be ignored, and code also flags lines that look like
  injected instructions and lowers any field quoting them. That's defence in depth.
- **Page markers** (`=== PAGE n ===`) let the model cite pages; fake markers inside the text
  are neutralised.
- **Chunking:** about 6000 tokens per chunk with 1 page of overlap, so a table split across pages
  is seen whole once. Header fields come from the first chunk that has them, totals from the
  last, and line items from all chunks, with overlap duplicates removed.
- **Temperature 0 and a JSON schema** for repeatable, machine-checked output. One repair retry
  for invalid JSON, then a clean failure instead of an exception.

