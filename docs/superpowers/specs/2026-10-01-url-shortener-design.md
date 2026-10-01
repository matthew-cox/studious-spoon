# URL Shortener Platform — Design Spec

- **Date:** 2026-10-01
- **Status:** Draft — pending review (rev 2: event-driven click pipeline)
- **Context:** Technical interview exercise. Must run entirely locally (Docker / colima) and be designed for a later AWS deployment via Terraform.

## 1. Goals and Non-Goals

### Goals
- A URL shortening service with:
  - a JSON **API** that also serves public redirects
  - an **admin UI**
  - a **Postgres** database
  - **Keycloak** for authentication and role-based authorization
  - an **OpenTelemetry** pipeline for metrics, logs, and traces
- **Event-driven click analytics:** each redirect emits a `link.clicked` event to a queue. A separate processor turns events into hourly rollups that the admin UI displays. The event format is designed so CloudFront access logs can become a second event source later without changing anything downstream.
- A single `docker compose up` (via `make up`) brings up the entire stack, including a Keycloak realm with seeded test users and roles.
- Admins can block a link for abuse, and the owner cannot override the block.
- Every component maps cleanly to an AWS equivalent (see §11).
- The code is clear, tested, and explainable; this is valued over raw scale.

### Non-Goals (this iteration)
- Custom aliases, link expiration, Redis caching, teams/orgs, embedded Grafana panels, audit log, domain blocklist, block appeals, CloudFront integration, raw-event archive. All are captured in §12 (Future Work), and the design leaves room for each.
- Storing individual click events anywhere we can query them. Only rollups are kept (see D12).
- Exact (exactly-once) click counts. Counts are at-least-once and may very occasionally be slightly too high (D13).
- Terraform / AWS deployment itself.
- Geo-IP enrichment; storing client IPs.

## 2. Key Decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | **Python + FastAPI** for all services | Author's strongest stack; free OpenAPI docs; mature OTEL and OIDC libraries. |
| D2 | **Admin UI = FastAPI + Jinja2 + HTMX, as a separate service** (not a React SPA, not pages inside the API) | Author has HTMX experience and is weak on React. Running it as its own service keeps the UI/API separation clean: the API is the single source of truth and enforces all authorization, and the admin UI calls it with the user's own token (token relay). Tokens never reach the browser. |
| D3 | **Postgres 16 + SQLAlchemy 2.x (async) + Alembic** for link data, rollups, and admin sessions | Conventional; maps directly to RDS. |
| D4 | **Click analytics are event-driven and kept separate from operational metrics.** Redirects publish `link.clicked` events. OTEL metrics are for operating the service only. | Clicks are business data: they must be durable, queryable per link, and filtered by the caller's permissions. Prometheus is the wrong store for that (a per-link label would cause a cardinality explosion). Publishing events keeps database writes off the redirect path and lets more than one source produce clicks (CloudFront later). |
| D5 | **`grafana/otel-lgtm` all-in-one** for local observability | One container provides the collector, Prometheus, Loki, Tempo, and Grafana. Lighter footprint for local runs. |
| D6 | **Owner on/off switch (`is_active`) and admin abuse block (`blocked_*`) stored separately** | See §4.3. A single flag would let the owner re-enable a blocked link, or point it at a "clean" URL to get it unblocked. Storing them independently means unblocking restores whatever `is_active` was before. |
| D7 | **Admin UI sessions stored server-side in Postgres**, in a separate `admin` schema with its own DB user | Keycloak tokens are too large to fit safely in a cookie (~4 KB limit). An in-memory store breaks once more than one instance runs (ECS). Redis is out of scope. |
| D8 | **`302` redirects with `Cache-Control: private, no-store`** | Browsers cache `301`, which would skip the server and lose clicks. Edge caching is future work (§12), once CloudFront logs can count clicks served from the cache. |
| D9 | **A non-owner gets `404`, not `403`** | Avoids revealing that a link exists. |
| D10 | **Keycloak realm roles** `admin`, `editor`, `viewer`; login required to create links; redirects are public | Matches the stated requirements. Keycloak groups are where orgs/teams would hook in later. |
| D11 | **Amazon SQS for the click queue; ElasticMQ locally** | CloudFront's future source is *standard* logs delivered to S3 and processed in batches. Real-time logs aren't needed: stats are hourly, and real-time logs cost more and need Kinesis. The S3 path is S3 → event notification → SQS, so SQS covers both producers with one kind of infrastructure, is fully managed, and has no shards to size. ElasticMQ speaks the SQS API, so boto3 code only changes its endpoint URL. |
| D12 | **Rollups (not raw events) in a separate `analytics` schema in Postgres** | Rollup size grows with links × active hours, not with clicks. Queries need joins with `links` (ownership filtering, top links), which is plain SQL in Postgres. Writes go through a `RollupStore` interface, so DynamoDB could replace it later (§12). |
| D13 | **At-least-once processing with no per-event dedupe table** | A dedupe table would put one row per event back into Postgres. Duplicate deliveries from SQS are rare, and a slight overcount is acceptable for analytics. Double counting between the API and CloudFront is avoided structurally instead: each source counts only the requests it actually served (§5.4). Exact counts, if ever needed, can be rebuilt from the future S3 archive. |
| D14 | **No time-series or analytics database (for now)** | TimescaleDB's continuous aggregates and retention policies are attractive, but: (1) it doesn't run on RDS or Aurora (its license prevents cloud providers from offering it as a managed service), so on AWS it would mean Timescale Cloud or self-hosting on EC2; (2) the processor already rolls up at write time, so continuous aggregates would mostly duplicate it; (3) metric-style time-series databases (InfluxDB, Prometheus, Timestream) handle our data shape poorly: many series (one per link) with few points each is their cardinality weakness. Compaction and retention use `pg_cron` (supported on RDS) instead. Revisit if raw clicks need to be queryable with low latency (§12, item 15). |

## 3. Architecture

### 3.1 Overview

```
 browser ─► admin (8001) ──bearer token──► api (8000) ──► postgres (public: links)
                │                            │  ▲
                └─► postgres (admin schema)  │  └─ reads analytics.* for /stats
                                             │
 GET /{code} ─► api ─► ClickPublisher ─► SQS: click-events ─► click-processor ─► postgres (analytics schema)
                       (in-memory buffer,           │
                        batched sends)              └─► click-events-dlq (after 5 failed receives)

 all services ─OTLP─► otel-lgtm ; admin/api ─OIDC─► keycloak
```

### 3.2 Services (docker compose)

| Service | Image / build | Port | Purpose |
|---|---|---|---|
| `api` | built from `api/` | 8000 | JSON API (`/api/v1/...`) + public redirects `GET /{code}`. Publishes click events. |
| `admin` | built from `admin/` | 8001 | Admin UI. Keycloak confidential client; calls `api` using the user's access token. |
| `click-processor` | built from `processor/` | 8002 (health/metrics only) | Reads from `click-events` and writes rollups to `analytics` |
| `postgres` | `postgres:16` | 5432 | Databases: `shortener` (schemas `public`, `analytics`, `admin`) and `keycloak` |
| `elasticmq` | `softwaremill/elasticmq-native` (pinned) | 9324 (SQS API), 9325 (UI) | Local SQS. Queues `click-events` and `click-events-dlq` are created from `infra/elasticmq/elasticmq.conf`. |
| `keycloak` | `quay.io/keycloak/keycloak` (pinned) | 8080 | Starts with `--import-realm` |
| `keycloak-seed` | `api` image (or a small python image), runs `seed_users.py` | — | One-off job: seeds users from `users.yaml` once Keycloak is healthy |
| `otel-lgtm` | `grafana/otel-lgtm` (pinned) | 3000 (Grafana), 4317, 4318 (OTLP) | Collector + Prometheus + Loki + Tempo + Grafana |
| `migrate` | `api` image, runs `alembic upgrade head` | — | One-off job. `api`, `admin`, and `click-processor` depend on it with `condition: service_completed_successfully`. |

All services have healthchecks, and startup order is enforced with `depends_on: condition: service_healthy`.

### 3.3 Repository Layout

uv workspace at the repo root. Each service is a workspace member with its own `pyproject.toml` and Dockerfile.

```
pyproject.toml                 uv workspace definition (members below)
libs/shortener-events/         shared package: ClickEvent model, JSON schema, SQS codec
api/
  Dockerfile, alembic.ini, alembic/        (owns ALL migrations: public, analytics, admin schemas)
  src/shortener_api/
    main.py, config.py, db.py, models.py, schemas.py
    auth.py            JWT validation, Principal, require_roles
    policy.py          can(principal, action, link): pure function
    codes.py           short-code generator + reserved words
    urls.py            target URL validation
    publisher.py       ClickPublisher (buffered, batched SQS sends)
    routes/redirect.py, routes/links.py, routes/stats.py, routes/health.py
    telemetry.py, errors.py
  tests/unit/, tests/integration/
processor/
  Dockerfile
  src/shortener_processor/
    main.py            consume loop + health/metrics endpoint
    config.py
    consumer.py        SQS receive/delete, batch window
    aggregate.py       pure function: events → rollup deltas
    rollup_store.py    RollupStore protocol + PostgresRollupStore
    link_resolver.py   code → link_id lookup (for events without link_id)
    telemetry.py
  tests/unit/, tests/integration/
admin/
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
  keycloak/realm-export.json, users.yaml, seed_users.py
  elasticmq/elasticmq.conf     queues + DLQ redrive policy
  otel/dashboards/             provisioned Grafana dashboard(s)
  postgres/init.sql            creates keycloak DB, schemas, DB users/grants
tests/e2e/                     stack-level smoke tests (make e2e)
docs/
docker-compose.yml, Makefile, .env.example, README.md
.github/workflows/ci.yml
```

A `terraform/` directory is reserved for later.

**Tooling:** `uv` (dependencies and workspace), `ruff` (lint/format), `pytest`, Makefile targets: `up`, `down`, `logs`, `test`, `lint`, `migrate`, `seed-users`, `e2e`, `token USER=<name>`.

### 3.4 Database Users and Grants

| DB user | Privileges |
|---|---|
| `api_user` | read/write `public.*`; read-only `analytics.*` |
| `processor_user` | read/write `analytics.*`; read-only `public.links (id, code)` |
| `admin_user` | read/write `admin.*` only |
| `migrator` | Owns all schemas; used only by the `migrate` job |

## 4. Data Model

### 4.1 `public.links`

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

### 4.2 `analytics` schema (rollups)

```
analytics.link_clicks_hourly
  link_id       uuid         FK → public.links.id ON DELETE CASCADE
  bucket_start  timestamptz  truncated to the hour (UTC)
  count         bigint       NOT NULL
  PRIMARY KEY (link_id, bucket_start)

analytics.link_referrers_daily
  link_id        uuid   FK → public.links.id ON DELETE CASCADE
  bucket_date    date   (UTC)
  referrer_host  text   lower-cased host of the Referer header, or '(direct)' if absent/unparseable
  count          bigint NOT NULL
  PRIMARY KEY (link_id, bucket_date, referrer_host)

analytics.pipeline_status
  id                 smallint PK (always 1)
  last_committed_at  timestamptz   time of the processor's last successful batch commit
```

- Buckets are based on the event's **`occurred_at`**, not on when it was processed, so events that arrive late land in the correct hour.
- Daily click series are computed by summing hourly rows.
- Referrers are stored by **host only**, which keeps the number of rows bounded.

### 4.3 Administrative Block (abuse handling)

The owner's switch and the admin block are deliberately separate (D6):

- **Redirect:** a blocked link always returns **`410 Gone`** with a small HTML "This link has been disabled" page, whatever `is_active` says. **No click event is published**, but `shortener.redirects{result="blocked"}` is incremented.
- **Owner restrictions while blocked:**
  - `PATCH` (any field) → `409 Conflict`; the problem detail includes the block reason.
  - `DELETE` → `409 Conflict`, so the record is kept as evidence.
- **Admins** can block, unblock, edit, and delete any link, blocked or not.
- **Unblocking** clears all three `blocked_*` columns. The link goes back to its previous `is_active` value.
- **Admin UI:** a "Blocked" badge and the reason are shown to everyone who can see the link. Admins get block/unblock controls; blocking requires entering a reason.
- **Future edge caching:** once redirects are cached in CloudFront, block, disable, update, and delete must also trigger a CloudFront invalidation for `/{code}` (§12).

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

Expired sessions are deleted lazily on access, plus an opportunistic cleanup on login.

## 5. Click Event Pipeline

### 5.1 Event Contract (`libs/shortener-events`)

The event format is the stable interface between producers and the processor. It is defined once as a Pydantic model, with a JSON Schema exported to `libs/shortener-events/schema/link.clicked.v1.json`.

```json
{
  "type": "link.clicked",
  "version": 1,
  "event_id": "uuid (api) | x-edge-request-id (cloudfront)",
  "occurred_at": "2026-10-01T12:34:56.789Z",
  "source": "api | cloudfront",
  "code": "aZ3kQ9x",
  "link_id": "uuid | null",
  "referrer": "https://news.ycombinator.com/item?id=1 | null",
  "user_agent": "Mozilla/5.0 ... | null"
}
```

- The SQS message body is this JSON.
- SQS message attributes carry `type`, `version`, and the W3C `traceparent` for trace propagation.
- `link_id` is optional because CloudFront log lines only have the path. The processor resolves `code → link_id` when it is missing.
- Changes that aren't backward compatible bump `version`. The processor rejects versions it doesn't know (they end up in the DLQ; §5.3).

### 5.2 Publisher (API)

- `ClickPublisher` is an interface with an `SqsClickPublisher` implementation (aioboto3, with `SQS_ENDPOINT_URL` pointing at ElasticMQ locally) and an `InMemoryClickPublisher` for tests.
- The redirect handler calls `publisher.publish(event)`, which **only puts the event in an in-memory `asyncio.Queue` (default maxsize 10,000) and returns immediately.** The redirect never waits on SQS.
- A background flusher sends messages with `SendMessageBatch`, up to 10 per call. It flushes when 10 events are waiting or every 250 ms.
- If the queue is full, the event is **dropped** and `shortener.click_events.dropped` is incremented.
- If a send fails, it retries up to 3 times with jittered backoff. After that the batch is dropped and `shortener.click_events.publish_failures` is incremented. Messages that SQS rejects individually inside a batch are retried the same way.
- On shutdown (`lifespan`), the flusher tries to send what's left for up to 5 s.
- **Accepted risk:** if the API crashes, events still in its memory buffer (at most about a few hundred ms of clicks) are lost. This is documented, and the metrics make it visible.

### 5.3 Processor (`click-processor`)

Loop:
1. Long-poll `ReceiveMessage` (`WaitTimeSeconds=20`, `MaxNumberOfMessages=10`). Keep collecting until there are **100 messages or 1 s has passed**, whichever comes first. The queue's visibility timeout is 30 s.
2. Parse and validate each message on its own:
   - **Invalid or unknown version:** leave it on the queue (don't delete it) and log + count `result=invalid`. After 5 receives, SQS moves it to `click-events-dlq` (the redrive policy in `elasticmq.conf`, `maxReceiveCount=5`).
   - **`link_id` missing:** resolve it with `LinkResolver` (a `code → link_id` lookup with a small TTL cache).
   - **Link unknown or deleted:** count `result=unknown_link` and delete the message.
3. `aggregate(events) -> RollupDeltas`, a **pure function**, groups counts by `(link_id, hour)` and `(link_id, date, referrer_host)`.
4. In **one transaction**, upsert every delta (`INSERT … ON CONFLICT DO UPDATE SET count = count + excluded.count`) and update `pipeline_status.last_committed_at`. Rows whose link was deleted at the same moment are skipped if they violate the foreign key: the processor checks link existence again inside the transaction.
5. After the commit, call `DeleteMessageBatch` for every processed message (valid ones and unknown-link ones).
   - If the transaction fails, nothing is deleted. The messages reappear after the visibility timeout and are retried.
   - If the commit succeeds but the delete fails, the messages are redelivered and **counted twice**. This is the accepted at-least-once overcount (D13).

- The processor processes one batch at a time. Running more processor containers scales it horizontally, because upsert increments don't conflict at the logical level. Postgres row locks make them run one after another per row.
- Tracing: each batch gets a `process click batch` span, with **span links** to the `traceparent` of each message's producer span. A batch has many parents, so links are used rather than a single parent.

### 5.4 Counting Rules (who counts which click)

| Source | Counts | Status |
|---|---|---|
| `api` | Every successful `302` the API itself serves (`result=ok`) | Built now |
| `cloudfront` | Only edge cache hits: `x-edge-result-type` ∈ {`Hit`, `RefreshHit`} with status 302 | Future (§12) |

Each click is counted by exactly one source, so there is no need to dedupe across sources.

## 6. API

Base path for JSON endpoints: `/api/v1`. Errors are `application/problem+json` (§9). OpenAPI docs are at `/docs`.

| Method & Path | Roles | Behavior |
|---|---|---|
| `GET /{code}` | public | Active and not blocked → `302` to the target, with `Cache-Control: private, no-store`, and the click event is published. Unknown or inactive → `404`. Blocked → `410` HTML page. |
| `GET /healthz` | public | Liveness check |
| `GET /readyz` | public | Readiness check: tests the DB connection. SQS is *not* checked, because redirects must keep working when SQS is down. |
| `GET /api/v1/me` | any role | `{sub, username, roles}` |
| `POST /api/v1/links` | editor, admin | Body `{target_url}` → `201` with the link, including `short_url` |
| `GET /api/v1/links` | viewer, editor, admin | Query: `q` (matches code or target), `status` (`active\|disabled\|blocked`), `owner` (admin/viewer only), `page`, `page_size` (max 100). Editors only get their own links. |
| `GET /api/v1/links/{id}` | viewer, editor (own), admin | Editors get `404` for links they don't own |
| `PATCH /api/v1/links/{id}` | editor (own), admin | Body `{target_url?, is_active?}`. Owner on a blocked link → `409`. |
| `DELETE /api/v1/links/{id}` | editor (own), admin | `204`. Owner on a blocked link → `409`. Rollups are removed by cascade. |
| `POST /api/v1/links/{id}/block` | admin | Body `{reason}` (required, 1–1000 chars) → `200`. Already blocked → `409`. |
| `POST /api/v1/links/{id}/unblock` | admin | `200`. Not blocked → `409`. |
| `GET /api/v1/links/{id}/stats` | viewer, editor (own), admin | Query `from`, `to` (default: last 7 days), `bucket=hour\|day`. Returns `{total, series:[{ts,count}], top_referrers:[{referrer_host,count}], data_as_of}`. Top referrers: top 10 over the range. `data_as_of` = `pipeline_status.last_committed_at`. |
| `GET /api/v1/stats/summary` | viewer, editor, admin | For the dashboard: `{link_count, clicks_7d, top_links:[{id, code, clicks_7d}], data_as_of}`. Editors only get their own links. |

### 6.1 Permission Policy

Implemented as a pure function `can(principal, action, link) -> bool` in `policy.py`. Routes call it; nothing else makes authorization decisions.

| Action | admin | editor (own) | editor (other's) | viewer | no role |
|---|---|---|---|---|---|
| list / read / stats | ✅ | ✅ | ❌ (404) | ✅ | ❌ (403) |
| create | ✅ | ✅ | — | ❌ (403) | ❌ (403) |
| update / delete (not blocked) | ✅ | ✅ | ❌ (404) | ❌ (403) | ❌ (403) |
| update / delete (blocked) | ✅ | ❌ (409) | ❌ (404) | ❌ (403) | ❌ (403) |
| block / unblock | ✅ | ❌ (403) | ❌ (404) | ❌ (403) | ❌ (403) |

Missing or invalid token → `401`.

## 7. Authentication & Keycloak

### 7.1 Realm `shortener`
- Realm roles: `admin`, `editor`, `viewer`, emitted in the access token under `realm_access.roles`.
- Clients:

| Client | Type | Config |
|---|---|---|
| `shortener-admin` | confidential, standard login flow, PKCE S256 | Redirect URI `http://localhost:8001/auth/callback`. Post-logout redirect `http://localhost:8001/`. An audience mapper adds `shortener-api` to `aud`. |
| `shortener-api` | resource server (bearer-only, no flows) | Exists only as a token audience |
| `shortener-dev` | public client, direct username/password login enabled | **Local/dev only**, for curl, `make token`, and e2e tests. Audience mapper adds `shortener-api`. Must not exist in non-local realms. |

### 7.2 Token Validation (API)
- Uses PyJWT (or `joserfc`) with a JWKS cache fetched from `OIDC_INTERNAL_URL`. When a token arrives with an unknown `kid`, the cache is refreshed (rate-limited).
- Checks the signature (RS256), `iss == OIDC_ISSUER`, `aud` contains `shortener-api`, `exp`/`nbf` (with 30 s leeway).
- Produces `Principal(sub, username, roles)`. FastAPI dependencies `current_principal` and `require_roles(*roles)`.

### 7.3 Issuer / Hostname Handling
The browser reaches Keycloak at `http://localhost:8080`, but containers reach it at `http://keycloak:8080`. Keycloak runs with `KC_HOSTNAME=http://localhost:8080` (with backchannel dynamic) so the token issuer is always the same. Both services take two settings:
- `OIDC_ISSUER=http://localhost:8080/realms/shortener`: used to check `iss`, and for the browser-facing redirects.
- `OIDC_INTERNAL_URL=http://keycloak:8080/realms/shortener`: used for JWKS, token exchange, and refresh.

### 7.4 Admin UI Login Flow
1. An unauthenticated request redirects to `/auth/login`. Authlib builds the authorization request (state, nonce, PKCE).
2. `/auth/callback` exchanges the code for tokens over the internal URL, creates an `admin.sessions` row, and sets cookie `sid` (`HttpOnly`, `SameSite=Lax`, `Secure` when not local, path `/`).
3. Each request loads the session. If the access token expires within 30 s, it is refreshed. If the refresh fails, the session is deleted and the user is redirected to login with a "session expired" message.
4. `/auth/logout` deletes the session and redirects to Keycloak's end-session endpoint with `id_token_hint` (RP-initiated logout).

### 7.5 Seeded Users
`infra/keycloak/users.yaml` + `seed_users.py` (idempotent; it creates or updates users and reconciles role assignments to match the file). It runs automatically through the `keycloak-seed` compose job, and can be re-run with `make seed-users`. Default users, all with password `password` (dev only):

| User | Roles | Demonstrates |
|---|---|---|
| `alice` | admin | Full access, block/unblock |
| `eddie` | editor | Owns links |
| `erin` | editor | Cannot see eddie's links |
| `victor` | viewer | Read-only across all links |
| `nora` | (none) | `403` / "no access" page |

## 8. Admin UI

Jinja2 + HTMX, with Pico.css for styling and Chart.js for charts. All vendored under `static/`; no CDN dependency.

| Route | Content |
|---|---|
| `/` | Dashboard (from `/api/v1/stats/summary`): link count, clicks over the last 7 days, top 5 links. Editors see their own links only. |
| `/links` | Table with search, status filter, and pagination. Filter and paging use `hx-get` to swap the table fragment. |
| `/links/new` | Create form (editor/admin). On success, shows the short URL with a copy button. |
| `/links/{id}` | Details: target, status badge, block reason if blocked. Inline edit form (`hx-post` to the admin service, which calls the API's `PATCH`), active toggle, delete with an in-page confirmation, block/unblock with a reason (admin), click chart with an hour/day range selector, top referrer hosts. |
| `/auth/login`, `/auth/callback`, `/auth/logout` | OIDC |

- Pages showing stats display **"Data as of HH:MM:SS"** from `data_as_of`, so it's clear the numbers may lag slightly (stats are eventually consistent).
- Routes render the full page, or a partial when the `HX-Request` header is present.
- Controls are hidden according to role (from the session's roles), but **the API is the authority**. API `403`/`404`/`409` responses are shown as inline messages.
- `ApiClient` (httpx) adds `Authorization: Bearer <access_token>` and converts problem+json into a typed `ApiError`.
- CSRF: the per-session token is rendered into `<body hx-headers='{"X-CSRF-Token": "..."}'>` and into a hidden field for non-HTMX forms. It is checked on all unsafe methods.
- A user with no roles sees a "no access" page with a logout button.

## 9. Error Handling

- **Format:** every API error is RFC 9457 `application/problem+json` (`type`, `title`, `status`, `detail`, plus `errors` for validation). This covers FastAPI `RequestValidationError` (422) and `HTTPException`.
- **Redirects come first:** publishing a click event can never fail or slow a redirect (§5.2). If SQS is unavailable, redirects keep working; clicks are dropped and counted (`dropped`, `publish_failures`).
- **DB unavailable:** redirects and API calls return `503`; `/readyz` fails. The processor stops deleting messages, so they stay in SQS and are processed once the DB is back. This is the key durability benefit of using a queue.
- **Processor failures:** handled as in §5.3. Bad messages go to the DLQ, and a non-empty DLQ is visible on the dashboard. Messages in the DLQ can be sent back for reprocessing with ElasticMQ/SQS redrive.
- **Keycloak unavailable:** cached signing keys keep existing tokens valid. If the keys can't be fetched at all (cold start), authenticated endpoints return `503`. Public redirects are unaffected. The admin UI shows an error page if login or refresh can't reach Keycloak.
- **Code collision exhaustion:** `500`, logged at ERROR. Effectively impossible (62⁷ ≈ 3.5 × 10¹²).
- **Admin UI:** an `ApiError` becomes a flash/inline message. Unexpected errors render a generic error page that shows the `trace_id`, so it can be looked up in Grafana.

## 10. Observability

- **SDK:** OpenTelemetry Python SDK in all three services. Auto-instrumentation for FastAPI, httpx, SQLAlchemy (asyncpg), botocore/aiobotocore, and logging. Exports over OTLP to `otel-lgtm:4317`.
- **Resource attributes:** `service.name` (`shortener-api` / `shortener-admin` / `shortener-click-processor`), `service.version`, `deployment.environment` (`local`).
- **Traces:** W3C `traceparent` is passed from admin to api to Postgres, so one UI action produces a single end-to-end trace in Tempo. For clicks, `traceparent` travels in SQS message attributes, and the processor's batch span **links** to each producer span (§5.3).
- **Logs:** structured JSON to stdout, and also exported over OTLP to Loki. Every record includes `trace_id` and `span_id`.
- **Custom metrics:**

| Service | Metric | Type | Attributes |
|---|---|---|---|
| api | `shortener.redirects` | counter | `result` = `ok\|not_found\|disabled\|blocked` |
| api | `shortener.redirect.duration` | histogram (s) | `result` |
| api | `shortener.links.created` | counter | — |
| api | `shortener.links.blocked` | counter | — |
| api | `shortener.click_events.published` | counter | — |
| api | `shortener.click_events.dropped` | counter | `reason` = `buffer_full\|publish_failed` |
| api | `shortener.click_events.buffer_size` | gauge | — |
| processor | `shortener.processor.messages` | counter | `result` = `ok\|invalid\|unknown_link` |
| processor | `shortener.processor.batch.duration` | histogram (s) | — |
| processor | `shortener.processor.event_lag` | histogram (s) | from `occurred_at` to commit |
| processor | `shortener.queue.depth` | gauge | `queue` = `click-events\|click-events-dlq` (polled from `GetQueueAttributes` every 30 s) |

  **No per-link attribute (e.g. `code`, `link_id`) is ever attached to a metric.** That would cause unbounded cardinality; per-link analytics live in the rollups (D4).
- **Dashboard:** "Shortener Overview", provisioned from `infra/otel/dashboards/`. Panels:
  - redirect rate by result
  - p50/p95/p99 redirect latency
  - HTTP 5xx rate per service
  - click pipeline: published, dropped, processed by result, event lag p95, queue depth, DLQ depth
  - links created/blocked

## 11. AWS Mapping (for the later Terraform discussion)

| Local | AWS |
|---|---|
| `api`, `admin` containers | ECS Fargate services behind an ALB (host- or path-based routing) |
| `click-processor` | ECS Fargate service scaled on queue depth. Alternative: Lambda with an SQS event source; the pure `aggregate` and `RollupStore` code carries over unchanged. |
| `elasticmq` | Amazon SQS standard queue `click-events` + `click-events-dlq` with a redrive policy |
| `postgres` | RDS for PostgreSQL (separate instance or DB for Keycloak) |
| `keycloak` | ECS Fargate service on its own RDS DB, or replaced by Amazon Cognito (the OIDC settings make this a config change) |
| `otel-lgtm` | ADOT collector sidecar → CloudWatch Logs/Metrics + X-Ray, or Amazon Managed Prometheus + Managed Grafana |
| `migrate` | One-off ECS task run by the deploy pipeline before rolling out services |
| `keycloak-seed` | Not used in prod; realm managed by the Keycloak Terraform provider |
| `.env` secrets | Secrets Manager / SSM Parameter Store, injected into task definitions |
| (future) edge | CloudFront in front of the ALB, with standard logs delivered to S3 |

All configuration comes from environment variables (pydantic-settings). Nothing is hard-coded to `localhost` outside `.env.example` and compose. Locally, `SQS_ENDPOINT_URL` points at ElasticMQ; in AWS it is left unset.

## 12. Future Work

Not built in this iteration; the design leaves room for each.

1. **CloudFront in front of redirects, with edge caching:**
   - Allow short-TTL edge caching of `302`s (change D8's `Cache-Control`).
   - Block, disable, update, and delete must trigger a CloudFront invalidation for `/{code}`.
2. **CloudFront log ingester (batch, not real-time):**
   - CloudFront standard logs → S3 → S3 event notification → SQS `cf-log-files` → ingester.
   - The ingester parses each log file and publishes `link.clicked` v1 events (`source=cloudfront`, `event_id = x-edge-request-id`) **only for edge cache hits** (§5.4).
   - Delivery is best-effort and usually takes minutes; that's acceptable for hourly stats. Real-time logs (Kinesis) were considered and rejected as unnecessary (D11).
3. **Raw event archive:**
   - Firehose (or the processor) writes every `link.clicked` event to S3 as partitioned Parquet, queryable with Athena.
   - This makes rollups rebuildable and enables exact recounts, which matters because SQS is not a replayable log.
4. **DynamoDB rollup store:** an alternative `RollupStore` (PK `link_id`, SK `hour`, atomic `ADD` counters) if rollup write volume outgrows Postgres. Top-links queries would need a GSI or a lookup in Postgres first.
5. **Custom aliases:** user-chosen `code` values, with validation, reserved words, and duplicate rejection. The `code` column is already `varchar(32)`.
6. **Link expiration:** nullable `expires_at`; expired links return `410 Gone`.
7. **Redirect caching (Redis / ElastiCache):** cache-aside on `code → (target_url, status)`, cleared when a link is updated or blocked. Justified by the `shortener.redirect.duration` metrics.
8. **Teams / orgs:** Keycloak groups → `org_id` on links; the policy function gains org-scoped rules.
9. **Embedded Grafana panels:** an admin-only "System health" page in the admin UI showing service metrics.
10. **Audit log:** an append-only `link_audit` table (who, what, when, before/after) for all link changes, including block/unblock.
11. **Domain blocklist:** target domains checked on create/update; blocked domains rejected with `422`.
12. **Block appeals:** an owner-initiated appeal workflow for blocked links.
13. **Keycloak via Terraform:** manage the realm, clients, and roles with the Keycloak Terraform provider instead of a JSON import.
14. **Playwright UI tests** and **e2e tests in CI.**
15. **Rollup compaction and retention, then an analytics store if needed:**
    - **Step 1 (no new infrastructure):** a `pg_cron` job (supported on RDS) that folds `link_clicks_hourly` rows older than N days into a new `link_clicks_daily` table and prunes referrer rows past a retention window. The stats API reads hourly data for recent ranges and daily data for older ones.
    - **Step 2 (only if raw clicks must be queryable with low latency**, e.g. slicing by user agent or referrer path over any range): add a dedicated analytics store as a new `RollupStore`/query backend, fed by the same SQS events.
      - **ClickHouse** is preferred: materialized views roll up automatically, and it stores raw events efficiently. On AWS it would be ClickHouse Cloud.
      - **TimescaleDB** is second choice: continuous aggregates and retention policies, but it doesn't run on RDS, so it would mean Timescale Cloud or EC2.
      - See D14 for why neither is used now.

## 13. Testing Strategy

Written test-first (TDD).

| Layer | Scope | Approach |
|---|---|---|
| Unit (events lib) | `ClickEvent` model, schema export, SQS codec | Round-trip encode/decode; rejection of unknown versions |
| Unit (api) | `codes.py`, `urls.py`, `policy.py`, `ClickPublisher` buffering/batching/drop behavior | `policy.can` is tested with a table of cases covering every role × action × owned/not-owned × blocked/not-blocked combination (§6.1). Publisher tested with a fake SQS client (full buffer, partial batch failures, shutdown drain). |
| Unit (processor) | `aggregate()`, referrer-host normalization, hour bucketing | Pure-function tests, including late events and missing referrers |
| Integration (api) | Every endpoint, redirects publish events, stats read from rollups, block rules | `pytest` + `httpx.AsyncClient` (ASGI transport) against real Postgres via **testcontainers**; `InMemoryClickPublisher`. Auth: the test fixture generates an RSA keypair, overrides the JWKS provider, and mints tokens with any roles. |
| Integration (processor) | Consume → rollups → delete; invalid messages → DLQ; unknown links; transaction failure leaves messages on the queue | testcontainers for Postgres **and ElasticMQ** (generic container with the same `elasticmq.conf`) |
| Integration (admin) | Routes, session store, refresh, CSRF, HTMX fragments | API mocked with `respx`; real Postgres for `admin.sessions`; OIDC callback stubbed. |
| E2E smoke | The full compose stack | `make e2e` (pytest, `-m e2e`): real tokens via `shortener-dev` for seeded users. Scenario: eddie creates a link → erin gets `404` on it → following it returns `302` → **poll stats until total = 1 (timeout 15 s)** → alice blocks it → eddie's `PATCH` gets `409` → the redirect returns `410` and stats total stays 1 → victor can read it but gets `403` on create → nora gets `403`. |

**CI (GitHub Actions):** `ruff check`, `ruff format --check`, unit + integration tests for all packages, and Docker image builds. E2E runs locally only for now.

## 14. Success Criteria

- `make up` on a clean machine (Docker or colima) brings up all services healthy, with the realm, users, and queues provisioned, without manual steps.
- Logging in as each seeded user shows behavior matching §6.1.
- Following a short link produces a click that appears in the admin UI chart within a few seconds, with "data as of" shown.
- With `click-processor` stopped, redirects keep working and events pile up in the queue. Restarting the processor drains them into the rollups.
- An admin block is enforced as described in §4.3 and can't be bypassed by the owner.
- A UI action can be followed as a single trace in Grafana/Tempo, with correlated logs. A click's processing span links back to its redirect span. The Overview dashboard shows redirect and pipeline metrics.
- `make test` and `make e2e` pass; CI is green.
