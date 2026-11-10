# Common commands. Run `make <target>` from the repository root.
COMPOSE := docker compose

.PHONY: up down test lint env logs

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
