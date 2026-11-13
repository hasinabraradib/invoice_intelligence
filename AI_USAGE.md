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
| 2026-11-13 | 5d: Cross-verification | Claude Code (Claude Opus 5.5) | Ledger detection with header synonyms and stored rows (sheet + row evidence), column-mapping override; migration for ledger_rows, cross_checks, findings; pure comparison (invoice number, then supplier+date+total; Decimal tolerance; duplicates; line items by description similarity) run as an arq job; CROSS_CHECK_MISMATCH issues that survive re-validation and set needs_review without changing values; Cross-check page and ledger row highlighting; ledgers processed without an LLM. | _To fill in after review_ |
| 2026-11-14 | 5e: AI assistant | Claude Code (Claude Opus 5.5) | Native tool calling for Gemini/Groq; agent loop (max 6 tool steps) over read tools wrapping existing services and fixed SQL sums/counts per currency; write tools that only propose actions, confirmed by the user, with expiry and audit trail; code-level guard against injected instructions; conversations/messages/actions tables; SSE streaming with Stop; chat as home page with greeting, suggestions, tool trace, citations, confirmation cards, attachments; safe Markdown renderer; 15-question eval set and scoring script. Fixed an approval bug in batch documents found on the way. | _To fill in after review_ |
| 2026-11-14 | 5f: OpenAI provider | Claude Code (Claude Opus 5.5) | Add OpenAI as a third LLM provider (LLM_PROVIDER=openai, OPENAI_API_KEY, default gpt-6-luna with reasoning effort none/low, temperature dropped only if the model rejects it) over httpx: strict JSON-schema extraction, native tool calling, temperature 0, same timeout/retry/backoff; record token usage for cost; mock-server tests (shapes, tool calls, 429 retry, invalid-JSON repair); live check on one synthetic invoice and one short assistant conversation; check that .env is ignored and no key is in git history. | _To fill in after review_ |
| 2026-11-14 | 6a: Real-provider check + evaluation | Claude Code (Claude Opus 5.5) | Confirm 5d and parallel OCR are complete; real OpenAI runs on synthetic samples and one assistant conversation (question, sum, proposed correction) over SSE; fix what they find; sanitised real replies as fixtures; concurrency cap for rate limits; fixed-seed labelled synthetic dataset (41 invoices, 7 file types, planted defects, dev/test split) with a real-data template; make eval end to end through API and worker with field, line item, flag, duplicate, segmentation, cross-check, timing and cost metrics; docs/evaluation.md; latest accuracy on the dashboard. | _To fill in after review_ |

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


## Assistant prompt design (step 5e)

- **Tools instead of free text over documents.** The model reaches data only through tools
  over our own services, so every answer is grounded in tool results it can cite.
- **Arithmetic moved out of the model.** Totals and counts come from `sum_invoices` /
  `count_invoices` (parameterised SQL, Decimal, one total per currency). The prompt forbids
  mental arithmetic and adding up different currencies.
- **Citations required** (file, invoice number, page), and "nothing found" is a valid answer.
- **Changes only by confirmation.** Write tools return a proposal, never a result, and the
  prompt tells the model to say a change happens only when the user confirms. Code also
  refuses write tools when the user's own message does not ask for a change. That is the
  second layer if a document's text manages to persuade the model.
- **Untrusted-data delimiters** around tool results (hash-tagged, like the extraction prompt).


## OpenAI provider (step 5f)

- **Same rules as the other providers.** Chat Completions over `httpx`, temperature 0,
  JSON schema for extraction, native tools for the assistant, shared retry/backoff code.
  Groq speaks the same API, so both share one base class.
- **Strict structured output.** Our schemas already require every key and forbid extra ones,
  which is what OpenAI's `strict: true` needs, so the reply is guaranteed to match the schema.
  A refusal is a permanent error, not a retry.
- **Default model `gpt-6-luna` with `reasoning_effort: "none"`** (chosen by the project owner;
  the first draft used gpt-4.1-mini). Probed the live API first: gpt-6-luna rejects
  `temperature: 0` on its own and at effort `low`, but accepts it at effort `none`. So
  `none` is the default, which keeps output repeatable. If OpenAI rejects `temperature` or
  `reasoning_effort` with a 400, the client drops that parameter for OpenAI only, resends
  once and remembers it, instead of failing.
- **Separate key variable.** `OPENAI_API_KEY` is never filled from `LLM_API_KEY`, so a
  Gemini/Groq key can't be sent to OpenAI by mistake.
- **Token usage** from the provider's `usage` field is summed per client and printed by the
  sample, eval and benchmark scripts. That gives cost from measured tokens, not estimates.
- **Live check (2026-11-14, real key, gpt-6-luna, reasoning effort `none`, temperature 0 kept):**

  | Check | Result | LLM calls | Input tokens | Output tokens |
  |-------|--------|-----------|--------------|---------------|
  | `make extract-sample` on a synthetic 1-page invoice (`samples/`, git-ignored) | All 9 fields and 2 line items found, values and evidence correct, 7.3 s | 1 | 2,286 | 915 |
  | Assistant, 2 questions in one conversation (count + total per currency; largest supplier with citation) | Right tools (`count_invoices`, `sum_invoices`, `search_documents`), answers match the database (7 invoices / 130.00; Hooli 40.00, E-500), cited | 9 | 26,118 | 350 |

  No parameter had to be dropped. Multiply the tokens by OpenAI's current gpt-6-luna price for cost.

## Real-provider check and evaluation (step 6a)

- **Real runs found real bugs, fixed in small commits:**
  - CSV invoices with a key/value block above the item table were rejected as an
    unsupported type.
  - A worker restart left jobs "running" for 30 minutes, because arq's redelivery was
    skipped.
  - Excel date cells (`2026-10-03 00:00:00`) were lost. `make eval` found this on the dev
    split.
- **Request/response formats held up.** Structured output, tool calls and the SSE stream
  all worked first time against OpenAI. The recorded fixtures showed prompt caching (about
  90% of input tokens), so cached tokens are now counted separately.
- **Ground truth is independent of the code under test.** Expected validation flags come
  from each document's planted design, not from the app's rules. That is how the
  evaluation caught a real rule bug (a line-sum check that ignores tax when there is no
  subtotal). It shows up only on the test split, so it is reported and not fixed, to
  keep the test split untouched.
- **Scorer bugs are not system bugs.** Two first-run "failures" were mistakes in the
  scoring: the cross-check matching and the assistant's date comparison. They were fixed
  in the scorer with tests, and both runs are kept so the history is visible.
- **Isolation.** The evaluation runs in its own compose project with a fresh database,
  because duplicate detection and the assistant see every document; it never touches dev
  data.

