# URL Shortener Platform

A URL shortener with an API, an HTMX admin UI, an event-driven click pipeline (SQS), Keycloak auth,
and OpenTelemetry. Runs entirely locally; designed to map onto AWS (see the spec).

- Design spec: `docs/superpowers/specs/2026-10-01-url-shortener-design.md`
- Implementation plans: `docs/superpowers/plans/`

## Prerequisites

- Docker with Compose v2 (Docker Desktop, or colima: `colima start --cpu 4 --memory 8`)
- [uv](https://docs.astral.sh/uv/) 0.12+

Integration tests use testcontainers. `make test` / `make check` / `make e2e` (and
`scripts/test.sh <pytest args>`) detect your Docker runtime from the active Docker context
(`scripts/docker-env.sh`) and set what testcontainers needs. colima, for example, also needs
`TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE`. Values you've already exported win. Running plain
`uv run pytest` outside those wrappers? `. scripts/docker-env.sh` first.

## Quickstart

```bash
make sync               # install the workspace
make up                 # start backing services, run migrations, seed Keycloak users
make e2e                # verify the running stack
make token USER=eddie   # print an access token for a seeded user
make down               # stop everything and delete volumes
```

## Local services

| Service | URL | Notes |
|---|---|---|
| Postgres | `localhost:5432` | DBs `shortener`, `keycloak`; credentials in `.env` |
| Keycloak | http://localhost:8080 | Admin console: `KEYCLOAK_ADMIN_USER` / `KEYCLOAK_ADMIN_PASSWORD` from `.env` |
| ElasticMQ (SQS) | http://localhost:9324 | Stats UI: http://localhost:9325 |
| API | http://localhost:8000 | OpenAPI at /docs |
| Click processor | http://localhost:8002/healthz | Consumes click-events → analytics rollups |
| Admin UI | http://localhost:8001 | Sign in as a seeded user (password `password`) |
| Grafana (otel-lgtm) | http://localhost:3000 | OTLP: `localhost:4317` (gRPC), `localhost:4318` (HTTP) |

Port 8080 taken on your machine? Set `KEYCLOAK_HOST_PORT` and `KEYCLOAK_URL` in `.env` (e.g. 8180)
and run `make down && make up`. The token issuer follows that port.

## API

- OpenAPI docs: http://localhost:8000/docs
- Health: `/healthz` (liveness), `/readyz` (database reachable)

```bash
TOKEN=$(make -s token USER=eddie)
curl -s -X POST localhost:8000/api/v1/links -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"target_url": "https://example.com"}'
curl -si localhost:8000/<code>        # 302 → target; a link.clicked event goes to SQS
```

The click-processor rolls clicks into `analytics.*` within a second or two; `GET /api/v1/links/{id}/stats` shows them. Stop it (`docker compose stop click-processor`) and clicks queue up in `click-events` (http://localhost:9325); start it again and the backlog drains.

## Admin UI

- Sign in at http://localhost:8001 as `alice` (admin), `eddie`/`erin` (editor), `victor` (viewer) or `nora` (no access).
- Every action goes through the API with your own token, so what you can do is exactly what the API allows.
- Sessions are server-side in `admin.sessions`; the browser only holds an opaque `sid`.

## Observability

- **Grafana** at http://localhost:3000 → **Dashboards → Shortener → Shortener Overview**: redirects, latency, 5xx by service, and the click pipeline (published, dropped, processed, lag, queue and DLQ depth).
- **Traces** (Explore → Tempo):
  - one admin action is one trace across admin → API → Postgres
  - a click's processor batch span **links** to the redirect that produced it
  - error pages show a **Reference** (trace id); paste it into Tempo's trace search
- **Logs:** every service writes JSON lines with `trace_id`/`span_id` to stdout (`docker compose logs api`) and to Loki (Explore → Loki, e.g. `{service_name="shortener-api"} | trace_id="…"`).
- **Dashboard source:** `scripts/gen-dashboard` (dashboard-as-code). Run it after editing, and `make check` fails if the JSON is stale.

## Seeded users (DEV ONLY, password `password`)

| User | Role |
|---|---|
| alice | admin |
| eddie, erin | editor |
| victor | viewer |
| nora | (none) |

Edit `infra/keycloak/users.yaml` and run `make seed-users` (inside compose) or `scripts/seed-users`
(from the host) to add users or change roles. Seeding is idempotent and never removes roles it
doesn't manage. Passwords are set on creation only (`RESET_PASSWORDS=true make seed-users` to force).

## Development

```bash
make check    # ruff, mypy --strict, tests with coverage gates
make fmt      # auto-format and fix lint
```

- Migrations live in `api/alembic` and run only via `make migrate` (the `migrate` release job).
- Executable Python scripts live in `scripts/` and use a uv shebang (`#!/usr/bin/env -S uv run --quiet --script`)
  with a PEP 723 header, so they run from any directory with only uv installed. Library modules and
  container entrypoints use `python -m` instead.
- `scripts/gen-event-schema` regenerates the committed `link.clicked` JSON schema.

## Troubleshooting

- **Realm changes not applied:** Keycloak imports the realm only when it doesn't exist. Run `make down && make up`.
- **Changed a password or the Keycloak admin in `.env`:** Postgres roles and the Keycloak bootstrap admin are created once per data volume. Run `make down && make up` to recreate them.
- **`Account is not fully set up` on login:** the user is missing email/first/last name in `users.yaml`.
- **Testcontainers can't find Docker:** check the `docker-env:` line printed by `make test`; it names the runtime and socket it detected (`docker context ls` shows the active context).
