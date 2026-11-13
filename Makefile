# Common commands. Run `make <target>` from the repository root.
COMPOSE := docker compose

.PHONY: up down test lint env logs worker worker-logs ocr-sample extract-sample embed-backfill frontend frontend-dev frontend-check e2e batch-benchmark assistant-eval

# Create .env from the template the first time, so `make up` works out of the box.
env:
	@test -f .env || (cp .env.example .env && echo "Created .env from .env.example")

up: env  ## Build images and start all services in the background
	$(COMPOSE) up -d --build --wait

down:  ## Stop and remove containers (named volumes, i.e. data, are kept)
	$(COMPOSE) down

test: env  ## Run the test suite inside the backend image (Python 3.12, same deps as prod)
	$(COMPOSE) run --rm --build --no-deps api pytest

lint: env  ## Check lint rules and formatting (no changes made)
	$(COMPOSE) run --rm --build --no-deps api sh -c "ruff check . && ruff format --check ."

logs:
	$(COMPOSE) logs -f api worker

worker: env  ## (Re)build and start only the background worker
	$(COMPOSE) up -d --build worker

worker-logs:
	$(COMPOSE) logs -f worker

# Usage: make ocr-sample FILE=samples/scan.jpg [LANGS=eng+ben]
# samples/ is mounted read-only into a one-off container.
ocr-sample: env
	$(COMPOSE) run --rm --no-deps -v "$(CURDIR)/samples:/samples:ro" api \
		python scripts/ocr_sample.py "/samples/$(notdir $(FILE))" --langs "$(or $(LANGS),eng)"

# Usage: make extract-sample FILE=samples/invoice.pdf  (needs LLM_PROVIDER + LLM_API_KEY in .env)
extract-sample: env
	$(COMPOSE) run --rm --no-deps -v "$(CURDIR)/samples:/samples:ro" api \
		python scripts/extract_sample.py "/samples/$(notdir $(FILE))"

# Build semantic-search embeddings for documents processed before step 5 (safe to re-run).
embed-backfill: env
	$(COMPOSE) run --rm api python scripts/embed_backfill.py

frontend: env  ## (Re)build and start only the web UI on http://localhost:3000
	$(COMPOSE) up -d --build frontend

frontend-dev:  ## Next.js dev server with hot reload (needs the API running)
	cd frontend && npm install && npm run dev

frontend-check:  ## Lint, typecheck and unit tests for the frontend
	cd frontend && npm run lint && npm run typecheck && npm test

# Browser smoke test against the running stack; skips itself if the stack is down.
e2e:
	cd frontend && npx playwright install chromium && npm run e2e

# Time the pipeline on a synthetic batch PDF; prints JSON for the evaluation report.
# Usage: make batch-benchmark [PAGES=1000] [ARGS="--embed local" | "--scanned" | "--llm real"]
batch-benchmark: env
	$(COMPOSE) run --rm api python scripts/batch_benchmark.py --pages $(or $(PAGES),1000) $(ARGS)

# Ask the assistant the 15 questions in docs/assistant-eval.md with the REAL provider
# (uses API credits) and report how many had the right number and citation.
assistant-eval: env
	$(COMPOSE) run --rm --build -v "$(CURDIR)/docs:/docs:ro" api python scripts/assistant_eval.py
