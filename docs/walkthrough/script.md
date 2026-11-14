# Walkthrough script

A silent screen recording with on-screen subtitles only (no voice, no music), about 6
minutes. It uses synthetic demo data only. `make walkthrough` records it from this file:
the recorder reads every table row below, performs the action, then holds the screen while
the subtitle is shown. Each subtitle stays up for its word count ÷ 2.5 seconds (at least
2 seconds), and long subtitles wrap onto two lines of at most about 12 words. Every scene
starts with a 1.5-second title card. The timings in `walkthrough.srt` are measured during
the recording, so they match the video exactly.

Columns: **Cue** (the recorder's step id), **Subtitle** (shown on screen), **On screen**
(what happens before and while it shows).

## 1. Intro (20 s)

**On screen:** the README header with the dashboard screenshot, then the architecture
diagram.

| Cue | Subtitle | On screen |
|---|---|---|
| `intro-problem` | Finance teams get invoices as PDFs, scans, photos and spreadsheets. | README title and summary |
| `intro-what` | I built a system that reads them and extracts the key fields. | Summary paragraph highlighted |
| `intro-evidence` | Every value comes with the exact quote and page it came from. | Scroll to the dashboard screenshot |
| `intro-arch` | The parts: a web app, an API, a worker, Postgres and an LLM. | Architecture diagram |

## 2. Upload (40 s)

**On screen:** the Documents page; six files dragged onto the drop zone at once; the table
fills with live progress.

| Cue | Subtitle | On screen |
|---|---|---|
| `upload-page` | I start on the Documents page with an empty upload area. | Documents page, drop zone highlighted |
| `upload-drag` | I drop six different files onto it at once. | Cursor drags six files in and drops them |
| `upload-kinds` | A digital PDF, a scan, a phone photo, Excel, CSV and a batch. | The upload list with six files |
| `upload-checked` | Each file's type is checked from its bytes, not its name. | Upload results, all accepted |
| `upload-progress` | The table shows each file's live status and stage. | Status column, files processing |
| `upload-pages` | Scans and photos show page-by-page OCR progress. | A progress bar on a scanned file |
| `upload-background` | Processing runs in the background, so I can keep working. | Rows turning processed |

## 3. Review (60 s)

**On screen:** the review queue, then the review page of a scanned invoice with a wrong
total.

| Cue | Subtitle | On screen |
|---|---|---|
| `review-queue` | The review queue lists invoices that a person should check. | Review queue |
| `review-open` | I open a scanned invoice that needs review. | Click the scanned invoice |
| `review-evidence` | Each field shows the quote it came from. I click one. | Click an evidence chip |
| `review-jump` | The page preview jumps to the page with that quote. | Page preview highlighted |
| `review-confidence` | This bar shows how confident the system is in the value. | Confidence bar highlighted |
| `review-issue` | Plain code checks the maths. Here the total does not add up. | Issue card highlighted |
| `review-expected` | It shows what it expected and what it actually found. | Expected and found values |
| `review-vision` | For scans, a second reading of the page image double-checks values. | A field whose reasons show the vision reading |
| `review-correct` | I correct the total to the expected amount and save. | Edit Total, type, Save |
| `review-cleared` | The issue clears, and the change is kept in the history. | Issues and history highlighted |
| `review-approve` | Then I approve the invoice under my name. | Approve dialog, then "Approved by" |

## 4. Batch and duplicates (30 s)

**On screen:** the batch PDF split into invoices; then a scanned copy of an earlier invoice.

| Cue | Subtitle | On screen |
|---|---|---|
| `batch-open` | The batch PDF was split into separate invoices. | Invoice switcher highlighted |
| `batch-switch` | The switcher moves between them. Each has its own pages. | Click Next invoice |
| `batch-pages` | The preview follows the pages of the chosen invoice. | Page preview |
| `dup-open` | This scan is a copy of an invoice that came in as a PDF. | Duplicate document opened |
| `dup-flag` | It is flagged as a duplicate, with a link to the original. | Duplicate issue and link |

## 5. Cross-check (40 s)

**On screen:** the Cross-check page comparing the batch PDF with the Excel ledger.

| Cue | Subtitle | On screen |
|---|---|---|
| `xc-intro` | Cross-check compares invoices with other records. | Cross-check page |
| `xc-pick-a` | First I pick the batch PDF. | Document A picker |
| `xc-pick-b` | Then the Excel ledger it should match. | Document B picker |
| `xc-run` | The comparison runs in the background. | Click Run cross-check |
| `xc-summary` | The summary counts matched, mismatched and missing invoices. | Summary tiles |
| `xc-finding` | Each finding shows both values side by side. | Findings table |
| `xc-links` | The links open the exact PDF page or ledger row. | Evidence links |
| `xc-nochange` | Nothing is changed automatically. Mismatches go to review. | Findings table |

## 6. Search (30 s)

**On screen:** the Search page, one query in keyword mode, then in semantic mode.

| Cue | Subtitle | On screen |
|---|---|---|
| `search-keyword` | I search for "shipping costs" using exact words only. | Type the query, keyword mode |
| `search-none` | Keyword search finds nothing. No invoice uses those words. | Empty result |
| `search-semantic` | Semantic search looks for the meaning instead. | Switch to semantic mode |
| `search-found` | It finds courier and freight charges: a match by meaning. | First result |
| `search-badge` | The badge says how each result matched. | matched_by badge |

## 7. Assistant (60 s)

**On screen:** the assistant home page; a question with a computed total; a correction that
needs confirmation; the audit history.

| Cue | Subtitle | On screen |
|---|---|---|
| `chat-home` | The home page is an assistant that works on the same data. | Greeting and live summary |
| `chat-ask` | I ask how much we spent with Meghna Traders. | Type and send the question |
| `chat-tools` | It looks things up with tools. The trace shows which ones. | Tool trace |
| `chat-sum` | The total comes from a database query, not the model's arithmetic. | Answer with the total |
| `chat-cite` | It cites the file, invoice number and page for each claim. | Citation chips |
| `chat-correct` | Now I ask it to fill in a missing customer name. | Type and send the request |
| `chat-card` | It only proposes the change. Nothing happens until I confirm. | Confirmation card |
| `chat-confirm` | I confirm it. | Click Confirm |
| `chat-audit` | The change now appears in that invoice's history. | Review page, History |

## 8. Dashboard and evaluation (40 s)

**On screen:** the dashboard, then the headline of `docs/evaluation.md` and the known
limitations.

| Cue | Subtitle | On screen |
|---|---|---|
| `dash-kpis` | The dashboard shows what came in and what needs a person. | KPI cards |
| `dash-eval` | It also shows the latest stored evaluation run. | Latest evaluation card |
| `eval-clean` | On clean synthetic test invoices, field accuracy was 100%. | Evaluation table, clean column |
| `eval-hard` | On the hard subset of damaged scans and photos, it was 88.9%. | Evaluation table, hard column |
| `eval-limits` | Confident OCR misreads on heavy noise are the main weakness. | Known limitations |
| `eval-review` | That is why doubtful values always go to a person. | Known limitations |

## 9. Engineering (30 s)

**On screen:** the API docs at `/docs`, then `docs/test-results.md`.

| Cue | Subtitle | On screen |
|---|---|---|
| `eng-docs` | The API is documented with OpenAPI at /docs. | Swagger UI |
| `eng-key` | Every data route needs an API key, added by the web server. | Endpoint list |
| `eng-tests` | 419 backend tests, 72 frontend tests and an end-to-end test pass. | Test results counts |
| `eng-coverage` | Backend line coverage is 91.5%. | Coverage table |
| `eng-close` | Thanks for watching. The code and docs are in the repository. | Test results page |
