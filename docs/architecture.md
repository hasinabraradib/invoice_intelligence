# Architecture

Three views: the running components, the path a document takes from upload to review, and
how the assistant answers with tools. Each diagram is Mermaid (rendered by GitHub), with a
pre-rendered SVG and PNG in [architecture/](architecture/) for viewers that don't render
Mermaid. The database tables are described in [schema.md](schema.md), and the security
boundaries in [security.md](security.md).

## 1. Components

Rendered: [SVG](architecture/components.svg) · [PNG](architecture/components.png)

```mermaid
flowchart LR
    user([Reviewer's browser])

    subgraph web["frontend (Next.js, port 3000)"]
        pages["Pages: Assistant, Dashboard, Documents,<br/>Review, Cross-check, Search"]
        proxy["/api/* route handler<br/>adds X-API-Key (server-only)"]
    end

    subgraph backend["Private Docker network"]
        api["api (FastAPI)<br/>auth, rate limits, validation,<br/>assistant (SSE), search"]
        redis[("Redis<br/>job queue + rate-limit counters")]
        subgraph worker["worker (arq)"]
            loop["Job loop<br/>heartbeats, watchdog cron"]
            parse["parse pool<br/>(process, timeout)"]
            ocr["OCR pool<br/>(process per page)"]
            embed["embedding pool<br/>(process, timeout)"]
        end
        pg[("Postgres + pgvector<br/>documents, invoices, issues,<br/>corrections, chunks, conversations")]
        files[("uploads volume<br/>original files")]
    end

    llm{{"LLM provider<br/>(OpenAI, Gemini or Groq)"}}

    user -- "HTTPS / same origin" --> pages
    pages --> proxy
    proxy -- "X-API-Key" --> api
    api --> pg
    api --> files
    api -- "enqueue" --> redis
    api -- "rate limits" --> redis
    redis -- "jobs" --> loop
    loop --> parse & ocr & embed
    loop --> pg
    loop --> files
    loop -- "extract, classify pages,<br/>vision second opinion" --> llm
    api -- "assistant chat + tools" --> llm
```

| Component | Role |
|---|---|
| **frontend** | The UI. The browser only talks to this server. Its `/api/*` route handler forwards allow-listed paths to the API and adds the key, which never reaches the browser. |
| **api** | Uploads (magic-byte type check, size caps, dedupe by hash), results, corrections, approvals, search, cross-checks, the assistant (streamed over SSE), and dashboard statistics. |
| **worker** | Runs the processing pipeline and cross-checks from the Redis queue. CPU-heavy steps run in separate processes with hard timeouts, so a hostile file is killed, not the worker. |
| **Redis** | The job queue (arq) and the per-user rate-limit counters. |
| **Postgres + pgvector** | Everything durable: documents, page texts, invoices, line items, issues, corrections, search chunks with embeddings, conversations, assistant actions and evaluation runs. |
| **LLM provider** | Extraction (quote-backed values), classification of unclear batch pages, the vision second opinion, and the assistant. It never computes or "fixes" values. |

## 2. Document flow: upload to review

Rendered: [SVG](architecture/document-flow.svg) · [PNG](architecture/document-flow.png)

```mermaid
flowchart TD
    up["Upload (browser → proxy → API)"] --> check{"Magic-byte type check,<br/>size and count caps"}
    check -- "not allowed" --> reject["Rejected with a clear error<br/>(the other files still go on)"]
    check -- "ok" --> dedupe{"Same SHA-256<br/>already stored?"}
    dedupe -- "yes" --> existing["Return the existing document"]
    dedupe -- "no" --> store["Store the file (random name) and a<br/>document row; enqueue a job"]
    store --> parse["Parse in a process with a timeout<br/>PDF text layer · images · XLSX/CSV sheets"]
    parse --> needocr{"Pages without<br/>a text layer?"}
    needocr -- "yes" --> photo["Photo preprocessing<br/>(flatten lighting; optional warp, contrast)"]
    photo --> ocr["Tesseract OCR, pages in parallel,<br/>per-page timeout and confidence"]
    needocr -- "no" --> texts
    ocr --> texts["Save page texts<br/>(a retry reuses them)"]
    texts --> seg["Segmentation: rules first,<br/>LLM classifier only for unclear page headers"]
    seg --> each["Each invoice segment, in parallel (bounded)"]
    each --> extract["LLM extraction: values + the quotes they came from"]
    extract --> ground["Grounding: every quote must be in the page text,<br/>else the value is 'uncertain'"]
    ground --> trig{"OCR'd pages with low confidence<br/>or suspicious values?"}
    trig -- "yes" --> vision["Vision second opinion from the page image:<br/>agree → small confidence bonus;<br/>disagree → uncertain, both readings kept, review"]
    trig -- "no" --> validate
    vision --> validate["Plain-code validation: dates, numbers, currencies,<br/>line sums, subtotal + tax = total, duplicates"]
    validate --> save["Upsert the invoice, line items and issues<br/>(idempotent: reprocessing replaces, never duplicates)"]
    save --> embed["Chunk and embed page texts for semantic search<br/>(failure only adds a warning)"]
    embed --> status{"Any errors or<br/>low confidence?"}
    status -- "no" --> processed["Processed"]
    status -- "yes" --> review["Needs review"]
    review --> human["Reviewer: evidence per field, corrections (audited),<br/>approve with an override reason if errors remain"]
    processed --> xc
    human --> xc["Cross-check (on request): invoices vs a ledger sheet<br/>or another PDF → matched / mismatched / missing"]
```

What happens when things go wrong:

- **Permanent errors** (corrupt file, unsupported type, password, a parser that timed out)
  fail the document at once, with the reason shown.
- **Temporary errors** (LLM rate limit or outage, database hiccups) send the job back to the
  queue with backoff, up to `JOB_MAX_ATTEMPTS`.
- **A batch where some invoices fail** becomes *partially failed*. The good invoices are
  kept, and a retry only redoes the failed ones.
- **A stuck job** (no heartbeat) is retried or failed by the watchdog. A worker whose event
  loop blocks exits by itself, and Docker restarts it.

## 3. Assistant: tool-calling flow

Rendered: [SVG](architecture/assistant-flow.svg) · [PNG](architecture/assistant-flow.png)

```mermaid
sequenceDiagram
    autonumber
    actor U as Reviewer
    participant W as Web server (/api proxy)
    participant A as API: assistant agent
    participant L as LLM provider
    participant T as Tools (plain code + database)

    U->>W: Message (optionally with attached invoices)
    W->>A: POST /assistant/conversations/{id}/messages (X-API-Key, rate-limited)
    A->>L: System prompt + history + tool specs
    loop At most 6 tool rounds
        L-->>A: Tool call, e.g. search_documents, sum_invoices
        A->>T: Run the tool (arguments validated)
        T-->>A: Result with document, invoice and page references
        A->>L: Result wrapped as untrusted data
        A-->>W: SSE: tool_call / tool_result (shown as the tool trace)
    end
    L-->>A: Answer text
    A-->>W: SSE: tokens, citations, done
    W-->>U: Streamed answer with citation chips

    opt The model proposes a change (correct_field, approve_invoice, reprocess_document, run_cross_check)
        A-->>W: SSE: pending_action (summary, expiry)
        W-->>U: Confirmation card: nothing changes yet
        U->>W: Confirm (or Cancel, or let it expire)
        W->>A: POST /assistant/actions/{id}/confirm
        A->>T: Apply through the same audited code as the UI
    end
```

Tools: `search_documents`, `list_documents`, `get_invoice`, `list_review_queue`,
`get_job_status`, `get_stats`, `get_cross_check`, `sum_invoices` and `count_invoices` read.
`correct_field`, `approve_invoice`, `reprocess_document` and `run_cross_check` only
*propose*. Numbers in answers come from tools such as `sum_invoices` (database arithmetic),
not from the model.
