# Test results

- **Date:** 2026-11-14 19:03 UTC
- **Commit:** `0d74fef` (the code under test; this report is committed after it)
- **How to reproduce:** `make up` then `make test-report`
- **Raw files:** [test-results/](test-results/) (JUnit XML, backend `coverage.xml`, frontend `coverage-summary.json`)

## Counts

| Suite | Tests | Passed | Failed | Skipped | Time |
|---|---|---|---|---|---|
| Backend (pytest, including slow tests) | 420 | 419 | 0 | 1 | 120.2 s |
| Frontend (Vitest) | 72 | 72 | 0 | 0 | 1.3 s |
| End-to-end smoke (Playwright) | 1 | 1 | 0 | 0 | 8.1 s |

## Backend coverage (lines)

Overall: **91.5%**. Code that runs in child processes (parsing, OCR and embedding steps in `app/ocr/`, `app/steps.py`, `app/procpool.py`) is exercised by the tests but not traced there, so those numbers are lower bounds.

| Package | Lines | Covered |
|---|---|---|
| `app/api/` | 886 | 96.2% |
| `app/assistant/` | 527 | 74.4% |
| `app/config.py` | 86 | 96.5% |
| `app/crosscheck/` | 286 | 94.4% |
| `app/dashboard.py` | 43 | 93.0% |
| `app/db.py` | 13 | 84.6% |
| `app/evaluation/` | 204 | 95.6% |
| `app/extraction/` | 972 | 95.7% |
| `app/filters.py` | 39 | 94.9% |
| `app/invoices.py` | 163 | 96.3% |
| `app/jobs.py` | 51 | 86.3% |
| `app/ledger/` | 143 | 98.6% |
| `app/main.py` | 38 | 97.4% |
| `app/models.py` | 249 | 100.0% |
| `app/ocr/` | 415 | 83.6% |
| `app/parsing/` | 466 | 89.1% |
| `app/pipeline.py` | 272 | 92.3% |
| `app/previews.py` | 54 | 96.3% |
| `app/procpool.py` | 145 | 77.2% |
| `app/repository.py` | 154 | 98.1% |
| `app/search/` | 255 | 95.7% |
| `app/security.py` | 102 | 86.3% |
| `app/segmentation/` | 140 | 97.9% |
| `app/steps.py` | 77 | 70.1% |
| `app/storage.py` | 46 | 97.8% |
| `app/validation/` | 111 | 100.0% |
| `app/watchdog.py` | 84 | 100.0% |
| `app/worker.py` | 113 | 75.2% |

## Frontend coverage (lines, unit tests only)

Overall: **70.3%**. Pages are covered by the end-to-end smoke test, not by unit tests, so their unit coverage is low by design.

| Folder | Lines | Covered |
|---|---|---|
| `src/components` | 144 | 59.7% |
| `src/components/chat` | 66 | 84.8% |
| `src/components/controls` | 162 | 74.1% |
| `src/components/review` | 11 | 100.0% |
| `src/lib` | 189 | 70.9% |
| `src/proxy.ts` | 10 | 20.0% |

## Slowest tests: Backend (pytest, including slow tests)

| Time | Test |
|---|---|
| 15.20 s | `tests.test_search_semantic::test_real_model_finds_meaning_not_just_words` |
| 14.23 s | `tests.test_embedder::test_a_512_token_page_embeds_within_5_seconds_in_the_worker_pool` |
| 9.70 s | `tests.test_embedder::test_real_model_embeds_a_crowded_page_quickly` |
| 7.18 s | `tests.test_ocr::test_very_noisy_image_warns_low_confidence` |
| 5.94 s | `tests.test_steps::test_a_parse_step_that_never_returns_is_killed_and_the_next_job_runs` |
| 3.94 s | `tests.test_api::test_upload_then_job_gives_processed_result_with_evidence` |
| 3.91 s | `tests.test_pdf_parser::test_thousand_page_pdf_memory_stays_bounded` |
| 3.26 s | `tests.test_ocr_parallel::test_parallel_matches_sequential_ocr` |
| 2.57 s | `tests.test_steps::test_an_embedding_that_never_returns_only_costs_the_semantic_index` |
| 2.00 s | `tests.test_ocr_parallel::test_results_keep_page_order_and_progress_counts_each_page` |

## Slowest tests: Frontend (Vitest)

| Time | Test |
|---|---|
| 0.22 s | `src/__tests__/invoice-switcher.test.tsx::invoice switcher > lists invoices with pages and moves between them` |
| 0.20 s | `src/__tests__/controls.test.tsx::Select > is labelled, opens a themed menu and maps the empty option` |
| 0.15 s | `src/__tests__/controls.test.tsx::Filters toolbar > starts empty, shows removable chips and clears all` |
| 0.13 s | `src/__tests__/controls.test.tsx::DateRange > orders the two clicked dates` |
| 0.12 s | `src/__tests__/components.test.tsx::approve dialog > requires an override reason while errors remain` |
| 0.10 s | `src/__tests__/components.test.tsx::approve dialog > approves at once when there are no errors` |
| 0.09 s | `src/__tests__/assistant.test.tsx::safe markdown > renders bold, lists, code and safe links` |
| 0.07 s | `src/__tests__/controls.test.tsx::DocumentPicker > filters by filename or supplier and picks with the keyboard` |
| 0.06 s | `src/__tests__/controls.test.tsx::DateRange > applies a preset and a picked range` |
| 0.04 s | `src/__tests__/assistant.test.tsx::pending action card > changes nothing until Confirm, then calls the API` |

## Slowest tests: End-to-end smoke (Playwright)

| Time | Test |
|---|---|
| 8.13 s | `smoke.spec.ts::upload, review, correct, approve, search` |
