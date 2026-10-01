# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:0.12.1 AS uv

FROM python:3.12-slim AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app/.venv UV_PYTHON_DOWNLOADS=never
WORKDIR /src
# Dependencies first (cached until a lockfile or pyproject changes), then the workspace source.
# Every workspace member's pyproject.toml must be listed here so uv can resolve the workspace.
COPY pyproject.toml uv.lock .python-version ./
COPY libs/shortener-events/pyproject.toml libs/shortener-events/
COPY libs/shortener-testing/pyproject.toml libs/shortener-testing/
COPY api/pyproject.toml api/
COPY tools/keycloak-tools/pyproject.toml tools/keycloak-tools/
RUN uv sync --frozen --no-dev --no-install-workspace --package shortener-api
COPY . .
RUN uv sync --frozen --no-dev --no-editable --package shortener-api

FROM python:3.12-slim
ARG GIT_SHA=dev
ENV PATH=/app/.venv/bin:$PATH SERVICE_VERSION=$GIT_SHA PYTHONUNBUFFERED=1
RUN useradd --uid 10001 --no-create-home app
COPY --from=build /app/.venv /app/.venv
COPY api/alembic.ini /app/alembic.ini
COPY api/alembic /app/alembic
WORKDIR /app
USER 10001
EXPOSE 8000
CMD ["uvicorn", "--factory", "shortener_api.main:create_app_from_env", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
