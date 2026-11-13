# Database schema

PostgreSQL 17 with the `pgvector` extension (`pgvector/pgvector:pg17` image). Migrations live
in `backend/alembic/versions/`; the models are in `backend/app/models.py`.

```mermaid
erDiagram
    documents ||--o{ processing_jobs : "is processed by"
    documents ||--o| invoices : "yields"
    documents ||--o{ page_texts : "has"
    documents ||--o{ text_chunks : "is split into"
    invoices ||--o{ line_items : "contains"
    invoices ||--o{ validation_issues : "has"
    invoices ||--o{ corrections : "is corrected by"

    documents {
        uuid id PK
        string filename
        string sha256 UK
        string mime_type
        bigint size_bytes
        int page_count
        text storage_path
        string status "uploaded|processing|processed|needs_review|failed"
        text error
        timestamptz created_at
        timestamptz updated_at
    }
    processing_jobs {
        uuid id PK
        uuid document_id FK
        string status "queued|running|succeeded|failed"
        string stage "parsing|ocr|extracting|validating|saving"
        int attempts
        text error
        timestamptz started_at
        timestamptz finished_at
    }
    invoices {
        uuid id PK
        uuid document_id FK,UK
        string invoice_number
        date invoice_date
        date due_date
        text supplier_name
        string supplier_key
        text customer_name
        string currency "ISO 4217"
        numeric subtotal "18,2"
        numeric tax "18,2"
        numeric total "18,2"
        bool needs_review
        float overall_confidence
        jsonb fields "status, evidence, page, confidence, reasons"
        jsonb raw_model_output
        timestamptz extracted_at
    }
    line_items {
        uuid id PK
        uuid invoice_id FK
        int position
        text description
        numeric quantity "18,4"
        numeric unit_price "18,4"
        numeric line_total "18,2"
        jsonb fields
    }
    validation_issues {
        uuid id PK
        uuid invoice_id FK
        string code
        string severity "error|warning"
        string field
        text message
        text expected
        text actual
    }
    corrections {
        uuid id PK
        uuid invoice_id FK
        string field
        text old_value
        text new_value
        string corrected_by
        timestamptz corrected_at
    }
    page_texts {
        uuid id PK
        uuid document_id FK
        int page_number
        string source "text_layer|ocr|sheet"
        text text
        float ocr_confidence
        tsvector search "generated, GIN index"
    }
    text_chunks {
        uuid id PK
        uuid document_id FK
        int page_number
        int chunk_index
        text text
        vector embedding "384, HNSW cosine index"
    }
```

## Why each table exists

| Table | Why |
|---|---|
| `documents` | One row per uploaded file. The unique `sha256` means the same bytes are stored only once. `status` is what the UI shows. |
| `processing_jobs` | One row per processing run, so you can see how many attempts there were and which `stage` failed, without overwriting history. |
| `invoices` | The extracted invoice, at most one per document. Re-processing updates the same row, so `corrections` stay attached. Money is `NUMERIC`, never float. |
| `line_items` | Invoice rows in printed order (`position`). They're replaced on every run, which keeps re-processing idempotent. |
| `validation_issues` | What the rules flagged (errors and warnings) and why: the review queue reads this. |
| `corrections` | Audit trail of human edits (who changed what, from what, and when). Never deleted on re-processing. |
| `page_texts` | Text of every page or sheet, for keyword search (generated `tsvector` with a GIN index). |
| `text_chunks` | Pieces of about 500 tokens from one page each (50-token overlap) with their 384-number embedding, for semantic search. An HNSW index makes nearest-neighbour lookups fast. They're replaced on every run (idempotent), and chunks never cross pages, so every hit has a page number. |

Indexes: `invoices(supplier_key, invoice_number_key)` and `invoices(supplier_key, invoice_date, total)`
for the duplicate checks, and `documents(status)` for listing by status. Foreign keys cascade
on delete, so deleting a document removes everything derived from it.
