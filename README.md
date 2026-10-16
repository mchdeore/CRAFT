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

## Documents

[Document downloads](https://mchdeore.github.io/CRAFT/) | [Specification](docs/spec/) | [API Contract](docs/API-Contract.md) | [Dependencies and Integration](docs/Dependencies-and-Integration.md)

## Contributing

Fork the repository, create a branch, run the checks in `.github/workflows/ci.yml`, and open a pull request. Only the maintainer merges.

## Kept locally

`CRAFT-Data-Lake-Import-2026-10-14.zip`: data lake import from the president's document drive, messy documents from students and peers with example data for the database, and sample data for use-case demos; original external data documents as received on 14 October 2026.
