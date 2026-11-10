# Invoice Intelligence

## Overview

_To be written._

## Architecture

_To be written._

## Quick start

Requirements: Docker (with Docker Compose v2) and `make`.

```bash
# 1. Create your local config (only placeholders, edit if needed)
cp .env.example .env

# 2. Build and start everything: api, worker, postgres (pgvector), redis
make up

# 3. Check that the app, database and Redis are all reachable
curl -s http://localhost:8000/health
# -> {"status":"ok","app":{...},"database":{"status":"ok",...},"redis":{"status":"ok",...}}

# Interactive API docs: http://localhost:8000/docs

# 4. Run tests and lint (inside the backend container, Python 3.12)
make test
make lint

# 5. Stop everything (your database data is kept in a Docker volume)
make down
```

`/health` returns HTTP 200 when everything is reachable and HTTP 503 (`"status": "degraded"`)
when Postgres or Redis is down.

## API

_To be written._

## Tests

_To be written._

## Evaluation

_To be written._

## Design decisions

_To be written._

## Limitations

_To be written._

## AI usage

See [AI_USAGE.md](AI_USAGE.md).
