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
| 2026-11-14 | 6a follow-ups: rule fix, assistant miss, hard subset | Claude Code (Claude Opus 5.5) | Fix the no-subtotal line-sum rule with unit tests and dev examples, reporting pre-fix test results as the headline; find and fix the root cause of the assistant's "no Umbrella Trading invoice" miss with a regression test; add a hard test subset (noise, blur, phone photos, 8-12° rotation, faint scans, crowded and two-column layouts) reported as its own row; re-run make eval. | _To fill in after review_ |
| 2026-11-14 | 6b: Hardening the worker against hangs | Claude Code (Claude Opus 5.5) | After the embedding deadlock: run parsing, OCR and embedding in killable process pools with a hard timeout per call; embedding problems never block or fail a document; job heartbeats, a watchdog that retries or fails stuck jobs, and a worker that recycles itself; Docker healthchecks and restart policies; regression tests (512-token page within 5 s, a step that sleeps forever is killed and the next job runs, the watchdog picks up a stuck job); pin torch/transformers/sentence-transformers and document eager attention. | _To fill in after review_ |
| 2026-11-14 | 6c: Photos and noisy scans | Claude Code (Claude Opus 5.5) | Generate a separate hard dev set (different seed) and work only on it; photo preprocessing (page edges and perspective warp, shadow/lighting removal, CLAHE) applied only to images that look like photos, each step kept only if measured to help; vision fallback below OCR_MIN_CONFIDENCE with fields marked "vision", lower confidence and always reviewed; tests; re-run make eval and report hard-test before/after rows. | _To fill in after review_ |

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

## Follow-ups to step 6a

- **Report the held-out result, not the improved one.** The line-sum bug was found on the
  test split, so the headline stays the pre-fix test result and the post-fix result is
  labelled next to it. The fix also got unit tests and two new dev invoices, so it is
  covered outside the test split.
- **Measure the cause before fixing.** The assistant miss looked like a retrieval
  threshold problem. Asking the same question 8 times with the real model showed it was
  keyword search requiring every word, after the model chose keyword mode. Similarities,
  the supplier filter and the batch mapping were measured and ruled out first.
- **A harder subset found a production bug, not just lower numbers.** Crowded pages hung
  the embedding model (torch's fused attention on CPU never returns for longer inputs), and
  the hung thread couldn't be cancelled. It was fixed with eager attention (identical
  vectors), and the regression tests fail without the fix.
- **Don't judge flags on the hard subset by precision.** Its documents have no planted
  defects, so the useful measure is whether a misread reaches review unflagged. It never
  did (7/7 flagged).

## Hardening the worker (step 6b)

- **Kill processes, not threads.** A hung thread can't be stopped, and it keeps its locks;
  that is how one long page blocked 40 jobs. Every CPU-heavy step now runs in a small pool
  of long-lived spawn processes. Each call has a hard timeout, and the reply is awaited on
  the event loop (a reader on the pipe), not in a helper thread, so a timeout leaves nothing
  behind. The pool generalises the earlier parallel-OCR pattern, which also gained a
  per-page timeout.
- **What a timeout means depends on the step.** A parse timeout is a permanent failure
  (the same file would hang again). A crashed process is temporary (often memory). An OCR
  timeout fails one page. An embedding timeout only costs the semantic index.
- **Heartbeat on progress, not just on being alive.** The heartbeat stops when the job's
  progress stops, so a job stuck on anything is caught even when its worker is healthy.
  The watchdog claims stuck jobs atomically, and a monitor thread (which works even when
  the event loop is blocked) restarts the worker. A blocked loop can't run anything else.
- **Tests drove out a real bug.** The first "embedding times out" test crashed the job: a
  rollback had expired the worker's `job` object, and reading `job.id` afterwards raised
  MissingGreenlet. It was fixed in its own commit, with a test that fails without the fix.
  Tests that patch code in-process use explicit inline pools; everything else runs the real
  process pools.
- **Pinned and documented.** torch, transformers and sentence-transformers are pinned to
  the measured versions, with the reason (eager attention) in pyproject.toml and the
  README, and a slow test that times a 512-token page.

## Photos and noisy scans (step 6c)

- **A separate dev set for tuning.** A hard dev set (same kinds of damage, different seed,
  its own invoice numbers so duplicate detection can't link it to test) was generated
  first. `make hard-dev-bench` reads only those files. The hard test files were neither
  looked at nor tuned on, so their before/after rows are a held-out measurement.
- **Keep only what the measurements support.** Each photo step was measured on its own
  and in combination (OCR confidence and field accuracy with the real LLM). Flatten
  (dividing by the estimated paper brightness) was kept; CLAHE made things worse and was
  dropped from the default. The perspective warp is correct on a camera-projected test
  image, but never fired on the synthetic photos, so it stays off by default. That limit of
  the dataset is reported rather than hidden.
- **Testing found a wrong assumption.** The first warp sized the page from its edge lengths
  in the photo, which perspective foreshortens (aspect 1.23 instead of 1.41). The fix
  recovers the real aspect ratio from the four corners (Zhang & He's method). The first
  test image also didn't come from a real camera model, so it was replaced with a page
  projected through a pinhole camera.
- **Vision values are never trusted like OCR values.** They can only be checked against
  the model's own transcription, so they carry `source: "vision"`, a confidence cap below
  the review threshold, and a mandatory review issue. The eval showed the fallback never
  triggered: the remaining errors are confident misreads, which a confidence threshold
  can't catch. That is reported as a finding, not tuned away on test.
- **A small tooling bug.** The eval's "store the latest run" step picked the newest JSON
  in the results folder, which was now a benchmark file. It now only picks timestamped run
  files, with a test.
