# URL Shortener Platform — Design Spec

- **Date:** 2026-10-01
- **Status:** Draft — pending review
- **Context:** Technical interview exercise. Must run entirely locally (Docker / colima) and be designed for a later AWS deployment via Terraform.

## 1. Goals and Non-Goals

### Goals
- A URL shortening service with:
  - a JSON **API** that also serves public redirects
  - an **admin UI**
  - a **Postgres** database
  - **Keycloak** for authentication and role-based authorization
  - an **OpenTelemetry** pipeline for metrics, logs, and traces
- A single `docker compose up` (via `make up`) brings up the entire stack, including a Keycloak realm with seeded test users and roles.
- Click analytics stored as business data and surfaced in the admin UI.
- Admins can block a link for abuse, and the owner cannot override the block.
- Every component maps cleanly to an AWS equivalent (see §10).
- The code is clear, tested, and explainable; this is valued over raw scale.

### Non-Goals (this iteration)
- Custom aliases, link expiration, Redis caching, teams/orgs, embedded Grafana panels, audit log, domain blocklist, block appeals. All are captured in §11 (Future Work), and the data model is designed so they can be added without a rewrite.
- Terraform / AWS deployment itself (a later discussion; this design only keeps the path open).
- Geo-IP enrichment of clicks; storing client IPs.

## 2. Key Decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | **Python + FastAPI** for both services | Author's strongest stack; free OpenAPI docs; mature OTEL and OIDC libraries. |
| D2 | **Admin UI = FastAPI + Jinja2 + HTMX, as a separate service** (not a React SPA, not pages inside the API) | Author has HTMX experience and is weak on React. Running it as its own service keeps the UI/API separation clean: the API is the single source of truth and enforces all authorization, and the admin UI calls it with the user's own token (token relay). Tokens never reach the browser. |
| D3 | **Postgres 16 + SQLAlchemy 2.x (async) + Alembic** | Conventional; maps directly to RDS. |
| D4 | **Clicks are stored in Postgres (`click_events`); OTEL metrics are for operating the service only** | Clicks are business data: they must be exact, durable, queryable per link, and filtered by the caller's permissions. Prometheus is the wrong store for that: a per-link `short_code` label would cause a cardinality explosion, and metrics are aggregated and age out. |
| D5 | **`grafana/otel-lgtm` all-in-one** for local observability | One container provides the collector, Prometheus, Loki, Tempo, and Grafana. Lighter footprint for local runs. |
| D6 | **Owner on/off switch (`is_active`) and admin abuse block (`blocked_*`) stored separately** | See §4.3. A single flag would let the owner re-enable a blocked link, or point it at a "clean" URL to get it unblocked. Storing them independently means unblocking restores whatever `is_active` was before. |
| D7 | **Admin UI sessions stored server-side in Postgres**, in a separate `admin` schema with its own DB user | Keycloak tokens are too large to fit safely in a cookie (~4 KB limit). An in-memory store breaks once more than one instance runs (ECS). Redis is out of scope. The admin service never touches link data; DB grants enforce this. |
| D8 | **`302` redirects (not `301`)** | Browsers cache `301`, which would skip the server and lose click counts. |
| D9 | **A non-owner gets `404`, not `403`** | Avoids revealing that a link exists. |
| D10 | **Keycloak realm roles** `admin`, `editor`, `viewer`; login required to create links; redirects are public | Matches the stated requirements. Keycloak groups are where orgs/teams would hook in later. |

## 3. Architecture

### 3.1 Services (docker compose)

| Service | Image / build | Port | Purpose |
|---|---|---|---|
| `api` | built from `api/` | 8000 | JSON API (`/api/v1/...`) + public redirects `GET /{code}` |
| `admin` | built from `admin/` | 8001 | Admin UI. Keycloak confidential client; calls `api` using the user's access token. |
| `postgres` | `postgres:16` | 5432 | Databases: `shortener` (schemas `public` for link data and `admin` for sessions) and `keycloak` |
| `keycloak` | `quay.io/keycloak/keycloak` (pinned version) | 8080 | Starts with `--import-realm` |
| `otel-lgtm` | `grafana/otel-lgtm` (pinned version) | 3000 (Grafana), 4317 (OTLP gRPC), 4318 (OTLP HTTP) | Collector + Prometheus + Loki + Tempo + Grafana |
| `migrate` | `api` image, runs `alembic upgrade head` | — | Runs once before startup. `api` and `admin` depend on it with `condition: service_completed_successfully`. |

All services have healthchecks. Startup order is enforced with `depends_on: condition: service_healthy`.

### 3.2 Repository Layout

```
api/
  pyproject.toml
  Dockerfile
  alembic.ini, alembic/
  src/shortener_api/
    main.py            app factory, middleware, routers
    config.py          pydantic-settings
    db.py              engine/session
    models.py          SQLAlchemy models
    schemas.py         Pydantic request/response models
    auth.py            JWT validation, Principal, require_roles
    policy.py          can(principal, action, link): pure function
    codes.py           short-code generator + reserved words
    urls.py            target URL validation
    routes/redirect.py, routes/links.py, routes/stats.py, routes/health.py
    clicks.py          background click recording
    telemetry.py       OTEL setup + custom metrics
    errors.py          problem+json handlers
  tests/unit/, tests/integration/
admin/
  pyproject.toml
  Dockerfile
  src/shortener_admin/
    main.py, config.py
    oidc.py            Authlib login/callback/logout/refresh
    sessions.py        Postgres-backed session store (admin schema)
    api_client.py      httpx wrapper with token relay
    csrf.py
    routes/            dashboard, links, link_detail
    telemetry.py
  templates/           full pages + partials/ for HTMX fragments
  static/              pico.css, chart.js, htmx.js (vendored)
  tests/
infra/
  keycloak/realm-export.json   realm, clients, roles
  keycloak/users.yaml          seeded users + role assignments
  keycloak/seed_users.py       idempotent seeding via Keycloak admin REST API
  otel/dashboards/             provisioned Grafana dashboard(s)
  postgres/init.sql            creates keycloak DB, admin schema, DB users/grants
tests/e2e/                     stack-level smoke tests (make e2e)
docs/
docker-compose.yml
Makefile
.env.example
README.md
.github/workflows/ci.yml
```

A `terraform/` directory is reserved for later.

**Tooling:** `uv` (dependencies), `ruff` (lint/format), `pytest`, Makefile targets: `up`, `down`, `logs`, `test`, `lint`, `migrate`, `seed-users`, `e2e`, `token USER=<name>`.

## 4. Data Model

### 4.1 `links`

| Column | Type | Notes |
|---|---|---|
| `id` | `uuid` PK | |
| `code` | `varchar(32)` UNIQUE NOT NULL | 7-character base62 code from `secrets`. Wide enough for future custom aliases. |
| `target_url` | `text` NOT NULL | Validated per §4.4 |
| `owner_sub` | `varchar(64)` NOT NULL | Keycloak `sub` (user ID). Indexed. |
| `owner_username` | `varchar(255)` NOT NULL | Copied from the token for display |
| `is_active` | `boolean` NOT NULL DEFAULT true | The owner's on/off switch |
| `blocked_at` | `timestamptz` NULL | **Non-null means blocked.** Single source of truth for block state. |
| `blocked_by` | `varchar(64)` NULL | `sub` of the admin who blocked it |
| `blocked_reason` | `text` NULL | Required when blocking; visible to the owner |
| `created_at`, `updated_at` | `timestamptz` NOT NULL | |

Check constraint: `blocked_at IS NULL OR (blocked_by IS NOT NULL AND blocked_reason IS NOT NULL)`.

**Derived status** (for display and filtering): `blocked` if `blocked_at` is set, otherwise `active` / `disabled` from `is_active`.

### 4.2 `click_events`

| Column | Type | Notes |
|---|---|---|
| `id` | `bigserial` PK | |
| `link_id` | `uuid` FK → `links.id` ON DELETE CASCADE | |
| `clicked_at` | `timestamptz` NOT NULL DEFAULT now() | |
| `referrer` | `text` NULL | Truncated to 1024 chars |
| `user_agent` | `text` NULL | Truncated to 512 chars |

Index: `(link_id, clicked_at)`.

### 4.3 Administrative Block (abuse handling)

The owner's switch and the admin block are deliberately separate (D6):

- **Redirect:** a blocked link always returns **`410 Gone`** with a small HTML "This link has been disabled" page, whatever `is_active` says. **No click event is recorded**, but `shortener.redirects{result="blocked"}` is incremented.
- **Owner restrictions while blocked:**
  - `PATCH` (any field) → `409 Conflict`; the problem detail includes the block reason.
  - `DELETE` → `409 Conflict`, so the record is kept as evidence.
- **Admins** can block, unblock, edit, and delete any link, blocked or not.
- **Unblocking** clears all three `blocked_*` columns. The link goes back to its previous `is_active` value.
- **Admin UI:** a "Blocked" badge and the reason are shown to everyone who can see the link. Admins get block/unblock controls; blocking requires entering a reason.

### 4.4 Validation Rules
- `target_url`: scheme must be `http` or `https`; max 2048 chars; must have a host; must not point at the shortener's own public host (`PUBLIC_BASE_URL`), which prevents redirect loops.
- Code generation: 7 chars from `[A-Za-z0-9]` via `secrets.choice`. Generated codes that match the reserved-word list (case-insensitive) are skipped. Reserved words: `api`, `admin`, `docs`, `redoc`, `openapi`, `healthz`, `readyz`, `static`, `favicon`, `robots`. On a unique-constraint collision, retry up to 5 times, then return `500` and log.

### 4.5 Admin Session Table (`admin.sessions`)

| Column | Type | Notes |
|---|---|---|
| `id` | `varchar(64)` PK | Random opaque ID (`secrets.token_urlsafe`), stored in the cookie |
| `sub`, `username` | varchar | |
| `roles` | `text[]` | From the access token, for UI display only |
| `access_token`, `refresh_token`, `id_token` | `text` | |
| `access_expires_at`, `refresh_expires_at` | `timestamptz` | |
| `csrf_token` | `varchar(64)` | |
| `created_at`, `last_seen_at` | `timestamptz` | |

Expired sessions are deleted lazily on access, plus an opportunistic cleanup on login. The `admin` DB user has privileges only on the `admin` schema. The `api` DB user has no privileges on it.

## 5. API

Base path for JSON endpoints: `/api/v1`. Errors are `application/problem+json` (§8). OpenAPI docs are at `/docs`.

| Method & Path | Roles | Behavior |
|---|---|---|
| `GET /{code}` | public | Active and not blocked → `302` to the target, and a click is recorded as a background task. Unknown or inactive → `404`. Blocked → `410` HTML page. |
| `GET /healthz` | public | Liveness check |
| `GET /readyz` | public | Readiness check: tests the DB connection |
| `GET /api/v1/me` | any role | `{sub, username, roles}` |
| `POST /api/v1/links` | editor, admin | Body `{target_url}` → `201` with the link, including `short_url` |
| `GET /api/v1/links` | viewer, editor, admin | Query: `q` (matches code or target), `status` (`active\|disabled\|blocked`), `owner` (admin/viewer only), `page`, `page_size` (max 100). Editors only get their own links. |
| `GET /api/v1/links/{id}` | viewer, editor (own), admin | Editors get `404` for links they don't own |
| `PATCH /api/v1/links/{id}` | editor (own), admin | Body `{target_url?, is_active?}`. Owner on a blocked link → `409`. |
| `DELETE /api/v1/links/{id}` | editor (own), admin | `204`. Owner on a blocked link → `409`. |
| `POST /api/v1/links/{id}/block` | admin | Body `{reason}` (required, 1–1000 chars) → `200`. Already blocked → `409`. |
| `POST /api/v1/links/{id}/unblock` | admin | `200`. Not blocked → `409`. |
| `GET /api/v1/links/{id}/stats` | viewer, editor (own), admin | Query `from`, `to` (default: last 7 days), `bucket=hour\|day`. Returns `{total, series:[{ts,count}], top_referrers:[{referrer,count}]}` (top 10). |

### 5.1 Permission Policy

Implemented as a pure function `can(principal, action, link) -> bool` in `policy.py`. Routes call it; nothing else makes authorization decisions.

| Action | admin | editor (own) | editor (other's) | viewer | no role |
|---|---|---|---|---|---|
| list / read / stats | ✅ | ✅ | ❌ (404) | ✅ | ❌ (403) |
| create | ✅ | ✅ | — | ❌ (403) | ❌ (403) |
| update / delete (not blocked) | ✅ | ✅ | ❌ (404) | ❌ (403) | ❌ (403) |
| update / delete (blocked) | ✅ | ❌ (409) | ❌ (404) | ❌ (403) | ❌ (403) |
| block / unblock | ✅ | ❌ (403) | ❌ (404) | ❌ (403) | ❌ (403) |

Missing or invalid token → `401`.

## 6. Authentication & Keycloak

### 6.1 Realm `shortener`
- Realm roles: `admin`, `editor`, `viewer`, emitted in the access token under `realm_access.roles`.
- Clients:

| Client | Type | Config |
|---|---|---|
| `shortener-admin` | confidential, standard login flow, PKCE S256 | Redirect URI `http://localhost:8001/auth/callback`. Post-logout redirect `http://localhost:8001/`. An audience mapper adds `shortener-api` to `aud`. |
| `shortener-api` | resource server (bearer-only, no flows) | Exists only as a token audience |
| `shortener-dev` | public client, direct username/password login enabled | **Local/dev only**, for curl, `make token`, and e2e tests. Audience mapper adds `shortener-api`. Must not exist in non-local realms. |

### 6.2 Token Validation (API)
- Uses PyJWT (or `joserfc`) with a JWKS cache fetched from `OIDC_INTERNAL_URL`. When a token arrives with an unknown `kid`, the cache is refreshed (rate-limited).
- Checks the signature (RS256), `iss == OIDC_ISSUER`, `aud` contains `shortener-api`, `exp`/`nbf` (with 30 s leeway).
- Produces `Principal(sub, username, roles)`. FastAPI dependencies `current_principal` and `require_roles(*roles)`.

### 6.3 Issuer / Hostname Handling
The browser reaches Keycloak at `http://localhost:8080`, but containers reach it at `http://keycloak:8080`. Keycloak runs with `KC_HOSTNAME=http://localhost:8080` (with backchannel dynamic) so the token issuer is always the same. Both services take two settings:
- `OIDC_ISSUER=http://localhost:8080/realms/shortener`: used to check `iss`, and for the browser-facing redirects.
- `OIDC_INTERNAL_URL=http://keycloak:8080/realms/shortener`: used for JWKS, token exchange, and refresh.

### 6.4 Admin UI Login Flow
1. An unauthenticated request redirects to `/auth/login`. Authlib builds the authorization request (state, nonce, PKCE).
2. `/auth/callback` exchanges the code for tokens over the internal URL, creates an `admin.sessions` row, and sets cookie `sid` (`HttpOnly`, `SameSite=Lax`, `Secure` when not local, path `/`).
3. Each request loads the session. If the access token expires within 30 s, it is refreshed. If the refresh fails, the session is deleted and the user is redirected to login with a "session expired" message.
4. `/auth/logout` deletes the session and redirects to Keycloak's end-session endpoint with `id_token_hint` (RP-initiated logout).

### 6.5 Seeded Users
`infra/keycloak/users.yaml` + `make seed-users` (idempotent; it creates or updates users and reconciles role assignments to match the file). Default users, all with password `password` (dev only):

| User | Roles | Demonstrates |
|---|---|---|
| `alice` | admin | Full access, block/unblock |
| `eddie` | editor | Owns links |
| `erin` | editor | Cannot see eddie's links |
| `victor` | viewer | Read-only across all links |
| `nora` | (none) | `403` / "no access" page |

`make up` runs the seeding automatically once Keycloak is healthy (a one-shot `keycloak-seed` compose service using the same script).

## 7. Admin UI

Jinja2 + HTMX, with Pico.css for styling and Chart.js for charts. All vendored under `static/`; no CDN dependency.

| Route | Content |
|---|---|
| `/` | Dashboard: link count, total clicks (last 7 days), top 5 links by clicks. Editors see their own links only. |
| `/links` | Table with search, status filter, and pagination. Filter and paging use `hx-get` to swap the table fragment. |
| `/links/new` | Create form (editor/admin). On success, shows the short URL with a copy button. |
| `/links/{id}` | Details: target, status badge, block reason if blocked. Inline edit form (`hx-post` to the admin service, which calls the API's `PATCH`), active toggle, delete with an in-page confirmation, block/unblock with a reason (admin), click chart with an hour/day range selector, top referrers. |
| `/auth/login`, `/auth/callback`, `/auth/logout` | OIDC |

- Routes render the full page, or a partial when the `HX-Request` header is present.
- Controls are hidden according to role (from the session's roles), but **the API is the authority**. API `403`/`404`/`409` responses are shown as inline messages.
- `ApiClient` (httpx) adds `Authorization: Bearer <access_token>` and converts problem+json into a typed `ApiError`.
- CSRF: the per-session token is rendered into `<body hx-headers='{"X-CSRF-Token": "..."}'>` and into a hidden field for non-HTMX forms. It is checked on all unsafe methods.
- A user with no roles sees a "no access" page with a logout button.

## 8. Error Handling

- **Format:** every API error is RFC 9457 `application/problem+json` (`type`, `title`, `status`, `detail`, plus `errors` for validation). This covers FastAPI `RequestValidationError` (422) and `HTTPException`.
- **Click recording:** a background task with its own DB session. Failures are logged at WARN with trace context and counted in `shortener.click_events.write_failures`. They never affect the redirect response.
- **DB unavailable:** redirects and API calls return `503`; `/readyz` fails.
- **Keycloak unavailable:** cached signing keys keep existing tokens valid. If the keys can't be fetched at all (cold start), authenticated endpoints return `503`. Public redirects are unaffected. The admin UI shows an error page if login or refresh can't reach Keycloak.
- **Code collision exhaustion:** `500`, logged at ERROR. Effectively impossible (62⁷ ≈ 3.5 × 10¹²).
- **Admin UI:** an `ApiError` becomes a flash/inline message. Unexpected errors render a generic error page that shows the `trace_id`, so it can be looked up in Grafana.

## 9. Observability

- **SDK:** OpenTelemetry Python SDK in both services. Auto-instrumentation for FastAPI, httpx, SQLAlchemy (asyncpg), and logging. Exports over OTLP to `otel-lgtm:4317`.
- **Resource attributes:** `service.name` (`shortener-api` / `shortener-admin`), `service.version`, `deployment.environment` (`local`).
- **Traces:** W3C `traceparent` is passed from admin to api to Postgres, so one UI action produces a single end-to-end trace in Tempo.
- **Logs:** structured JSON to stdout, and also exported over OTLP to Loki. Every record includes `trace_id` and `span_id`.
- **Custom metrics (API):**

| Metric | Type | Attributes |
|---|---|---|
| `shortener.redirects` | counter | `result` = `ok\|not_found\|disabled\|blocked` |
| `shortener.redirect.duration` | histogram (s) | `result` |
| `shortener.links.created` | counter | — |
| `shortener.links.blocked` | counter | — |
| `shortener.click_events.write_failures` | counter | — |

  **No per-link attribute (e.g. `short_code`) is ever attached to a metric.** That would cause unbounded cardinality. Per-link analytics live in Postgres (D4).
- **Dashboard:** "Shortener Overview", provisioned from `infra/otel/dashboards/`. Shows redirect rate by result, p50/p95/p99 redirect latency, HTTP 5xx rate per service, click-write failures, and links created/blocked.

## 10. AWS Mapping (for the later Terraform discussion)

| Local | AWS |
|---|---|
| `api`, `admin` containers | ECS Fargate services behind an ALB (host- or path-based routing) |
| `postgres` | RDS for PostgreSQL (separate instance or DB for Keycloak) |
| `keycloak` | ECS Fargate service on its own RDS DB, or replaced by Amazon Cognito (the OIDC settings make this a config change) |
| `otel-lgtm` | ADOT collector sidecar → CloudWatch Logs/Metrics + X-Ray, or Amazon Managed Prometheus + Managed Grafana |
| `migrate` | One-off ECS task run by the deploy pipeline before rolling out services |
| `keycloak-seed` | Not used in prod; realm managed by the Keycloak Terraform provider |
| `.env` secrets | Secrets Manager / SSM Parameter Store, injected into task definitions |

All configuration comes from environment variables (pydantic-settings). Nothing is hard-coded to `localhost` outside `.env.example` and compose.

## 11. Future Work

Not built in this iteration; the design leaves room for each.

1. **Custom aliases:** user-chosen `code` values, with validation, reserved words, and duplicate rejection. The `code` column is already `varchar(32)`.
2. **Link expiration:** nullable `expires_at`; expired links return `410 Gone`.
3. **Redirect caching (Redis / ElastiCache):** cache-aside on `code → (target_url, status)`, cleared when a link is updated or blocked. The first scaling step, justified by the `shortener.redirect.duration` metrics.
4. **Teams / orgs:** Keycloak groups → `org_id` on links; the policy function gains org-scoped rules.
5. **Embedded Grafana panels:** an admin-only "System health" page in the admin UI showing service metrics (option C from brainstorming).
6. **Audit log:** an append-only `link_audit` table (who, what, when, before/after) for all link changes, including block/unblock.
7. **Domain blocklist:** target domains checked on create/update; blocked domains rejected with `422`.
8. **Block appeals:** an owner-initiated appeal workflow for blocked links.
9. **Keycloak via Terraform:** manage the realm, clients, and roles with the Keycloak Terraform provider instead of a JSON import.
10. **Playwright UI tests** and **e2e tests in CI.**
11. **Click-event retention / rollups:** a daily aggregates table and pruning of raw events once volume justifies it.

## 12. Testing Strategy

Written test-first (TDD).

| Layer | Scope | Approach |
|---|---|---|
| Unit (api) | `codes.py`, `urls.py`, `policy.py` | `policy.can` is tested with a table of cases covering every role × action × owned/not-owned × blocked/not-blocked combination (§5.1). |
| Integration (api) | Every endpoint, redirects, click recording, stats aggregation, block rules | `pytest` + `httpx.AsyncClient` (ASGI transport) against real Postgres via **testcontainers**. Auth: the test fixture generates an RSA keypair, overrides the JWKS provider, and mints tokens with any roles. No Keycloak needed. |
| Integration (admin) | Routes, session store, refresh, CSRF, HTMX fragments | API mocked with `respx`; real Postgres for `admin.sessions`; OIDC callback stubbed. |
| E2E smoke | The full compose stack | `make e2e` (pytest, `-m e2e`): real tokens via `shortener-dev` for seeded users. Scenario: eddie creates a link → erin gets `404` on it → following it records a click (stats total = 1) → alice blocks it → eddie's `PATCH` gets `409` → the redirect returns `410` → victor can read it but gets `403` on create → nora gets `403`. |

**CI (GitHub Actions):** `ruff check`, `ruff format --check`, unit + integration tests for both services, and Docker image builds. E2E runs locally only for now.

## 13. Success Criteria

- `make up` on a clean machine (Docker or colima) brings up all services healthy, with the realm and users seeded, without manual steps.
- Logging in as each seeded user shows behavior matching §5.1.
- Following a short link records a click that shows up in the admin UI chart.
- An admin block is enforced as described in §4.3 and can't be bypassed by the owner.
- A UI action can be followed as a single trace in Grafana/Tempo, with correlated logs, and the Overview dashboard shows redirect metrics.
- `make test` and `make e2e` pass; CI is green.
