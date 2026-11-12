# AI usage log

How AI tools were used to build this project. Each row is one step.

| Date | Step | Tool | Prompt summary | What I changed by hand |
|------|------|------|----------------|------------------------|
| 2026-11-11 | 1: Project skeleton | Claude Code (Claude Opus 5.5) | Create the repo skeleton: FastAPI app with settings and a real `/health` check (Postgres + Redis), pytest, async Alembic, Docker Compose (api, worker placeholder, pgvector Postgres, Redis with healthchecks), Dockerfile, `.env.example`, `.gitignore`, Makefile, CLAUDE.md, README skeleton. Build, run, test, lint, first commit. | _To fill in after review_ |
| 2026-11-12 | 2: Safe uploads + parsing | Claude Code (Claude Opus 5.5) | Magic-byte file detection, streaming upload storage (size cap, SHA-256, UUID names), per-page PDF text extraction with OCR flags, xlsx/xls/csv parsing with header detection into one ParsedDocument model, POST /documents with per-file errors, tests with in-code fixtures, samples folder. | _To fill in after review_ |
| 2026-11-12 | 3a: OCR | Claude Code (Claude Opus 5.5) | Tesseract (eng+ben) in Docker; OpenCV preprocessing (grayscale, upscale, denoise, deskew, adaptive binarize); OCR engine with word-level confidence and PSM 6/4 fallback; page-by-page OCR pipeline for scanned PDF pages and images wired into parse_document; generated-image tests; ocr_sample script. | _To fill in after review_ |
