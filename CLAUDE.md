# Project rules (Invoice Intelligence)

These rules apply to every change, by humans or AI assistants.

1. **Money is `Decimal`, never `float`.** Parse amounts with `decimal.Decimal`, store them as
   `NUMERIC` in PostgreSQL, and serialise them as strings in JSON.
2. **The LLM only extracts and quotes.** It returns values copied from the document, with the
   source text it quoted. Plain, tested code does parsing (dates, numbers, currencies),
   arithmetic (totals, tax) and validation. Never ask the LLM to compute or "fix" a value.
3. **Every extracted field may be null, and carries a status** (for example `found`, `missing`,
   `invalid`). Never invent a value to fill a gap.
4. **Jobs are idempotent.** Running the same job twice must give the same result and no duplicate
   rows (use content hashes and upserts).
5. **Never log full document text.** Log IDs, sizes, hashes and short, truncated snippets only.
6. **Never trust the file extension or the client's Content-Type.** Detect the type from magic
   bytes and reject anything not on the allow-list.
7. **Keep commits small.** One logical change per commit, with tests in the same commit.

## Layout
- `backend/`: FastAPI app (`app/`), tests (`tests/`), migrations (`alembic/`)
- `frontend/`: Next.js UI (not started yet)
- `make up | down | test | lint`: the main commands
