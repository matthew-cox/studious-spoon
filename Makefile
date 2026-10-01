SHELL := /bin/bash
COMPOSE := docker compose
GIT_SHA ?= $(shell git rev-parse --short HEAD 2>/dev/null || echo dev)
export GIT_SHA

# Pure business-logic modules: >= 90% branch coverage (spec §15.2). Later plans append to this list.
PURE_MODULES := */shortener_events/*,*/keycloak_tools/users.py,*/keycloak_tools/plan.py,*/shortener_api/policy.py,*/shortener_api/codes.py,*/shortener_api/urls.py,*/shortener_api/stats.py,*/shortener_processor/referrers.py,*/shortener_processor/aggregate.py,*/shortener_admin/security.py
MYPY_TARGETS := libs/shortener-events/src api/src tools/keycloak-tools/src processor/src admin/src

# `make token USER=eddie`; USER is also a shell env var, so only honor it from the command line.
TOKEN_USER = $(if $(filter command line,$(origin USER)),$(USER),alice)

.PHONY: sync lint fmt typecheck test check up down logs migrate seed-users token e2e

sync:
	uv sync --all-packages --frozen

lint:
	uv run ruff check .
	uv run ruff format --check .

fmt:
	uv run ruff format .
	uv run ruff check --fix .

typecheck:
	uv run mypy $(MYPY_TARGETS)

test:
	uv run pytest --cov --cov-report=term-missing --cov-report=xml --cov-fail-under=80
	uv run coverage report --include='$(PURE_MODULES)' --fail-under=90

check: lint typecheck test

.env:
	cp .env.example .env

up: .env
	$(COMPOSE) up -d --build --wait postgres elasticmq keycloak otel-lgtm
	$(COMPOSE) run --rm --build migrate
	$(COMPOSE) run --rm --build keycloak-seed
	$(COMPOSE) up -d --build --wait api click-processor

down:
	$(COMPOSE) down -v

logs:
	$(COMPOSE) logs -f

migrate: .env
	$(COMPOSE) run --rm --build migrate

seed-users: .env
	$(COMPOSE) run --rm --build keycloak-seed

token:
	@scripts/token $(TOKEN_USER)

e2e:
	uv run pytest -m e2e tests/e2e -v
