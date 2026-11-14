# Common commands. Run `make <target>` from the repository root.
COMPOSE := docker compose

.PHONY: up down demo test test-report prompts-doc lint env logs worker worker-logs ocr-sample extract-sample capture-fixtures eval-dataset eval hard-dev-bench embed-backfill frontend frontend-dev frontend-check e2e batch-benchmark assistant-eval

# Create .env from the template the first time, so `make up` works out of the box.
# It also adds a random API key (API_KEYS) if .env has none. The key stays in .env and in
# the frontend *server*; the browser never sees it.
env:
	@test -f .env || (cp .env.example .env && echo "Created .env from .env.example")
	@grep -q '^API_KEYS=..*' .env || (printf '\nAPI_KEYS=%s\n' "$$(openssl rand -hex 32)" >> .env && echo "Added a random API key (API_KEYS) to .env")

up: env  ## Build images and start all services in the background
	$(COMPOSE) up -d --build --wait

down:  ## Stop and remove containers (named volumes, i.e. data, are kept)
	$(COMPOSE) down

test: env  ## Run the test suite inside the backend image (Python 3.12, same deps as prod)
	$(COMPOSE) run --rm --build --no-deps api pytest -p no:cacheprovider

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

# Record sanitised real-provider replies (synthetic text only) as test fixtures.
# Uses API credits; needs LLM_PROVIDER and its key in .env.
capture-fixtures: env
	$(COMPOSE) run --rm --build --no-deps -v "$(CURDIR)/backend/tests/fixtures:/fixtures" api \
		python scripts/capture_llm_fixtures.py --out /fixtures/llm

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

# Demo data: generate the synthetic dataset (fixed seed) and upload a varied set of it
# through the API, then cross-check the batch PDF against its ledger. Needs `make up`.
demo: eval-dataset
	$(COMPOSE) run --rm --no-deps -v "$(CURDIR)/eval/dataset/files:/demo:ro" api \
		python scripts/seed_demo.py /demo

# Rewrite the "Runtime prompts" section of docs/prompts.md from the code.
prompts-doc: env
	$(COMPOSE) run --rm --build --no-deps -v "$(CURDIR)/docs:/docs" api \
		python scripts/export_prompts.py /docs/prompts.md

# All three suites with coverage and JUnit output into docs/test-results/, then a summary
# in docs/test-results.md. Needs the stack running (`make up`) for the e2e test.
RESULTS := $(CURDIR)/docs/test-results
test-report: env
	mkdir -p $(RESULTS)
	$(COMPOSE) run --rm --build --no-deps -v "$(RESULTS):/results" api \
		pytest -p no:cacheprovider -m "" --cov=app --cov-report=xml:/results/coverage.xml \
		--cov-report=term --junitxml=/results/backend-junit.xml --durations=15
	cd frontend && npx vitest run --coverage --coverage.provider=v8 \
		--coverage.reporter=json-summary --coverage.reporter=text-summary \
		--coverage.reportsDirectory=$(RESULTS)/frontend-coverage \
		--reporter=default --reporter=junit --outputFile.junit=$(RESULTS)/frontend-junit.xml
	cd frontend && npx playwright install chromium && \
		PLAYWRIGHT_JUNIT_OUTPUT_FILE=$(RESULTS)/e2e-junit.xml npx playwright test --reporter=list,junit
	$(COMPOSE) run --rm --no-deps -v "$(CURDIR)/docs:/docs" \
		-e GIT_COMMIT="$$(git rev-parse --short HEAD)" -e GIT_DATE="$$(date -u '+%Y-%m-%d %H:%M UTC')" \
		api python scripts/test_summary.py /docs/test-results /docs/test-results.md

# Time the pipeline on a synthetic batch PDF; prints JSON for the evaluation report.
# Usage: make batch-benchmark [PAGES=1000] [ARGS="--embed local" | "--scanned" | "--llm real"]
batch-benchmark: env
	$(COMPOSE) run --rm api python scripts/batch_benchmark.py --pages $(or $(PAGES),1000) $(ARGS)

# Ask the assistant the 15 questions in docs/assistant-eval.md with the REAL provider
# (uses API credits) and report how many had the right number and citation.
assistant-eval: env
	$(COMPOSE) run --rm --build -v "$(CURDIR)/docs:/docs:ro" api python scripts/assistant_eval.py

# Regenerate the synthetic labelled dataset (fixed seed, byte-identical) in eval/dataset/.
eval-dataset: env
	$(COMPOSE) run --rm --build --no-deps -v "$(CURDIR)/eval:/eval" api \
		python /eval/dataset/generate.py

# Full evaluation with the REAL provider (uses API credits), in an isolated stack with its
# own database (compose project invoice-eval), then the headline goes to your dev database
# for the dashboard. Writes eval/results/<timestamp>.json and docs/evaluation.md.
# Usage: make eval [ARGS="--skip-assistant" | "--real"]
EVAL_COMPOSE := $(COMPOSE) -p invoice-eval -f docker-compose.yml -f eval/docker-compose.eval.yml
eval: eval-dataset
	-docker volume rm -f invoice-eval_pgdata invoice-eval_uploads > /dev/null 2>&1
	$(EVAL_COMPOSE) up -d --build --wait api worker
	$(EVAL_COMPOSE) run --rm --no-deps -v "$(CURDIR)/eval:/eval" -v "$(CURDIR)/docs:/docs" \
		api python /eval/run_eval.py --api http://api:8000 $(ARGS); \
		status=$$?; $(EVAL_COMPOSE) down; exit $$status
	$(COMPOSE) run --rm -v "$(CURDIR)/eval/results:/results:ro" api \
		python scripts/store_eval_run.py /results

# Compare OCR preprocessing choices on the HARD DEV set only (real LLM; a few cents).
# Usage: make hard-dev-bench [CONFIGS=base,flatten,...] [ARGS=--second-opinion]
hard-dev-bench: eval-dataset
	$(COMPOSE) run --rm --build --no-deps -v "$(CURDIR)/eval:/eval" api \
		python /eval/hard_dev_bench.py $(if $(CONFIGS),--configs $(CONFIGS)) $(ARGS) --out /eval/results/hard-dev-bench.json
