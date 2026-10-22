FROM python:3.12.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ENV PATH="/opt/craft-python-workspace/.venv/bin:$PATH"
WORKDIR /opt/craft-python-workspace
COPY pyproject.toml uv.lock ./
COPY src ./src
RUN python -m pip install --no-cache-dir uv==0.12.23 \
    && uv sync --locked --no-dev --no-cache \
    && groupadd --gid 10001 workspace \
    && useradd --uid 10001 --gid 10001 --no-create-home workspace

USER 10001:10001
WORKDIR /workspace
CMD ["python", "-m", "craft_workspace.kernel_worker"]
