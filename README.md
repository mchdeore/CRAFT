# CRAFT

CRAFT answers questions over project documents with cited sources.
It also maintains an engineering datacube.

## Run it

Use Docker with the Compose plugin. Copy the environment file and set the three local passwords before starting.

```bash
cp .env.example .env
docker compose up --build --wait
curl -i localhost:8000/health/ready
```

The ready response is HTTP 200 with an `X-Request-Id` header and `{"status":"ok"}`. If port 5432 is occupied, set `CRAFT_DATABASE_PORT` to a free port in `.env`. Stop this stack with `docker compose down` before starting the development server.

## Develop

Install [uv](https://docs.astral.sh/uv/) 0.12.23. Use the same environment file and passwords.

```bash
uv sync --locked
docker compose up -d db
uv run alembic upgrade head
uv run pytest
uv run uvicorn app.main:app --reload
```

The development server listens on port 8000. Check readiness with `curl -i localhost:8000/health/ready`. Stop the server with Ctrl-C and the database with `docker compose down`.

The tracked React/TypeScript/Vite client in `web/` uses Node.js 24 and shared types from `api/types.ts`. Follow the [frontend setup and checks](web/README.md) to install both npm dependency trees and connect its local proxy to the authenticated backend. Serve the production client and backend from the same origin.

## Documents

The [public wiki](https://mchdeore.github.io/CRAFT/) has topic guides, the specification and API reference. Every topic includes a Markdown download and a GitHub edit link. The public site also includes a simulated preview; the application runs separately with authentication.

[Topic index](docs/index.md) · [API contract](docs/API-Contract.md) · [Dependencies](docs/Dependencies-and-Integration.md)

Build the wiki and PDF downloads with `docs/tools/build.sh`.

## Contributing

Fork the repository, create a branch, run the checks in `.github/workflows/ci.yml` and `.github/workflows/frontend.yml`, and open a pull request. Open it as a draft while work is in progress for quick checks only; mark it ready for review to run the full checks. Only the maintainer merges.

