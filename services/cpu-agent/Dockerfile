FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

COPY --from=ghcr.io/astral-sh/uv:0.4 /uv /usr/local/bin/uv

WORKDIR /app

# workspace layout: contracts is a path dep, so we need both
COPY services/contracts/    /src/services/contracts/
COPY services/cpu-agent/    /src/services/cpu-agent/
COPY pyproject.toml         /src/pyproject.toml

WORKDIR /src
RUN uv sync --package podmind-cpu-agent --no-dev

ENV PATH="/src/.venv/bin:$PATH"

WORKDIR /src/services/cpu-agent

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
