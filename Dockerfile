# Build stage: install the locked dependencies with uv.
FROM python:3.12.13-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
COPY packages/runtime/pyproject.toml packages/runtime/pyproject.toml
COPY packages/python_workspace/pyproject.toml packages/python_workspace/pyproject.toml
COPY packages/data_provider/pyproject.toml packages/data_provider/pyproject.toml
COPY packages/provider_fleet/pyproject.toml packages/provider_fleet/pyproject.toml
COPY packages/process_manager/pyproject.toml packages/process_manager/pyproject.toml
RUN uv sync --locked --no-dev --no-install-workspace
COPY app ./app
COPY packages/process_manager ./packages/process_manager
COPY migrations ./migrations
COPY alembic.ini ./
RUN uv sync --locked --no-dev

# Run stage: only the result; no uv, no build tools, no dev packages; never root.
FROM python:3.12.13-slim
WORKDIR /app
COPY --from=build /app /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER 10001
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
