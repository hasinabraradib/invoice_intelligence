# Common commands. Run `make <target>` from the repository root.
COMPOSE := docker compose

.PHONY: up down test lint env logs ocr-sample extract-sample

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

# Usage: make ocr-sample FILE=samples/scan.jpg [LANGS=eng+ben]
# samples/ is mounted read-only into a one-off container.
ocr-sample: env
	$(COMPOSE) run --rm --no-deps -v "$(CURDIR)/samples:/samples:ro" api \
		python scripts/ocr_sample.py "/samples/$(notdir $(FILE))" --langs "$(or $(LANGS),eng)"

# Usage: make extract-sample FILE=samples/invoice.pdf  (needs LLM_PROVIDER + LLM_API_KEY in .env)
extract-sample: env
	$(COMPOSE) run --rm --no-deps -v "$(CURDIR)/samples:/samples:ro" api \
		python scripts/extract_sample.py "/samples/$(notdir $(FILE))"
