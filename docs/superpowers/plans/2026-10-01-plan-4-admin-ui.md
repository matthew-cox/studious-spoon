# Plan 4 — Admin UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `admin` service, a server-rendered FastAPI + Jinja2 + HTMX UI on `:8001`:
- **Keycloak login:** Authorization Code + PKCE with a confidential client, and server-side sessions in `admin.sessions`.
- **CSRF protection** on every unsafe request.
- **Pages:** a dashboard; a links list with search, filter and paging; create, detail and edit; active toggle; delete with confirmation; admin block/unblock with a reason; a click chart with an hour/day selector and top referrers.

Every action goes through the API using the signed-in user's own token, so the API stays the authority.

**Architecture:** A new workspace package, `admin/` (`shortener_admin`).
- **Pure module `security.py`:** PKCE, random tokens, constant-time compare, and an open-redirect-safe `next` path.
- **Protocol-typed adapters with fakes:**
  - `SessionStore`: Postgres in `admin.sessions`, plus an in-memory fake that passes the same contract tests
  - `KeycloakOidc`: a thin adapter over **Authlib**'s Starlette client (discovery, PKCE S256, state, nonce, ID-token verification against JWKS, refresh), with errors normalized to `OidcError`
  - `ApiClient`: httpx against the API; problem+json becomes `ApiError`
- **App wiring:** `create_app(deps)` with the dependencies `require_session` (login redirect, transparent token refresh, `HX-Redirect` for HTMX), `require_access` (no-role users get a "no access" page) and `verify_csrf`.
- **Pages:** Jinja2 templates with Pico.css, htmx and Chart.js, all vendored under `static/` (no CDN).

**Tech Stack:** Python 3.12, FastAPI, Jinja2, htmx 2, Pico.css 2, Chart.js 4, **Authlib 1.8** (Starlette client), Starlette `SessionMiddleware` (itsdangerous) for the login handshake only, httpx, python-multipart, and SQLAlchemy 2 async with psycopg 3. Tests use pytest with respx for Keycloak and the API, testcontainers Postgres through the `shortener_testing` plugin, and an HTTP-level e2e login through the real Keycloak login form.

**Spec:** `docs/superpowers/specs/2026-10-01-url-shortener-design.md`. Read §2 (D2, D7), §3.2–3.4, §4.5, §6, §6.1, §7.1, §7.3, §7.4, §8, §9 ("Admin UI" bullet), and §15 before starting.

**Plan series:** 1 Foundation, 2 API, 3 Click processor (done) → **4 Admin UI (this)** → 5 Observability (tracing + `trace_id` on error pages, JSON logs, dashboard), E2E, CI hardening.

**Scope split with Plan 5 (intentional):** the generic error page shows a plain message now. Showing the `trace_id` (spec §9) needs tracing, which comes in Plan 5. The admin service exports no custom metrics (spec §10 lists none).

**Suggested execution batches** (batched subagent-driven execution): (Tasks 1–3), (4–5), (6–8), (9).

**Deviations from the spec (intentional; Task 9 records them in the spec):**
1. **Roles come from the API's `GET /api/v1/me`,** not from decoding the access token. That keeps the API as the single authority (D2). Roles are re-read on every token refresh, so a role change takes effect within about one access-token lifetime (5 min).
2. **The pre-login handshake** (Authlib's `state`, `nonce` and PKCE verifier, plus our `next` path) lives in Starlette's signed-cookie `SessionMiddleware`. The cookie is named `login_state`, scoped to `Path=/auth`, and lasts 10 minutes. The real user session is still the server-side `sid` (D7); the handshake cookie never holds tokens. This adds one dev-only secret, `ADMIN_COOKIE_SECRET`.
3. **Logout is `POST /auth/logout`, CSRF-checked.** A GET logout would be cross-site triggerable.
4. **Tokens in `admin.sessions` are stored as plaintext.** That relies on database encryption at rest; RDS on AWS. Encrypting them at the application level with a KMS data key goes into §12 future work.

## Global Constraints

- Everything in Plans 1–3's Global Constraints still applies:
  - config only from the environment, failing fast
  - pinned images and pinned, vendored front-end assets
  - no SQLite; no `sleep` in unit/integration tests
  - fakes over mocks; `respx` only at the Keycloak and API HTTP boundaries
  - `mypy --strict` on `src/`
  - coverage ≥ 80% overall and ≥ 90% on pure modules
  - Run `uv run ruff format . && make check` before every commit, chained with `&&`, and never piped through `tail`/`head`. Never commit on a red gate.
- Commit trailer: the implementing model's own name, e.g. `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- **Pure module:** `shortener_admin/security.py` (stdlib only). Add it to `PURE_MODULES`.
- ruff `S101` forbids `assert` in `src/`. Use `typing.cast` or explicit raises.
- **Database user is `admin_user`.** It may touch only `admin.sessions` (spec §3.4); the admin service never reads link data from Postgres.
- **Cookies:**
  - `sid`: an opaque `secrets.token_urlsafe(32)`; `HttpOnly`, `SameSite=Lax`, `Path=/`, and `Secure` when `COOKIE_SECURE=true`
  - `login_state`: Starlette `SessionMiddleware`, signed; `HttpOnly`, `SameSite=Lax`, `Path=/auth`, `Max-Age=600`; it holds only Authlib's handshake data and `next`
  - `sid` is cleared on logout; the handshake data is cleared on every callback, success or failure.
- **OIDC (Authlib):**
  - **Registration:** `server_metadata_url={OIDC_INTERNAL_URL}/.well-known/openid-configuration`. Verified against the live stack: fetched over the internal hostname, Keycloak's discovery returns the browser-facing `issuer`, `authorization_endpoint` and `end_session_endpoint` (`localhost:{KEYCLOAK_HOST_PORT}`) and the internal `token_endpoint` and `jwks_uri` (`keycloak:8080`), thanks to `KC_HOSTNAME_BACKCHANNEL_DYNAMIC`.
  - **Login request:** `scope=openid`, `code_challenge_method=S256`, and `client_secret_basic` token auth (the Authlib default).
  - **Redirect URI:** `{PUBLIC_BASE_URL}/auth/callback`.
  - **ID-token checks:** Authlib's signature, `iss` (== discovery issuer), `aud` (== `shortener-admin`), `nonce` and `exp`, with Authlib's default 120 s leeway. An HS256-signed ID token must be rejected (tested).
  - **Errors:** Authlib, joserfc and httpx errors are normalized to `OidcError` in one place (`oidc.py`). Routes never see library exceptions.
  - **Pin `authlib>=1.8,<2`.** Authlib 1.8 emits `AuthlibDeprecationWarning` about moving its httpx integration to `httpx2`. Silence that one warning class in pytest `filterwarnings`, and record the upgrade in spec §12.
- **Session lifecycle:**
  - Refresh when the access token expires within 30 s.
  - If a refresh fails (or `/me` returns 401), delete the session and send the user to login with a "session expired" message.
  - A session past `refresh_expires_at` counts as gone.
- **CSRF:**
  - The per-session token is required on `POST`/`PUT`/`PATCH`/`DELETE`, read from the `X-CSRF-Token` header or a `csrf_token` form field.
  - Compare with `hmac.compare_digest`; a mismatch returns 403.
  - The `<body>` carries `hx-headers='{"X-CSRF-Token": "..."}'`, and every form has a hidden field.
- **HTMX:**
  - A request carrying `HX-Request: true` gets a partial.
  - An unauthenticated HTMX request gets `200` with `HX-Redirect: /auth/login?next=…`, never a 302 into a fragment.
- **Redirects after login** only go to same-origin relative paths (`security.safe_next_path`). Anything else goes to `/`.
- **Jinja2 autoescape stays on.** Never use `|safe` on user- or API-supplied data.
- **API errors:** `403`/`404`/`409`/`422` from the API are shown inline (the 409 includes the block reason). `401` means the session is stale, so it goes through the session-expired flow.
- **Front-end assets** are pinned and vendored into `admin/src/shortener_admin/static/vendor/`, with the SHA-256 of each file recorded in `VENDORED.md`:
  - htmx `2.0.4`
  - Pico.css `2.0.6`
  - Chart.js `4.4.7`
- **New workspace package `admin`:** add it to the root `members`, and copy its `pyproject.toml` in **every** Dockerfile's dependency layer (api, processor, keycloak-tools, admin).
- Environment for running tests:
  - integration tests on colima need `export DOCKER_HOST="unix://${HOME}/.colima/default/docker.sock" TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE=/var/run/docker.sock`
  - `export VIRTUAL_ENV=` (a stray venv is active)
  - Keycloak runs on host port `8180` locally (`.env`)
  - never run `make down`

## Review Focus

1. **Open redirect through `next`.** Values like `//evil.example`, `https://evil.example`, `/\evil.example` and `javascript:…`, and anything containing control characters, all go to `/` after login. Tests: Task 1 `test_security.py`; Task 5 `test_callback_ignores_an_offsite_next`.
2. **Callback tampering:**
   - the `login_state` handshake cookie missing, expired or forged
   - a `state` mismatch
   - a replayed or invalid `code` (the token endpoint returns 400)
   - an ID token with the wrong `nonce`, `iss` or `aud`, or a bad signature

   Each must give a 400 "login failed" page and create no session. Tests: Task 3 `test_oidc.py`; Task 5 `test_auth_routes.py`.
3. **CSRF.** An unsafe request with no token, a wrong token, or another session's token gets 403, and the API is never called. That includes logout. Tests: Task 5 `test_csrf_*` and Task 7 `test_post_without_csrf_never_reaches_the_api`.
4. **Expiry mid-flow:**
   - an access token about to expire is refreshed transparently
   - a revoked refresh token deletes the session and redirects to login
   - for an HTMX request, both happen through `HX-Redirect`, never by putting a login page inside a fragment

   Tests: Task 5 `test_session.py`.
5. **Escaping.** A block reason or target URL containing `<script>`, `"` or `'` renders inertly, in pages and in `data-*` attributes such as the chart JSON. Tests: Task 7 `test_detail_escapes_block_reason` and Task 8 `test_chart_data_attribute_is_escaped`.

---

## File Structure

```
admin/                                   NEW workspace member `shortener-admin`
  pyproject.toml, Dockerfile
  src/shortener_admin/
    __init__.py, py.typed
    settings.py          AdminSettings, load_admin_settings()
    security.py          (pure) new_token, pkce_pair, tokens_match, safe_next_path
    sessions.py          Session, TokenSet, SessionStore, PostgresSessionStore, InMemorySessionStore
    db.py                admin.sessions table (SQLAlchemy Core)
    oidc.py              OidcError, KeycloakOidc (Authlib adapter)
    api_client.py        ApiError, ApiClient
    deps.py              AdminDeps, get_deps
    auth.py              LoginRequired, require_session, require_access, verify_csrf, cookie helpers
    main.py              create_app(deps), build_deps(settings), create_app_from_env()
    routes/__init__.py, routes/auth.py, routes/pages.py, routes/links.py, routes/health.py
    templates/           base.html, login_failed.html, session_expired.html, no_access.html, error.html,
                         dashboard.html, links.html, link_new.html, link_detail.html,
                         partials/links_table.html, partials/link_stats.html
    static/app.js, static/app.css, static/vendor/{htmx.min.js,pico.min.css,chart.umd.js}, static/VENDORED.md
  tests/conftest.py, tests/unit/..., tests/integration/...
api/Dockerfile, processor/Dockerfile, tools/keycloak-tools/Dockerfile   + COPY admin/pyproject.toml
docker-compose.yml, Makefile, pyproject.toml (root), .env.example, .github/workflows/ci.yml
tests/e2e/test_admin.py                  HTTP-level login through Keycloak's real form + UI actions
README.md, spec                          docs
```

---

### Task 1: Admin package, settings, pure security helpers

**Files:**
- Create: `admin/pyproject.toml`, `admin/src/shortener_admin/{__init__,settings,security}.py`, `admin/src/shortener_admin/py.typed`
- Modify: root `pyproject.toml` (members; ruff `src`; coverage `source`; pytest `testpaths`), `Makefile` (`MYPY_TARGETS`, `PURE_MODULES`), and `api/Dockerfile`, `processor/Dockerfile`, `tools/keycloak-tools/Dockerfile` (dependency-layer COPY line)
- Test: `admin/tests/unit/test_settings.py`, `admin/tests/unit/test_security.py`

**Interfaces:**
- Produces:
  - `AdminSettings` with fields:
    - `database_url: PostgresDsn`
    - `api_base_url: AnyHttpUrl`
    - `public_base_url: AnyHttpUrl`
    - `oidc_internal_url: str` (no trailing slash; discovery, token and JWKS are reached through it)
    - `oidc_client_id: str = "shortener-admin"`
    - `oidc_client_secret: SecretStr`
    - `cookie_secret: SecretStr` (≥ 32 chars)
    - `cookie_secure: bool = False`
    - `http_timeout_seconds: float = 5.0`
    - `service_version: str = "dev"`
  - `AdminSettings.redirect_uri` (a property): `f"{public_base_url}/auth/callback"`, with any trailing slash on `public_base_url` stripped
  - `load_admin_settings() -> AdminSettings`
  - `security.new_token(nbytes: int = 32) -> str`
  - `security.pkce_pair() -> tuple[str, str]`, returning (verifier, S256 challenge)
  - `security.tokens_match(expected: str | None, supplied: str | None) -> bool`
  - `security.safe_next_path(raw: str | None, default: str = "/") -> str`

- [ ] **Step 1: Create the package and register it**

`admin/pyproject.toml`:
```toml
[project]
name = "shortener-admin"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "pydantic>=2.8",
  "pydantic-settings>=2.4",
  "fastapi>=0.115",
  "uvicorn[standard]>=0.30",
  "jinja2>=3.1",
  "python-multipart>=0.0.9",
  "itsdangerous>=2.2",
  "authlib>=1.8,<2",
  "httpx>=0.27",
  "sqlalchemy[asyncio]>=2.0.35",
  "psycopg[binary]>=3.2",
]

[build-system]
requires = ["hatchling>=1.25"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/shortener_admin"]
```
Create `admin/src/shortener_admin/__init__.py` with `"""Admin UI: server-rendered FastAPI + Jinja2 + HTMX over the API (spec §8)."""`, and an empty `py.typed`.

Root `pyproject.toml`:
- add `"admin"` to `[tool.uv.workspace] members`
- add `"admin/src"` to `[tool.ruff] src`
- add `"shortener_admin"` to `[tool.coverage.run] source`
- add `"admin"` to `[tool.pytest.ini_options] testpaths`

`Makefile`:
- append ` admin/src` to `MYPY_TARGETS`
- append `,*/shortener_admin/security.py` to `PURE_MODULES`

In **each** of `api/Dockerfile`, `processor/Dockerfile` and `tools/keycloak-tools/Dockerfile`, add `COPY admin/pyproject.toml admin/` next to the other member COPY lines in the dependency layer.

Run: `uv lock && uv sync --all-packages && docker build -q -f api/Dockerfile . && docker build -q -f processor/Dockerfile . && docker build -q -f tools/keycloak-tools/Dockerfile .`
Expected: everything succeeds.

- [ ] **Step 2: Write the failing settings tests**

`admin/tests/unit/test_settings.py`:
```python
import pytest
from pydantic import ValidationError

from shortener_admin.settings import load_admin_settings

REQUIRED = {
    "DATABASE_URL": "postgresql+psycopg://admin_user:pw@db:5432/shortener",
    "API_BASE_URL": "http://api:8000",
    "PUBLIC_BASE_URL": "http://localhost:8001",
    "OIDC_INTERNAL_URL": "http://keycloak:8080/realms/shortener",
    "OIDC_CLIENT_SECRET": "client-secret",
    "COOKIE_SECRET": "x" * 32,
}


@pytest.fixture
def env(monkeypatch):
    for name, value in REQUIRED.items():
        monkeypatch.setenv(name, value)
    return monkeypatch


@pytest.mark.parametrize("missing", sorted(REQUIRED))
def test_missing_required_variable_fails_fast_naming_it(env, missing):
    env.delenv(missing)
    with pytest.raises(ValidationError, match=missing.lower()):
        load_admin_settings()


def test_defaults_and_redirect_uri(env):
    env.setenv("PUBLIC_BASE_URL", "http://localhost:8001/")
    settings = load_admin_settings()
    assert settings.oidc_client_id == "shortener-admin"
    assert settings.cookie_secure is False
    assert settings.redirect_uri == "http://localhost:8001/auth/callback"


def test_short_cookie_secret_is_rejected(env):
    env.setenv("COOKIE_SECRET", "too-short")
    with pytest.raises(ValidationError, match="cookie_secret"):
        load_admin_settings()


def test_trailing_slash_internal_url_is_rejected(env):
    env.setenv("OIDC_INTERNAL_URL", REQUIRED["OIDC_INTERNAL_URL"] + "/")
    with pytest.raises(ValidationError, match="oidc_internal_url"):
        load_admin_settings()


def test_secrets_are_not_printed(env):
    assert "client-secret" not in repr(load_admin_settings())
```

Run: `uv run pytest admin/tests/unit/test_settings.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'shortener_admin.settings'`.

- [ ] **Step 3: Implement the settings**

`admin/src/shortener_admin/settings.py`:
```python
from pydantic import AnyHttpUrl, Field, PostgresDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_NO_TRAILING_SLASH = r"^https?://\S+[^/]$"


class AdminSettings(BaseSettings):
    """Configuration for the admin UI (environment only, spec §15.1 III)."""

    model_config = SettingsConfigDict(extra="ignore")

    database_url: PostgresDsn
    api_base_url: AnyHttpUrl
    public_base_url: AnyHttpUrl
    # Discovery is fetched here; the issuer the ID token must match comes from discovery itself.
    oidc_internal_url: str = Field(pattern=_NO_TRAILING_SLASH)
    oidc_client_id: str = "shortener-admin"
    oidc_client_secret: SecretStr
    cookie_secret: SecretStr = Field(min_length=32)
    cookie_secure: bool = False
    http_timeout_seconds: float = Field(default=5.0, gt=0)
    service_version: str = "dev"

    @property
    def redirect_uri(self) -> str:
        return f"{str(self.public_base_url).rstrip('/')}/auth/callback"


def load_admin_settings() -> AdminSettings:
    return AdminSettings()
```

Run: `uv run pytest admin/tests/unit/test_settings.py -q`
Expected: PASS.

- [ ] **Step 4: Write the failing security-helper tests**

`admin/tests/unit/test_security.py`:
```python
import base64
import hashlib

import pytest

from shortener_admin.security import new_token, pkce_pair, safe_next_path, tokens_match


def test_new_tokens_are_long_and_unique():
    tokens = {new_token() for _ in range(100)}
    assert len(tokens) == 100
    assert all(len(t) >= 43 for t in tokens)  # 32 bytes, base64url


def test_pkce_pair_is_rfc7636_s256():
    verifier, challenge = pkce_pair()
    assert 43 <= len(verifier) <= 128
    assert set(verifier) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == expected


@pytest.mark.parametrize(
    ("expected", "supplied", "result"),
    [("abc", "abc", True), ("abc", "abd", False), ("abc", None, False), (None, "abc", False),
     ("", "", False), ("abc", "", False)],
)  # fmt: skip
def test_tokens_match(expected, supplied, result):
    assert tokens_match(expected, supplied) is result


@pytest.mark.parametrize("path", ["/", "/links", "/links/abc?status=blocked&page=2", "/links#x"])
def test_safe_next_accepts_local_paths(path):
    assert safe_next_path(path) == path


@pytest.mark.parametrize(
    "raw",
    [
        None, "", "links", "//evil.example", "///evil.example", "/\\evil.example", "\\\\evil.example",
        "https://evil.example/", "http:/evil.example", "javascript:alert(1)", "/links\r\nSet-Cookie: x",
        "/\x00", " /links",
    ],
)  # fmt: skip
def test_safe_next_rejects_everything_else(raw):
    assert safe_next_path(raw) == "/"


def test_safe_next_custom_default():
    assert safe_next_path("//evil", default="/links") == "/links"
```

Run: `uv run pytest admin/tests/unit/test_security.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 5: Implement `security.py`**

`admin/src/shortener_admin/security.py`:
```python
"""Security primitives (pure, stdlib only): tokens, PKCE, constant-time compare, safe redirects."""

import base64
import hashlib
import hmac
import secrets
from urllib.parse import urlsplit


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def pkce_pair() -> tuple[str, str]:
    """(code_verifier, S256 code_challenge) per RFC 7636."""
    verifier = secrets.token_urlsafe(64)  # 86 chars of [A-Za-z0-9_-]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def tokens_match(expected: str | None, supplied: str | None) -> bool:
    if not expected or not supplied:
        return False
    return hmac.compare_digest(expected.encode(), supplied.encode())


def safe_next_path(raw: str | None, default: str = "/") -> str:
    """Only same-origin absolute paths survive; everything else becomes `default`."""
    if not raw or not raw.startswith("/") or raw.startswith(("//", "/\\")):
        return default
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in raw):
        return default
    parts = urlsplit(raw)
    if parts.scheme or parts.netloc:
        return default
    return raw
```

Run: `uv run pytest admin/tests/unit -q`
Expected: all PASS.

- [ ] **Step 6: Gates and commit**

```bash
uv run ruff format . && make check && git add admin pyproject.toml uv.lock Makefile api/Dockerfile processor/Dockerfile tools/keycloak-tools/Dockerfile && git commit -m "feat(admin): package, settings, and pure security helpers

Co-Authored-By: <implementing model> <noreply@anthropic.com>"
```
Expected: `make check` passes, with `security.py` at 100% branch coverage.

---
### Task 2: Server-side session store (Postgres + in-memory fake, one contract)

**Files:**
- Create: `admin/src/shortener_admin/{db,sessions}.py`
- Create: `admin/tests/integration/conftest.py`
- Modify: `libs/shortener-testing/src/shortener_testing/fixtures.py` (`reset_database` also truncates `admin.sessions`)
- Test: `admin/tests/integration/test_session_store_contract.py`

**Interfaces:**
- Consumes: `new_token` (Task 1); the `migrated` and `reset_database` fixtures (Plan 3 plugin).
- Produces:
  - `sessions.MANAGED_ROLES = frozenset({"admin", "editor", "viewer"})`
  - `TokenSet(access_token, refresh_token, id_token, access_expires_at: datetime, refresh_expires_at: datetime)`
  - `Session(id, sub, username, roles: frozenset[str], tokens: TokenSet, csrf_token, created_at, last_seen_at)`, with properties:
    - `has_access`
    - `is_admin`
    - `can_create` (admin or editor)
    - `roles_label` (sorted managed roles joined by ", ", or "no roles")
  - `SessionStore` (Protocol), with methods:
    - `create(*, sub, username, roles, tokens, now) -> Session`
    - `get(session_id, now) -> Session | None`. It deletes the session and returns `None` once `refresh_expires_at <= now`, and otherwise sets `last_seen_at = now`.
    - `update_tokens(session_id, *, tokens, roles, now) -> Session | None`
    - `delete(session_id) -> None` (idempotent)
    - `purge_expired(now) -> int`
    - `ping() -> bool`
  - `PostgresSessionStore(engine)` and `InMemorySessionStore()`

`id` and `csrf_token` are `security.new_token()` values. `roles` is stored as a sorted `text[]`.

- [ ] **Step 1: Make the shared reset also clear sessions**

In `libs/shortener-testing/src/shortener_testing/fixtures.py`, add this line to `reset_database`, after the `pipeline_status` update:
```python
        conn.execute("TRUNCATE admin.sessions")
```
Run: `uv run pytest api processor -q`
Expected: the same pass counts as before. The truncate is harmless for those suites.

- [ ] **Step 2: Write the failing contract tests (run against both stores)**

`admin/tests/integration/conftest.py`:
```python
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from shortener_testing.fixtures import PgServer


@pytest.fixture(autouse=True)
def _reset_database(reset_database: None) -> None:
    """Every admin integration test starts from the post-migration state."""


@pytest.fixture
async def admin_engine(migrated: PgServer) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(migrated.url("admin_user"))
    yield engine
    await engine.dispose()
```

`admin/tests/integration/test_session_store_contract.py`:
```python
from datetime import UTC, datetime, timedelta

import pytest

from shortener_admin.sessions import InMemorySessionStore, PostgresSessionStore, TokenSet

pytestmark = pytest.mark.integration
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def tokens(suffix="1", access_in=300, refresh_in=1800) -> TokenSet:
    return TokenSet(
        access_token=f"access-{suffix}",
        refresh_token=f"refresh-{suffix}",
        id_token=f"id-{suffix}",
        access_expires_at=NOW + timedelta(seconds=access_in),
        refresh_expires_at=NOW + timedelta(seconds=refresh_in),
    )


@pytest.fixture(params=["memory", "postgres"])
async def store(request):
    if request.param == "memory":
        return InMemorySessionStore()
    return PostgresSessionStore(request.getfixturevalue("admin_engine"))


async def make(store, roles=frozenset({"editor"}), **kw):
    return await store.create(sub="sub-eddie", username="eddie", roles=roles, tokens=tokens(**kw), now=NOW)


async def test_create_then_get(store):
    created = await make(store)
    assert len(created.id) >= 43 and len(created.csrf_token) >= 43
    assert created.id != created.csrf_token
    fetched = await store.get(created.id, NOW + timedelta(seconds=5))
    assert fetched is not None
    assert (fetched.sub, fetched.username, fetched.roles) == ("sub-eddie", "eddie", frozenset({"editor"}))
    assert fetched.tokens == created.tokens
    assert fetched.last_seen_at == NOW + timedelta(seconds=5)


async def test_unknown_session_is_none(store):
    assert await store.get("nope", NOW) is None


async def test_session_past_refresh_expiry_is_gone(store):
    created = await make(store, refresh_in=60)
    assert await store.get(created.id, NOW + timedelta(seconds=60)) is None
    assert await store.get(created.id, NOW) is None  # deleted, not merely hidden


async def test_update_tokens_and_roles(store):
    created = await make(store)
    updated = await store.update_tokens(
        created.id, tokens=tokens("2"), roles=frozenset({"viewer"}), now=NOW + timedelta(minutes=4)
    )
    assert updated is not None
    assert updated.tokens.access_token == "access-2"
    assert updated.roles == frozenset({"viewer"})
    assert updated.csrf_token == created.csrf_token  # stable for the session's life
    assert await store.update_tokens("nope", tokens=tokens(), roles=frozenset(), now=NOW) is None


async def test_delete_is_idempotent(store):
    created = await make(store)
    await store.delete(created.id)
    await store.delete(created.id)
    assert await store.get(created.id, NOW) is None


async def test_purge_expired(store):
    live = await make(store, refresh_in=3600)
    await make(store, refresh_in=10)
    await make(store, refresh_in=20)
    assert await store.purge_expired(NOW + timedelta(seconds=30)) == 2
    assert await store.get(live.id, NOW) is not None


async def test_role_helpers(store):
    admin = await make(store, roles=frozenset({"admin", "default-roles-shortener"}))
    nobody = await make(store, roles=frozenset({"offline_access"}))
    viewer = await make(store, roles=frozenset({"viewer"}))
    assert admin.is_admin and admin.can_create and admin.has_access
    assert admin.roles_label == "admin"
    assert not nobody.has_access and nobody.roles_label == "no roles"
    assert viewer.has_access and not viewer.can_create


async def test_ping(store):
    assert await store.ping() is True
```

Run (colima env exported): `uv run pytest admin/tests/integration -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'shortener_admin.sessions'`.

- [ ] **Step 3: Implement the table and the stores**

`admin/src/shortener_admin/db.py`:
```python
"""admin.sessions mirror (api/alembic owns the DDL). admin_user may touch nothing else."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

metadata = sa.MetaData()
TZ = sa.DateTime(timezone=True)

sessions = sa.Table(
    "sessions",
    metadata,
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("sub", sa.String(64), nullable=False),
    sa.Column("username", sa.String(255), nullable=False),
    sa.Column("roles", postgresql.ARRAY(sa.Text()), nullable=False),
    sa.Column("access_token", sa.Text(), nullable=False),
    sa.Column("refresh_token", sa.Text(), nullable=False),
    sa.Column("id_token", sa.Text(), nullable=False),
    sa.Column("access_expires_at", TZ, nullable=False),
    sa.Column("refresh_expires_at", TZ, nullable=False),
    sa.Column("csrf_token", sa.String(64), nullable=False),
    sa.Column("created_at", TZ, nullable=False),
    sa.Column("last_seen_at", TZ, nullable=False),
    schema="admin",
)
```

`admin/src/shortener_admin/sessions.py`:
```python
"""Server-side sessions (spec §4.5, D7). Tokens never reach the browser."""

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Protocol

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from shortener_admin.db import sessions as table
from shortener_admin.security import new_token

MANAGED_ROLES = frozenset({"admin", "editor", "viewer"})


@dataclass(frozen=True)
class TokenSet:
    access_token: str
    refresh_token: str
    id_token: str
    access_expires_at: datetime
    refresh_expires_at: datetime


@dataclass(frozen=True)
class Session:
    id: str
    sub: str
    username: str
    roles: frozenset[str]
    tokens: TokenSet
    csrf_token: str
    created_at: datetime
    last_seen_at: datetime

    @property
    def managed_roles(self) -> frozenset[str]:
        return self.roles & MANAGED_ROLES

    @property
    def has_access(self) -> bool:
        return bool(self.managed_roles)

    @property
    def is_admin(self) -> bool:
        return "admin" in self.roles

    @property
    def can_create(self) -> bool:
        return bool(self.roles & {"admin", "editor"})

    @property
    def roles_label(self) -> str:
        return ", ".join(sorted(self.managed_roles)) or "no roles"


class SessionStore(Protocol):
    async def create(
        self, *, sub: str, username: str, roles: frozenset[str], tokens: TokenSet, now: datetime
    ) -> Session: ...
    async def get(self, session_id: str, now: datetime) -> Session | None: ...
    async def update_tokens(
        self, session_id: str, *, tokens: TokenSet, roles: frozenset[str], now: datetime
    ) -> Session | None: ...
    async def delete(self, session_id: str) -> None: ...
    async def purge_expired(self, now: datetime) -> int: ...
    async def ping(self) -> bool: ...


def _new_session(
    *, sub: str, username: str, roles: frozenset[str], tokens: TokenSet, now: datetime
) -> Session:
    return Session(
        id=new_token(),
        sub=sub,
        username=username,
        roles=frozenset(roles),
        tokens=tokens,
        csrf_token=new_token(),
        created_at=now,
        last_seen_at=now,
    )


class InMemorySessionStore:
    """Test double with the same contract as PostgresSessionStore."""

    def __init__(self) -> None:
        self.sessions: dict[str, Session] = {}

    async def create(
        self, *, sub: str, username: str, roles: frozenset[str], tokens: TokenSet, now: datetime
    ) -> Session:
        session = _new_session(sub=sub, username=username, roles=roles, tokens=tokens, now=now)
        self.sessions[session.id] = session
        return session

    async def get(self, session_id: str, now: datetime) -> Session | None:
        session = self.sessions.get(session_id)
        if session is None:
            return None
        if session.tokens.refresh_expires_at <= now:
            del self.sessions[session_id]
            return None
        session = replace(session, last_seen_at=now)
        self.sessions[session_id] = session
        return session

    async def update_tokens(
        self, session_id: str, *, tokens: TokenSet, roles: frozenset[str], now: datetime
    ) -> Session | None:
        session = self.sessions.get(session_id)
        if session is None:
            return None
        session = replace(session, tokens=tokens, roles=frozenset(roles), last_seen_at=now)
        self.sessions[session_id] = session
        return session

    async def delete(self, session_id: str) -> None:
        self.sessions.pop(session_id, None)

    async def purge_expired(self, now: datetime) -> int:
        expired = [k for k, s in self.sessions.items() if s.tokens.refresh_expires_at <= now]
        for key in expired:
            del self.sessions[key]
        return len(expired)

    async def ping(self) -> bool:
        return True


def _from_row(row: Any) -> Session:
    m = row._mapping
    return Session(
        id=m["id"],
        sub=m["sub"],
        username=m["username"],
        roles=frozenset(m["roles"]),
        tokens=TokenSet(
            access_token=m["access_token"],
            refresh_token=m["refresh_token"],
            id_token=m["id_token"],
            access_expires_at=m["access_expires_at"],
            refresh_expires_at=m["refresh_expires_at"],
        ),
        csrf_token=m["csrf_token"],
        created_at=m["created_at"],
        last_seen_at=m["last_seen_at"],
    )


def _token_values(tokens: TokenSet) -> dict[str, Any]:
    return {
        "access_token": tokens.access_token,
        "refresh_token": tokens.refresh_token,
        "id_token": tokens.id_token,
        "access_expires_at": tokens.access_expires_at,
        "refresh_expires_at": tokens.refresh_expires_at,
    }


class PostgresSessionStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def create(
        self, *, sub: str, username: str, roles: frozenset[str], tokens: TokenSet, now: datetime
    ) -> Session:
        session = _new_session(sub=sub, username=username, roles=roles, tokens=tokens, now=now)
        async with self._engine.begin() as conn:
            await conn.execute(
                sa.insert(table).values(
                    id=session.id,
                    sub=sub,
                    username=username,
                    roles=sorted(session.roles),
                    csrf_token=session.csrf_token,
                    created_at=now,
                    last_seen_at=now,
                    **_token_values(tokens),
                )
            )
        return session

    async def get(self, session_id: str, now: datetime) -> Session | None:
        async with self._engine.begin() as conn:
            row = (
                await conn.execute(
                    sa.update(table)
                    .where(table.c.id == session_id, table.c.refresh_expires_at > now)
                    .values(last_seen_at=now)
                    .returning(*table.c)
                )
            ).one_or_none()
            if row is None:
                await conn.execute(sa.delete(table).where(table.c.id == session_id))
                return None
        return _from_row(row)

    async def update_tokens(
        self, session_id: str, *, tokens: TokenSet, roles: frozenset[str], now: datetime
    ) -> Session | None:
        async with self._engine.begin() as conn:
            row = (
                await conn.execute(
                    sa.update(table)
                    .where(table.c.id == session_id)
                    .values(roles=sorted(roles), last_seen_at=now, **_token_values(tokens))
                    .returning(*table.c)
                )
            ).one_or_none()
        return _from_row(row) if row is not None else None

    async def delete(self, session_id: str) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(sa.delete(table).where(table.c.id == session_id))

    async def purge_expired(self, now: datetime) -> int:
        async with self._engine.begin() as conn:
            result = await conn.execute(
                sa.delete(table).where(table.c.refresh_expires_at <= now).returning(table.c.id)
            )
            return len(result.all())

    async def ping(self) -> bool:
        try:
            async with self._engine.connect() as conn:
                await conn.execute(sa.text("SELECT 1"))
        except (SQLAlchemyError, OSError):
            return False
        return True
```

Run: `uv run pytest admin/tests/integration -q`
Expected: all PASS, for both `[memory]` and `[postgres]`.

- [ ] **Step 4: Gates and commit**

```bash
uv run ruff format . && make check && git add admin libs/shortener-testing && git commit -m "feat(admin): server-side session store (Postgres + in-memory, one contract)

Co-Authored-By: <implementing model> <noreply@anthropic.com>"
```

---

### Task 3: Keycloak OIDC adapter over Authlib

**Files:**
- Create: `admin/src/shortener_admin/oidc.py`
- Create: `admin/tests/conftest.py` (keys, ID-token minting, discovery document, Keycloak/API URLs, a fixed clock)
- Modify: root `pyproject.toml` (pytest `filterwarnings` for `AuthlibDeprecationWarning`)
- Test: `admin/tests/unit/test_oidc.py`

**Interfaces:**
- Consumes: `safe_next_path` (Task 1); `TokenSet` (Task 2); `AdminSettings` (Task 1).
- Produces:
  - `OidcError(Exception)`, which carries a short reason (logged, never shown to users)
  - `KeycloakOidc(settings: AdminSettings, *, clock: Callable[[], datetime])` with methods:
    - `async begin_login(request, next_path: str) -> Response`. It stores `safe_next_path(next_path)` in `request.session["next"]` and returns Authlib's `authorize_redirect` response (a 302 to Keycloak).
    - `async complete_login(request) -> tuple[TokenSet, str]`. It returns the tokens and the sanitized `next` path. It **always** clears `request.session` (success or failure) and raises `OidcError` on any problem: an `error` query parameter, missing or mismatched state, a code exchange failure, an invalid ID token, or a network error.
    - `async refresh(refresh_token: str, previous_id_token: str) -> TokenSet`
    - `async end_session_url(id_token_hint: str, post_logout_redirect_uri: str) -> str`, built from the discovery document's `end_session_endpoint`
  - Starlette `SessionMiddleware` is required on the app (Task 5 adds it with `session_cookie="login_state"`, `path="/auth"`, `max_age=600`, `same_site="lax"`, `https_only=settings.cookie_secure`).

**Token expiry:**
- `access_expires_at = clock() + expires_in`
- `refresh_expires_at = clock() + refresh_expires_in`; Keycloak sends this, and when it's 0 or missing, use the access expiry
- `id_token` falls back to `previous_id_token` when a refresh response omits it

- [ ] **Step 1: Shared test fixtures, the warning filter, and test-only dependencies**

Add `"pyjwt[crypto]>=2.9"` to the root `[dependency-groups] dev` list. The admin tests mint ID tokens with it; the admin package itself doesn't depend on PyJWT. Then run `uv lock && uv sync --all-packages`.

In root `pyproject.toml` `[tool.pytest.ini_options]`, add:
```toml
filterwarnings = [
  # Authlib 1.8 announces its httpx integration moving to `httpx2`; tracked in spec §12.
  "ignore::authlib.deprecate.AuthlibDeprecationWarning",
]
```
If pytest can't import that class path, run `uv run python -c "import authlib.deprecate as d; print(d.AuthlibDeprecationWarning)"` to find the class and use its actual import path. Don't widen the filter to every `DeprecationWarning`.

`admin/tests/conftest.py`:
```python
"""Shared admin test fixtures: RSA key, ID-token minting, discovery, URLs, a fixed clock."""

import json
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from shortener_admin.settings import AdminSettings

INTERNAL = "http://keycloak.test/realms/shortener"  # token, certs, discovery (container network)
ISSUER = "http://localhost:8080/realms/shortener"  # auth, logout, `iss` (browser-facing)
API = "http://api.test"
PUBLIC = "http://localhost:8001"
CLIENT_ID = "shortener-admin"
KID = "kc-key"
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


class FixedClock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock()


@pytest.fixture
def ids() -> dict[str, str]:
    """URL constants for tests (test modules can't import each other)."""
    return {"INTERNAL": INTERNAL, "ISSUER": ISSUER, "API": API, "PUBLIC": PUBLIC, "CLIENT_ID": CLIENT_ID}


@pytest.fixture
def settings() -> AdminSettings:
    return AdminSettings(
        database_url="postgresql+psycopg://admin_user:x@127.0.0.1:1/shortener",
        api_base_url=API,
        public_base_url=PUBLIC,
        oidc_internal_url=INTERNAL,
        oidc_client_secret="s3cret",
        cookie_secret="c" * 32,
    )


@pytest.fixture
def discovery() -> dict[str, Any]:
    """Shape of Keycloak's real discovery document as fetched over the internal hostname."""
    return {
        "issuer": ISSUER,
        "authorization_endpoint": f"{ISSUER}/protocol/openid-connect/auth",
        "token_endpoint": f"{INTERNAL}/protocol/openid-connect/token",
        "jwks_uri": f"{INTERNAL}/protocol/openid-connect/certs",
        "end_session_endpoint": f"{ISSUER}/protocol/openid-connect/logout",
        # Keycloak advertises many algorithms, HS256 included; HS256 must still be rejected.
        "id_token_signing_alg_values_supported": ["RS256", "HS256", "ES256"],
    }


@pytest.fixture(scope="session")
def signing_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def jwks_document(signing_key: rsa.RSAPrivateKey) -> dict[str, Any]:
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(signing_key.public_key()))
    return {"keys": [jwk | {"kid": KID, "use": "sig", "alg": "RS256"}]}


@pytest.fixture
def mint_id_token(signing_key: rsa.RSAPrivateKey) -> Callable[..., str]:
    def _mint(nonce: str, *, key: Any = None, algorithm: str = "RS256", **claims: Any) -> str:
        now = int(time.time())
        body = {"iss": ISSUER, "aud": CLIENT_ID, "sub": "sub-eddie", "nonce": nonce,
                "iat": now, "exp": now + 300, "azp": CLIENT_ID} | claims  # fmt: skip
        signer = key if key is not None else signing_key
        return jwt.encode(body, signer, algorithm=algorithm, headers={"kid": KID})

    return _mint


@pytest.fixture
def token_body(mint_id_token: Callable[..., str]) -> Callable[..., dict[str, Any]]:
    def _body(nonce: str, suffix: str = "1", **overrides: Any) -> dict[str, Any]:
        return {
            "access_token": f"access-{suffix}",
            "refresh_token": f"refresh-{suffix}",
            "id_token": mint_id_token(nonce),
            "expires_in": 300,
            "refresh_expires_in": 1800,
            "token_type": "Bearer",
        } | overrides

    return _body
```

- [ ] **Step 2: Write the failing adapter tests**

The adapter needs a real Starlette request with a session. The tests mount it in a tiny Starlette app with `SessionMiddleware` (the same cookie settings Task 5 uses) and drive it with httpx over ASGI. `respx` mocks only Keycloak; the in-process app host passes through.

`admin/tests/unit/test_oidc.py`:
```python
import base64
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from shortener_admin.oidc import KeycloakOidc, OidcError


def query(url: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


@pytest.fixture
def oidc(settings, clock):
    return KeycloakOidc(settings, clock=clock)


@pytest.fixture
def keycloak(ids, discovery, jwks_document):
    with respx.mock(assert_all_called=False) as router:
        router.route(host="admin.test").pass_through()
        router.get(f"{ids['INTERNAL']}/.well-known/openid-configuration").respond(json=discovery)
        router.get(f"{ids['INTERNAL']}/protocol/openid-connect/certs").respond(json=jwks_document)
        yield router


@pytest.fixture
async def app_client(oidc):
    async def login(request: Request):
        return await oidc.begin_login(request, request.query_params.get("next", "/"))

    async def callback(request: Request):
        try:
            tokens, next_path = await oidc.complete_login(request)
        except OidcError as exc:
            return JSONResponse({"error": str(exc), "session": dict(request.session)}, status_code=400)
        return JSONResponse({"access": tokens.access_token, "next": next_path,
                             "access_exp": tokens.access_expires_at.isoformat(),
                             "refresh_exp": tokens.refresh_expires_at.isoformat(),
                             "session": dict(request.session)})  # fmt: skip

    app = Starlette(
        routes=[Route("/auth/login", login), Route("/auth/callback", callback)],
        middleware=[Middleware(SessionMiddleware, secret_key="c" * 32, session_cookie="login_state",
                               path="/auth", max_age=600, same_site="lax", https_only=False)],
    )  # fmt: skip
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://admin.test") as client:
        yield client


async def start_login(app_client, next_path="/links"):
    response = await app_client.get("/auth/login", params={"next": next_path})
    assert response.status_code == 302
    return query(response.headers["location"]), response


async def test_login_redirects_to_keycloak_with_pkce(keycloak, app_client, ids):
    params, response = await start_login(app_client)
    assert response.headers["location"].startswith(f"{ids['ISSUER']}/protocol/openid-connect/auth?")
    assert params["response_type"] == "code"
    assert params["client_id"] == "shortener-admin"
    assert params["redirect_uri"] == "http://localhost:8001/auth/callback"
    assert params["scope"] == "openid"
    assert params["code_challenge_method"] == "S256"
    assert len(params["state"]) >= 20 and len(params["nonce"]) >= 20
    cookie = response.headers["set-cookie"]
    assert "login_state=" in cookie and "path=/auth" in cookie.lower()
    assert "httponly" in cookie.lower() and "samesite=lax" in cookie.lower()


async def test_callback_exchanges_code_and_returns_tokens(keycloak, app_client, ids, token_body, clock):
    params, _ = await start_login(app_client, "/links?page=2")
    token_route = keycloak.post(f"{ids['INTERNAL']}/protocol/openid-connect/token").respond(
        json=token_body(params["nonce"])
    )
    response = await app_client.get("/auth/callback", params={"code": "the-code", "state": params["state"]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["access"] == "access-1"
    assert body["next"] == "/links?page=2"
    assert body["access_exp"] == (clock() + timedelta(seconds=300)).isoformat()
    assert body["refresh_exp"] == (clock() + timedelta(seconds=1800)).isoformat()
    assert body["session"] == {}  # handshake data cleared
    request = token_route.calls.last.request
    form = query("?" + request.content.decode())
    assert form["grant_type"] == "authorization_code"
    assert form["code"] == "the-code"
    assert form["redirect_uri"] == "http://localhost:8001/auth/callback"
    assert len(form["code_verifier"]) >= 43
    assert request.headers["authorization"] == "Basic " + base64.b64encode(b"shortener-admin:s3cret").decode()


async def test_offsite_next_is_sanitized(keycloak, app_client, ids, token_body):
    params, _ = await start_login(app_client, "//evil.example/x")
    keycloak.post(f"{ids['INTERNAL']}/protocol/openid-connect/token").respond(json=token_body(params["nonce"]))
    response = await app_client.get("/auth/callback", params={"code": "c", "state": params["state"]})
    assert response.json()["next"] == "/"


async def test_callback_without_handshake_cookie_fails_without_calling_keycloak(keycloak, app_client, ids):
    token_route = keycloak.post(f"{ids['INTERNAL']}/protocol/openid-connect/token")
    response = await app_client.get("/auth/callback", params={"code": "c", "state": "s"})
    assert response.status_code == 400
    assert not token_route.called


async def test_state_mismatch_fails_and_clears_handshake(keycloak, app_client, ids):
    await start_login(app_client)
    token_route = keycloak.post(f"{ids['INTERNAL']}/protocol/openid-connect/token")
    response = await app_client.get("/auth/callback", params={"code": "c", "state": "forged"})
    assert response.status_code == 400
    assert response.json()["session"] == {}
    assert not token_route.called


async def test_keycloak_error_parameter_fails(keycloak, app_client):
    params, _ = await start_login(app_client)
    response = await app_client.get(
        "/auth/callback", params={"error": "access_denied", "state": params["state"]}
    )
    assert response.status_code == 400


async def test_rejected_code_fails(keycloak, app_client, ids):
    params, _ = await start_login(app_client)
    keycloak.post(f"{ids['INTERNAL']}/protocol/openid-connect/token").respond(
        400, json={"error": "invalid_grant", "error_description": "Code not valid"}
    )
    response = await app_client.get("/auth/callback", params={"code": "replayed", "state": params["state"]})
    assert response.status_code == 400


@pytest.mark.parametrize(
    "bad",
    [{"nonce": "other"}, {"iss": "http://keycloak:8080/realms/shortener"}, {"aud": "shortener-api"},
     {"exp": 1000}],
    ids=["nonce", "issuer", "audience", "expired"],
)  # fmt: skip
async def test_bad_id_token_fails(keycloak, app_client, ids, token_body, mint_id_token, bad):
    params, _ = await start_login(app_client)
    claims = dict(bad)
    nonce = claims.pop("nonce", params["nonce"])
    keycloak.post(f"{ids['INTERNAL']}/protocol/openid-connect/token").respond(
        json=token_body(params["nonce"], id_token=mint_id_token(nonce, **claims))
    )
    response = await app_client.get("/auth/callback", params={"code": "c", "state": params["state"]})
    assert response.status_code == 400


async def test_id_token_signed_by_another_key_fails(keycloak, app_client, ids, token_body, mint_id_token):
    params, _ = await start_login(app_client)
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    keycloak.post(f"{ids['INTERNAL']}/protocol/openid-connect/token").respond(
        json=token_body(params["nonce"], id_token=mint_id_token(params["nonce"], key=other))
    )
    response = await app_client.get("/auth/callback", params={"code": "c", "state": params["state"]})
    assert response.status_code == 400


async def test_hs256_id_token_fails_even_though_keycloak_advertises_hs256(keycloak, app_client, ids, token_body, mint_id_token):
    params, _ = await start_login(app_client)
    forged = mint_id_token(params["nonce"], key="guessable-secret-guessable-secret!", algorithm="HS256")
    keycloak.post(f"{ids['INTERNAL']}/protocol/openid-connect/token").respond(
        json=token_body(params["nonce"], id_token=forged)
    )
    response = await app_client.get("/auth/callback", params={"code": "c", "state": params["state"]})
    assert response.status_code == 400


async def test_refresh_returns_new_tokens_and_keeps_old_id_token(keycloak, oidc, ids):
    keycloak.post(f"{ids['INTERNAL']}/protocol/openid-connect/token").respond(
        json={"access_token": "access-2", "refresh_token": "refresh-2", "expires_in": 300,
              "refresh_expires_in": 1800, "token_type": "Bearer"}
    )  # fmt: skip
    tokens = await oidc.refresh("refresh-1", "old-id-token")
    assert (tokens.access_token, tokens.refresh_token, tokens.id_token) == ("access-2", "refresh-2", "old-id-token")


async def test_refresh_rejected_is_oidc_error(keycloak, oidc, ids):
    keycloak.post(f"{ids['INTERNAL']}/protocol/openid-connect/token").respond(400, json={"error": "invalid_grant"})
    with pytest.raises(OidcError):
        await oidc.refresh("revoked", "id")


async def test_keycloak_unreachable_is_oidc_error(keycloak, oidc, ids):
    keycloak.post(f"{ids['INTERNAL']}/protocol/openid-connect/token").mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(OidcError):
        await oidc.refresh("r", "id")


async def test_end_session_url_comes_from_discovery(keycloak, oidc, ids):
    url = await oidc.end_session_url("the-id-token", "http://localhost:8001/")
    assert url.startswith(f"{ids['ISSUER']}/protocol/openid-connect/logout?")
    assert query(url) == {"id_token_hint": "the-id-token", "post_logout_redirect_uri": "http://localhost:8001/",
                          "client_id": "shortener-admin"}  # fmt: skip
```

Run: `uv run pytest admin/tests/unit/test_oidc.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'shortener_admin.oidc'`.

- [ ] **Step 3: Implement the adapter**

`admin/src/shortener_admin/oidc.py`:
```python
"""Keycloak login via Authlib (spec §7.4). This module is the only place that knows about
Authlib; every library error becomes OidcError."""

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, cast
from urllib.parse import urlencode

import httpx
from authlib.integrations.base_client import OAuthError
from authlib.integrations.starlette_client import OAuth
from joserfc.errors import JoseError
from starlette.requests import Request
from starlette.responses import Response

from shortener_admin.security import safe_next_path
from shortener_admin.sessions import TokenSet
from shortener_admin.settings import AdminSettings

logger = logging.getLogger(__name__)
_LIBRARY_ERRORS = (OAuthError, JoseError, httpx.HTTPError, KeyError, ValueError, TypeError)


class OidcError(Exception):
    """Login or refresh failed. The reason is logged; users see a generic message."""


class KeycloakOidc:
    def __init__(self, settings: AdminSettings, *, clock: Callable[[], datetime]) -> None:
        self._clock = clock
        self._client_id = settings.oidc_client_id
        self._redirect_uri = settings.redirect_uri
        oauth = OAuth()
        oauth.register(
            name="keycloak",
            client_id=settings.oidc_client_id,
            client_secret=settings.oidc_client_secret.get_secret_value(),
            server_metadata_url=f"{settings.oidc_internal_url}/.well-known/openid-configuration",
            client_kwargs={
                "scope": "openid",
                "code_challenge_method": "S256",
                "timeout": settings.http_timeout_seconds,
            },
        )
        self._app = cast(Any, oauth.create_client("keycloak"))

    def _token_set(self, token: dict[str, Any], previous_id_token: str = "") -> TokenSet:
        now = self._clock()
        access_in = int(token.get("expires_in") or 0)
        refresh_in = int(token.get("refresh_expires_in") or access_in)
        return TokenSet(
            access_token=str(token["access_token"]),
            refresh_token=str(token["refresh_token"]),
            id_token=str(token.get("id_token") or previous_id_token),
            access_expires_at=now + timedelta(seconds=access_in),
            refresh_expires_at=now + timedelta(seconds=refresh_in),
        )

    async def begin_login(self, request: Request, next_path: str) -> Response:
        request.session["next"] = safe_next_path(next_path)
        try:
            return cast(Response, await self._app.authorize_redirect(request, self._redirect_uri))
        except _LIBRARY_ERRORS as exc:
            raise OidcError(f"cannot start login: {exc}") from exc

    async def complete_login(self, request: Request) -> tuple[TokenSet, str]:
        next_path = safe_next_path(request.session.get("next"))
        try:
            token = await self._app.authorize_access_token(request)
            if "userinfo" not in token:  # Authlib only sets it after validating the ID token
                raise OidcError("no validated id token in response")
            return self._token_set(dict(token)), next_path
        except _LIBRARY_ERRORS as exc:
            logger.warning("login callback rejected: %s", exc)
            raise OidcError(str(exc)) from exc
        finally:
            request.session.clear()

    async def refresh(self, refresh_token: str, previous_id_token: str) -> TokenSet:
        try:
            token = await self._app.fetch_access_token(
                grant_type="refresh_token", refresh_token=refresh_token
            )
            return self._token_set(dict(token), previous_id_token)
        except _LIBRARY_ERRORS as exc:
            raise OidcError(f"refresh failed: {exc}") from exc

    async def end_session_url(self, id_token_hint: str, post_logout_redirect_uri: str) -> str:
        try:
            metadata = await self._app.load_server_metadata()
            endpoint = str(metadata["end_session_endpoint"])
        except _LIBRARY_ERRORS as exc:
            raise OidcError(f"cannot build logout url: {exc}") from exc
        params = {
            "id_token_hint": id_token_hint,
            "post_logout_redirect_uri": post_logout_redirect_uri,
            "client_id": self._client_id,
        }
        return f"{endpoint}?{urlencode(params)}"
```
Notes:
- **`authorize_access_token`'s exception types depend on the library version.** If a test shows an exception type not in `_LIBRARY_ERRORS` reaching the test route (for example a joserfc error that doesn't subclass `JoseError`, or `authlib.jose` errors), add that **specific** class to the tuple. Never use a bare `except Exception`.
- **HS256 rejection:** if Authlib accepts the HS256 token because the discovery document advertises HS256, pin the allowed algorithms. Register with `server_metadata={"id_token_signing_alg_values_supported": ["RS256"]}`, or override `load_server_metadata` so that key is forced to `["RS256"]` after loading. Then confirm `test_hs256_id_token_fails_even_though_keycloak_advertises_hs256` passes. This is a **security requirement**, not an optional test.
- **mypy:** `cast(Any, …)` contains Authlib's untyped client. If mypy reports `import-untyped` for `authlib`, add `[[tool.mypy.overrides]] module = ["authlib.*"] ignore_missing_imports = true` to the root `pyproject.toml`.

Run: `uv run pytest admin/tests -q`
Expected: all PASS.

- [ ] **Step 4: Gates and commit**

```bash
uv run ruff format . && make check && git add admin pyproject.toml uv.lock && git commit -m "feat(admin): Keycloak OIDC via Authlib (PKCE, ID-token checks, refresh, logout URL)

Co-Authored-By: <implementing model> <noreply@anthropic.com>"
```

---
### Task 4: API client (token relay, problem+json → ApiError)

**Files:**
- Create: `admin/src/shortener_admin/api_client.py`
- Test: `admin/tests/unit/test_api_client.py`

**Interfaces:**
- Produces:
  - `ApiError(Exception)` with `.status: int`, `.title: str`, `.detail: str | None`, and `.extra: dict[str, Any]` (e.g. `blocked_reason`)
  - `ApiClient(http: httpx.AsyncClient)`, where `http` has `base_url` set to the API. Every method takes the caller's access token first, sends `Authorization: Bearer <token>`, and returns the decoded JSON (or `None` for 204):
    - `me(token)`
    - `summary(token)`
    - `list_links(token, *, q=None, status=None, page=1, page_size=20)`
    - `create_link(token, target_url)`
    - `get_link(token, link_id)`
    - `update_link(token, link_id, *, target_url=None, is_active=None)`, which sends only the fields that aren't `None`
    - `delete_link(token, link_id)`
    - `block_link(token, link_id, reason)`
    - `unblock_link(token, link_id)`
    - `link_stats(token, link_id, *, bucket="hour", start: datetime | None = None, end: datetime | None = None)`, which sends `from`/`to` as ISO-8601
- Error mapping:
  - **Transport failures** (connect errors, timeouts) become `ApiError(503, "API unavailable")`.
  - **A 4xx/5xx** becomes `ApiError(status, title, detail, extra)`. `title` and `detail` come from the problem+json body, falling back to the HTTP reason phrase, and `extra` is every other body key except `type`, `title`, `status` and `detail`.

- [ ] **Step 1: Write the failing tests**

`admin/tests/unit/test_api_client.py`:
```python
import json
from datetime import UTC, datetime

import httpx
import pytest
import respx

from shortener_admin.api_client import ApiClient, ApiError

TOKEN = "tok"


@pytest.fixture
async def api(ids):
    http = httpx.AsyncClient(base_url=ids["API"])
    yield ApiClient(http)
    await http.aclose()


@respx.mock
async def test_sends_bearer_token_and_returns_json(api, ids):
    route = respx.get(f"{ids['API']}/api/v1/me").respond(json={"sub": "s", "username": "u", "roles": []})
    assert (await api.me(TOKEN))["username"] == "u"
    assert route.calls.last.request.headers["authorization"] == "Bearer tok"


@respx.mock
async def test_list_links_passes_only_given_filters(api, ids):
    route = respx.get(f"{ids['API']}/api/v1/links").respond(json={"items": [], "total": 0, "page": 2, "page_size": 20})
    await api.list_links(TOKEN, q="exa", page=2)
    assert dict(route.calls.last.request.url.params) == {"q": "exa", "page": "2", "page_size": "20"}


@respx.mock
async def test_update_sends_only_provided_fields(api, ids):
    route = respx.patch(f"{ids['API']}/api/v1/links/L1").respond(json={"id": "L1"})
    await api.update_link(TOKEN, "L1", is_active=False)
    assert json.loads(route.calls.last.request.content) == {"is_active": False}


@respx.mock
async def test_delete_returns_none_on_204(api, ids):
    respx.delete(f"{ids['API']}/api/v1/links/L1").respond(204)
    assert await api.delete_link(TOKEN, "L1") is None


@respx.mock
async def test_stats_sends_iso_range(api, ids):
    route = respx.get(f"{ids['API']}/api/v1/links/L1/stats").respond(json={"total": 0})
    await api.link_stats(TOKEN, "L1", bucket="day", start=datetime(2026, 9, 1, tzinfo=UTC))
    assert dict(route.calls.last.request.url.params) == {"bucket": "day", "from": "2026-09-01T00:00:00+00:00"}


@respx.mock
async def test_problem_json_becomes_api_error_with_extras(api, ids):
    respx.patch(f"{ids['API']}/api/v1/links/L1").respond(
        409,
        json={"type": "about:blank", "title": "Link is blocked", "status": 409,
              "detail": "Blocked by an administrator: spam", "blocked_reason": "spam"},
        headers={"content-type": "application/problem+json"},
    )  # fmt: skip
    with pytest.raises(ApiError) as caught:
        await api.update_link(TOKEN, "L1", target_url="https://x.example")
    error = caught.value
    assert (error.status, error.title, error.detail) == (409, "Link is blocked", "Blocked by an administrator: spam")
    assert error.extra == {"blocked_reason": "spam"}


@respx.mock
async def test_non_json_error_falls_back_to_reason_phrase(api, ids):
    respx.get(f"{ids['API']}/api/v1/me").respond(502, text="<html>bad gateway</html>")
    with pytest.raises(ApiError) as caught:
        await api.me(TOKEN)
    assert (caught.value.status, caught.value.title) == (502, "Bad Gateway")


@respx.mock
async def test_transport_failure_is_503(api, ids):
    respx.get(f"{ids['API']}/api/v1/stats/summary").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(ApiError) as caught:
        await api.summary(TOKEN)
    assert caught.value.status == 503
    assert caught.value.title == "API unavailable"


@respx.mock
async def test_block_and_create_bodies(api, ids):
    block = respx.post(f"{ids['API']}/api/v1/links/L1/block").respond(json={"id": "L1"})
    create = respx.post(f"{ids['API']}/api/v1/links").respond(201, json={"id": "L2"})
    await api.block_link(TOKEN, "L1", "phishing")
    await api.create_link(TOKEN, "https://example.com")
    assert json.loads(block.calls.last.request.content) == {"reason": "phishing"}
    assert json.loads(create.calls.last.request.content) == {"target_url": "https://example.com"}
```

Run: `uv run pytest admin/tests/unit/test_api_client.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 2: Implement the client**

`admin/src/shortener_admin/api_client.py`:
```python
"""Calls the API with the signed-in user's own token (token relay, spec §8). The API decides."""

from datetime import datetime
from http import HTTPStatus
from typing import Any

import httpx

_CORE_PROBLEM_KEYS = {"type", "title", "status", "detail"}


class ApiError(Exception):
    def __init__(self, status: int, title: str, detail: str | None = None, extra: dict[str, Any] | None = None):
        super().__init__(f"{status} {title}")
        self.status = status
        self.title = title
        self.detail = detail
        self.extra = extra or {}


def _phrase(status: int) -> str:
    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return "Error"


class ApiClient:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def _call(
        self,
        method: str,
        path: str,
        token: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        try:
            response = await self._http.request(
                method, path, params=params, json=json, headers={"Authorization": f"Bearer {token}"}
            )
        except httpx.HTTPError as exc:
            raise ApiError(503, "API unavailable", str(exc)) from exc
        if response.status_code >= 400:
            try:
                body = response.json()
            except ValueError:
                body = {}
            if not isinstance(body, dict):
                body = {}
            raise ApiError(
                response.status_code,
                str(body.get("title") or _phrase(response.status_code)),
                body.get("detail"),
                {k: v for k, v in body.items() if k not in _CORE_PROBLEM_KEYS},
            )
        if response.status_code == 204:
            return None
        return response.json()

    async def me(self, token: str) -> Any:
        return await self._call("GET", "/api/v1/me", token)

    async def summary(self, token: str) -> Any:
        return await self._call("GET", "/api/v1/stats/summary", token)

    async def list_links(
        self, token: str, *, q: str | None = None, status: str | None = None, page: int = 1, page_size: int = 20
    ) -> Any:
        params: dict[str, Any] = {"page": page, "page_size": page_size}
        if q:
            params["q"] = q
        if status:
            params["status"] = status
        return await self._call("GET", "/api/v1/links", token, params=params)

    async def create_link(self, token: str, target_url: str) -> Any:
        return await self._call("POST", "/api/v1/links", token, json={"target_url": target_url})

    async def get_link(self, token: str, link_id: str) -> Any:
        return await self._call("GET", f"/api/v1/links/{link_id}", token)

    async def update_link(
        self, token: str, link_id: str, *, target_url: str | None = None, is_active: bool | None = None
    ) -> Any:
        body: dict[str, Any] = {}
        if target_url is not None:
            body["target_url"] = target_url
        if is_active is not None:
            body["is_active"] = is_active
        return await self._call("PATCH", f"/api/v1/links/{link_id}", token, json=body)

    async def delete_link(self, token: str, link_id: str) -> Any:
        return await self._call("DELETE", f"/api/v1/links/{link_id}", token)

    async def block_link(self, token: str, link_id: str, reason: str) -> Any:
        return await self._call("POST", f"/api/v1/links/{link_id}/block", token, json={"reason": reason})

    async def unblock_link(self, token: str, link_id: str) -> Any:
        return await self._call("POST", f"/api/v1/links/{link_id}/unblock", token)

    async def link_stats(
        self,
        token: str,
        link_id: str,
        *,
        bucket: str = "hour",
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> Any:
        params: dict[str, Any] = {"bucket": bucket}
        if start is not None:
            params["from"] = start.isoformat()
        if end is not None:
            params["to"] = end.isoformat()
        return await self._call("GET", f"/api/v1/links/{link_id}/stats", token, params=params)
```
Link IDs come from the API's own responses or our own routes. Task 7's routes validate them as UUIDs before they're interpolated into paths.

Run: `uv run pytest admin/tests/unit/test_api_client.py -q`
Expected: PASS.

- [ ] **Step 3: Gates and commit**

```bash
uv run ruff format . && make check && git add admin && git commit -m "feat(admin): API client with token relay and problem+json errors

Co-Authored-By: <implementing model> <noreply@anthropic.com>"
```

---

### Task 5: App skeleton — auth routes, session refresh, CSRF, base templates, vendored assets

**Files:**
- Create: `admin/src/shortener_admin/{deps,auth,views,main}.py`, `admin/src/shortener_admin/routes/{__init__,auth,health}.py`
- Create: `admin/src/shortener_admin/templates/{base,login_failed,session_expired,no_access,error}.html`
- Create: `admin/src/shortener_admin/static/{app.js,app.css,VENDORED.md}`, `admin/src/shortener_admin/static/vendor/{htmx.min.js,pico.min.css,chart.umd.js}`
- Create: `admin/tests/unit/conftest.py` (app, client, mocks, login helper)
- Test: `admin/tests/unit/test_auth_routes.py`, `admin/tests/unit/test_session.py`, `admin/tests/unit/test_csrf.py`, `admin/tests/unit/test_app_basics.py`

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces:
  - `deps.AdminDeps(settings, sessions: SessionStore, oidc: KeycloakOidc, api: ApiClient, clock: Callable[[], datetime], templates: Jinja2Templates)` (kw-only)
  - `deps.get_deps(request) -> AdminDeps`
  - `auth.SID_COOKIE = "sid"`
  - Exceptions `auth.LoginRequired(next_path, expired=False)`, `auth.NoAccess` and `auth.CsrfFailed`
  - FastAPI dependencies:
    - `auth.require_session`: loads the session, refreshes it when the access token expires within 30 s, sets `request.state.session`, and raises `LoginRequired`
    - `auth.require_access`: like `require_session`, plus `NoAccess` for users without a managed role
    - `auth.verify_csrf`: like `require_access`; on unsafe methods it also checks the `X-CSRF-Token` header or the `csrf_token` form field and raises `CsrfFailed`
  - `auth.set_sid_cookie(response, session_id, settings)`
  - `views.render(request, template, *, status_code=200, **context) -> HTMLResponse`, which always passes `session` from `request.state`
  - `views.is_htmx(request) -> bool`
  - `main.create_app(deps) -> FastAPI`. It wires `SessionMiddleware` (the handshake cookie), `/static`, the routers, and handlers for `LoginRequired` (303, or 200 + `HX-Redirect`), `NoAccess` (403 page), `CsrfFailed` (403 page), `ApiError` (401 goes to the expired-login flow, others to an error page with the API's status) and `Exception` (500 page).
  - Routes:
    - `GET /auth/login` (`?next=`, `?expired=1`)
    - `GET /auth/callback`
    - `POST /auth/logout`
    - `GET /healthz`, `GET /readyz`

- [ ] **Step 1: Vendor the front-end assets (pinned, hashed)**

Run from the repo root:
```bash
V=admin/src/shortener_admin/static/vendor
mkdir -p "$V"
curl -fsSLo "$V/htmx.min.js"  https://unpkg.com/htmx.org@2.0.4/dist/htmx.min.js
curl -fsSLo "$V/pico.min.css" https://unpkg.com/@picocss/pico@2.0.6/css/pico.min.css
curl -fsSLo "$V/chart.umd.js" https://unpkg.com/chart.js@4.4.7/dist/chart.umd.js
(cd "$V" && shasum -a 256 htmx.min.js pico.min.css chart.umd.js)
```
Expected: three non-empty files (`curl -f` fails loudly on a 404). Write `admin/src/shortener_admin/static/VENDORED.md`:
````markdown
# Vendored front-end assets (no CDN at runtime, spec §8)

| File | Version | Source | SHA-256 |
|---|---|---|---|
| vendor/htmx.min.js | 2.0.4 | https://unpkg.com/htmx.org@2.0.4/dist/htmx.min.js | `<paste>` |
| vendor/pico.min.css | 2.0.6 | https://unpkg.com/@picocss/pico@2.0.6/css/pico.min.css | `<paste>` |
| vendor/chart.umd.js | 4.4.7 | https://unpkg.com/chart.js@4.4.7/dist/chart.umd.js | `<paste>` |

Upgrade: download the new version, update the table, and run `make check`
(`test_vendored_assets_match_recorded_hashes` verifies every row).
````
Replace each `<paste>` with the hash `shasum` printed. The integrity test in Step 2 fails until they match.

`admin/src/shortener_admin/static/app.css`:
```css
.badge { padding: 0.1rem 0.5rem; border-radius: 0.5rem; font-size: 0.8rem; }
.badge.active { background: #d1fae5; color: #065f46; }
.badge.disabled { background: #e5e7eb; color: #374151; }
.badge.blocked { background: #fee2e2; color: #991b1b; }
form.inline { display: inline; margin: 0; }
.muted { color: var(--pico-muted-color); }
td.target { max-width: 28rem; overflow-wrap: anywhere; }
```

`admin/src/shortener_admin/static/app.js`:
```javascript
// Charts read their data from a data-chart JSON attribute (no inline scripts); re-render after HTMX swaps.
(function () {
  function renderCharts(root) {
    root.querySelectorAll("canvas[data-chart]").forEach(function (canvas) {
      if (canvas._chart) { canvas._chart.destroy(); }
      var data = JSON.parse(canvas.dataset.chart);
      canvas._chart = new Chart(canvas, {
        type: "bar",
        data: { labels: data.labels, datasets: [{ label: "Clicks", data: data.counts }] },
        options: { animation: false, plugins: { legend: { display: false } },
                   scales: { y: { beginAtZero: true, ticks: { precision: 0 } } } },
      });
    });
  }
  function wireCopyButtons(root) {
    root.querySelectorAll("button[data-copy]").forEach(function (button) {
      button.addEventListener("click", function () {
        navigator.clipboard.writeText(button.dataset.copy);
        button.textContent = "Copied";
      });
    });
  }
  document.addEventListener("DOMContentLoaded", function () { renderCharts(document); wireCopyButtons(document); });
  document.addEventListener("htmx:afterSwap", function (event) { renderCharts(event.target); wireCopyButtons(event.target); });
})();
```

- [ ] **Step 2: Write the shared app fixtures and the failing tests**

`admin/tests/unit/conftest.py`:
```python
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import timedelta
from typing import Any

import httpx
import pytest
import respx

from shortener_admin.api_client import ApiClient
from shortener_admin.deps import AdminDeps
from shortener_admin.main import create_app, make_templates
from shortener_admin.oidc import KeycloakOidc
from shortener_admin.sessions import InMemorySessionStore, Session, TokenSet


@pytest.fixture
def store() -> InMemorySessionStore:
    return InMemorySessionStore()


@pytest.fixture
def mocks(ids, discovery, jwks_document):
    """One respx router for both outside services: Keycloak (internal URL) and the API."""
    with respx.mock(assert_all_called=False) as router:
        router.route(host="localhost").pass_through()  # the app under test (ASGI)
        router.get(f"{ids['INTERNAL']}/.well-known/openid-configuration").respond(json=discovery)
        router.get(f"{ids['INTERNAL']}/protocol/openid-connect/certs").respond(json=jwks_document)
        yield router


@pytest.fixture
async def deps(settings, clock, store, ids) -> AsyncIterator[AdminDeps]:
    http = httpx.AsyncClient(base_url=ids["API"])
    yield AdminDeps(
        settings=settings,
        sessions=store,
        oidc=KeycloakOidc(settings, clock=clock),
        api=ApiClient(http),
        clock=clock,
        templates=make_templates(),
    )
    await http.aclose()


@pytest.fixture
async def app(deps):
    return create_app(deps)


@pytest.fixture
async def client(app, ids) -> AsyncIterator[httpx.AsyncClient]:
    # ServerErrorMiddleware re-raises after rendering the 500 page; let tests see the response.
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url=ids["PUBLIC"]) as http:
        yield http


@pytest.fixture
def login_as(store, client, clock) -> Callable[..., Awaitable[Session]]:
    async def _login(username: str = "eddie", roles: tuple[str, ...] = ("editor",), *,
                     access_in: int = 300, refresh_in: int = 1800) -> Session:  # fmt: skip
        tokens = TokenSet(
            access_token=f"access-{username}",
            refresh_token=f"refresh-{username}",
            id_token=f"id-{username}",
            access_expires_at=clock() + timedelta(seconds=access_in),
            refresh_expires_at=clock() + timedelta(seconds=refresh_in),
        )
        session = await store.create(sub=f"sub-{username}", username=username, roles=frozenset(roles),
                                     tokens=tokens, now=clock())  # fmt: skip
        client.cookies.set("sid", session.id)
        return session

    return _login


@pytest.fixture
def me_route(mocks, ids) -> Callable[..., Any]:
    def _route(username: str = "eddie", roles: tuple[str, ...] = ("editor",), status: int = 200) -> Any:
        body = {"sub": f"sub-{username}", "username": username, "roles": list(roles)}
        return mocks.get(f"{ids['API']}/api/v1/me").respond(status, json=body)

    return _route
```

`admin/tests/unit/test_auth_routes.py`:
```python
from urllib.parse import parse_qs, urlsplit


def q(url):
    return {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}


async def login_round_trip(client, mocks, ids, token_body, next_path="/links"):
    started = await client.get("/auth/login", params={"next": next_path})
    assert started.status_code == 302
    params = q(started.headers["location"])
    mocks.post(f"{ids['INTERNAL']}/protocol/openid-connect/token").respond(json=token_body(params["nonce"]))
    return await client.get("/auth/callback", params={"code": "c", "state": params["state"]})


async def test_login_redirects_to_keycloak(client, mocks, ids):
    response = await client.get("/auth/login", params={"next": "/links"})
    assert response.status_code == 302
    assert response.headers["location"].startswith(f"{ids['ISSUER']}/protocol/openid-connect/auth?")


async def test_callback_creates_session_and_redirects_to_next(client, mocks, ids, token_body, store, me_route):
    me_route("alice", ("admin",))
    response = await login_round_trip(client, mocks, ids, token_body, "/links?page=2")
    assert response.status_code == 303
    assert response.headers["location"] == "/links?page=2"
    cookie = response.headers["set-cookie"].lower()
    assert "sid=" in cookie and "httponly" in cookie and "samesite=lax" in cookie and "path=/" in cookie
    [session] = store.sessions.values()
    assert (session.username, session.roles) == ("alice", frozenset({"admin"}))
    assert session.tokens.access_token == "access-1"


async def test_callback_ignores_an_offsite_next(client, mocks, ids, token_body, me_route):
    me_route()
    response = await login_round_trip(client, mocks, ids, token_body, "https://evil.example/")
    assert response.headers["location"] == "/"


async def test_failed_callback_renders_400_and_creates_no_session(client, mocks, store):
    response = await client.get("/auth/callback", params={"code": "c", "state": "forged"})
    assert response.status_code == 400
    assert "Sign-in failed" in response.text
    assert store.sessions == {}


async def test_callback_fails_when_api_rejects_the_new_token(client, mocks, ids, token_body, store, me_route):
    me_route(status=401)
    response = await login_round_trip(client, mocks, ids, token_body)
    assert response.status_code == 400
    assert store.sessions == {}


async def test_new_login_replaces_an_existing_session(client, mocks, ids, token_body, store, me_route, login_as):
    old = await login_as()
    me_route()
    await login_round_trip(client, mocks, ids, token_body)
    assert old.id not in store.sessions
    assert len(store.sessions) == 1


async def test_session_expired_page(client):
    response = await client.get("/auth/login", params={"expired": "1", "next": "/links"})
    assert response.status_code == 200
    assert "session expired" in response.text.lower()
    assert 'href="/auth/login?next=/links"' in response.text


async def test_logout_requires_csrf(client, mocks, login_as, store):
    session = await login_as()
    response = await client.post("/auth/logout")
    assert response.status_code == 403
    assert session.id in store.sessions


async def test_logout_ends_session_and_redirects_to_keycloak(client, mocks, ids, login_as, store):
    session = await login_as()
    response = await client.post("/auth/logout", data={"csrf_token": session.csrf_token})
    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith(f"{ids['ISSUER']}/protocol/openid-connect/logout?")
    assert q(location)["id_token_hint"] == "id-eddie"
    assert session.id not in store.sessions
    assert 'sid=""' in response.headers["set-cookie"] or "max-age=0" in response.headers["set-cookie"].lower()
```

`admin/tests/unit/test_session.py`:
```python
from datetime import timedelta

import pytest
from fastapi import Depends

from shortener_admin.auth import require_access, require_session
from shortener_admin.deps import get_deps


@pytest.fixture
def probe(app):
    @app.get("/__probe")
    async def _probe(session=Depends(require_session)):
        return {"user": session.username, "access": session.tokens.access_token}

    @app.get("/__probe_access")
    async def _probe_access(session=Depends(require_access)):
        return {"user": session.username}

    return app


async def test_anonymous_page_request_redirects_to_login(probe, client):
    response = await client.get("/__probe?x=1")
    assert response.status_code == 303
    assert response.headers["location"] == "/auth/login?next=/__probe%3Fx%3D1"


async def test_anonymous_htmx_request_gets_hx_redirect_not_a_login_page(probe, client):
    response = await client.get("/__probe", headers={"HX-Request": "true"})
    assert response.status_code == 200
    assert response.headers["hx-redirect"].startswith("/auth/login?next=")
    assert response.text == ""


async def test_fresh_session_is_used_as_is(probe, client, login_as):
    await login_as()
    assert (await client.get("/__probe")).json() == {"user": "eddie", "access": "access-eddie"}


async def test_expiring_access_token_is_refreshed_and_roles_reread(probe, client, mocks, ids, login_as, store, me_route):
    session = await login_as(access_in=20)  # within the 30 s margin
    mocks.post(f"{ids['INTERNAL']}/protocol/openid-connect/token").respond(
        json={"access_token": "access-2", "refresh_token": "refresh-2", "expires_in": 300,
              "refresh_expires_in": 1800, "token_type": "Bearer"}
    )  # fmt: skip
    me_route("eddie", ("viewer",))
    assert (await client.get("/__probe")).json()["access"] == "access-2"
    assert store.sessions[session.id].roles == frozenset({"viewer"})
    assert store.sessions[session.id].tokens.id_token == "id-eddie"  # kept: refresh omitted it


@pytest.mark.parametrize("htmx", [False, True])
async def test_failed_refresh_deletes_session_and_sends_to_expired_login(probe, client, mocks, ids, login_as, store, htmx):
    session = await login_as(access_in=5)
    mocks.post(f"{ids['INTERNAL']}/protocol/openid-connect/token").respond(400, json={"error": "invalid_grant"})
    response = await client.get("/__probe", headers={"HX-Request": "true"} if htmx else {})
    assert session.id not in store.sessions
    location = response.headers["hx-redirect" if htmx else "location"]
    assert "expired=1" in location


async def test_session_past_refresh_expiry_requires_login(probe, client, login_as, clock):
    await login_as(refresh_in=60)
    clock.advance(timedelta(seconds=61))
    assert (await client.get("/__probe")).status_code == 303


async def test_user_without_roles_gets_no_access_page(probe, client, login_as):
    await login_as("nora", ("offline_access",))
    response = await client.get("/__probe_access")
    assert response.status_code == 403
    assert "don't have access" in response.text
    assert "Sign out" in response.text


async def test_api_401_mid_request_ends_the_session(app, client, mocks, ids, login_as, store):
    @app.get("/__probe_api")
    async def _probe_api(session=Depends(require_access), deps=Depends(get_deps)):
        return await deps.api.summary(session.tokens.access_token)

    session = await login_as()
    mocks.get(f"{ids['API']}/api/v1/stats/summary").respond(401, json={"title": "Unauthorized", "status": 401})
    response = await client.get("/__probe_api")
    assert response.status_code == 303
    assert "expired=1" in response.headers["location"]
    assert session.id not in store.sessions
```
`admin/tests/unit/test_csrf.py`:
```python
import pytest
from fastapi import Depends

from shortener_admin.auth import verify_csrf


@pytest.fixture
def probe(app):
    @app.post("/__mutate")
    async def _mutate(session=Depends(verify_csrf)):
        return {"ok": True}

    return app


async def test_csrf_missing_is_403(probe, client, login_as):
    await login_as()
    response = await client.post("/__mutate")
    assert response.status_code == 403
    assert "reload" in response.text.lower()


async def test_csrf_wrong_is_403(probe, client, login_as):
    await login_as()
    assert (await client.post("/__mutate", headers={"X-CSRF-Token": "nope"})).status_code == 403


async def test_csrf_from_another_session_is_403(probe, client, login_as):
    other = await login_as("erin")
    await login_as("eddie")  # the sid cookie now points at eddie's session
    assert (await client.post("/__mutate", data={"csrf_token": other.csrf_token})).status_code == 403


async def test_csrf_header_is_accepted(probe, client, login_as):
    session = await login_as()
    assert (await client.post("/__mutate", headers={"X-CSRF-Token": session.csrf_token})).status_code == 200


async def test_csrf_form_field_is_accepted(probe, client, login_as):
    session = await login_as()
    assert (await client.post("/__mutate", data={"csrf_token": session.csrf_token})).status_code == 200
```

`admin/tests/unit/test_app_basics.py`:
```python
import hashlib
import re
from pathlib import Path

from fastapi import Depends, Request

import shortener_admin
from shortener_admin.auth import require_access
from shortener_admin.views import render

STATIC = Path(shortener_admin.__file__).parent / "static"


async def test_healthz(client):
    assert (await client.get("/healthz")).json() == {"status": "ok"}


async def test_readyz_reflects_the_session_store(client):
    assert (await client.get("/readyz")).status_code == 200  # in-memory store always pings


async def test_static_assets_are_served(client):
    for path in ("vendor/htmx.min.js", "vendor/pico.min.css", "vendor/chart.umd.js", "app.js", "app.css"):
        assert (await client.get(f"/static/{path}")).status_code == 200


def test_vendored_assets_match_recorded_hashes():
    table = (STATIC / "VENDORED.md").read_text()
    rows = re.findall(r"\| (vendor/\S+) \| [\d.]+ \| \S+ \| `([0-9a-f]{64})` \|", table)
    assert {name for name, _ in rows} == {"vendor/htmx.min.js", "vendor/pico.min.css", "vendor/chart.umd.js"}
    for name, digest in rows:
        assert hashlib.sha256((STATIC / name).read_bytes()).hexdigest() == digest, name


async def test_base_template_carries_csrf_in_hx_headers(app, client, login_as):
    @app.get("/__page")
    async def _page(request: Request, session=Depends(require_access)):
        return render(request, "no_access.html")

    session = await login_as()
    html = (await client.get("/__page")).text
    assert f'hx-headers=\'{{"X-CSRF-Token": "{session.csrf_token}"}}\'' in html
    assert f'name="csrf_token" value="{session.csrf_token}"' in html  # logout form


async def test_unexpected_error_renders_500_page(app, client, login_as):
    @app.get("/__boom")
    async def _boom():
        raise RuntimeError("secret internals")

    response = await client.get("/__boom")
    assert response.status_code == 500
    assert "secret internals" not in response.text
```
Run: `uv run pytest admin/tests/unit -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'shortener_admin.deps'` (collection errors in the new test files).

- [ ] **Step 3: Implement deps, auth dependencies, views, routes, templates, and the app**

`admin/src/shortener_admin/deps.py`:
```python
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import cast

from fastapi import Request
from fastapi.templating import Jinja2Templates

from shortener_admin.api_client import ApiClient
from shortener_admin.oidc import KeycloakOidc
from shortener_admin.sessions import SessionStore
from shortener_admin.settings import AdminSettings


@dataclass(kw_only=True)
class AdminDeps:
    settings: AdminSettings
    sessions: SessionStore
    oidc: KeycloakOidc
    api: ApiClient
    clock: Callable[[], datetime]
    templates: Jinja2Templates


def get_deps(request: Request) -> AdminDeps:
    return cast(AdminDeps, request.app.state.deps)
```

`admin/src/shortener_admin/views.py`:
```python
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse

from shortener_admin.deps import get_deps


def is_htmx(request: Request) -> bool:
    return request.headers.get("hx-request") == "true"


def render(request: Request, template: str, *, status_code: int = 200, **context: Any) -> HTMLResponse:
    deps = get_deps(request)
    context = {"session": getattr(request.state, "session", None), **context}
    return deps.templates.TemplateResponse(request, template, context, status_code=status_code)
```

`admin/src/shortener_admin/auth.py`:
```python
"""Session, access, and CSRF dependencies (spec §7.4, §8)."""

from datetime import timedelta
from typing import Annotated

from fastapi import Depends, Request
from fastapi.responses import Response

from shortener_admin.api_client import ApiError
from shortener_admin.deps import AdminDeps, get_deps
from shortener_admin.oidc import OidcError
from shortener_admin.security import tokens_match
from shortener_admin.sessions import Session
from shortener_admin.settings import AdminSettings

SID_COOKIE = "sid"
REFRESH_MARGIN = timedelta(seconds=30)
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class LoginRequired(Exception):
    def __init__(self, next_path: str, expired: bool = False) -> None:
        super().__init__(next_path)
        self.next_path = next_path
        self.expired = expired


class NoAccess(Exception):
    pass


class CsrfFailed(Exception):
    pass


def current_path(request: Request) -> str:
    query = request.url.query
    return request.url.path + (f"?{query}" if query else "")


def set_sid_cookie(response: Response, session_id: str, settings: AdminSettings) -> None:
    response.set_cookie(
        SID_COOKIE, session_id, httponly=True, samesite="lax", secure=settings.cookie_secure, path="/"
    )


async def require_session(request: Request, deps: Annotated[AdminDeps, Depends(get_deps)]) -> Session:
    sid = request.cookies.get(SID_COOKIE)
    now = deps.clock()
    session = await deps.sessions.get(sid, now) if sid else None
    if session is None:
        raise LoginRequired(current_path(request))
    if session.tokens.access_expires_at - now <= REFRESH_MARGIN:
        try:
            tokens = await deps.oidc.refresh(session.tokens.refresh_token, session.tokens.id_token)
            me = await deps.api.me(tokens.access_token)
        except (OidcError, ApiError) as exc:
            await deps.sessions.delete(session.id)
            raise LoginRequired(current_path(request), expired=True) from exc
        refreshed = await deps.sessions.update_tokens(
            session.id, tokens=tokens, roles=frozenset(me["roles"]), now=now
        )
        if refreshed is None:
            raise LoginRequired(current_path(request), expired=True)
        session = refreshed
    request.state.session = session
    return session


async def require_access(session: Annotated[Session, Depends(require_session)]) -> Session:
    if not session.has_access:
        raise NoAccess()
    return session


async def verify_csrf(request: Request, session: Annotated[Session, Depends(require_session)]) -> Session:
    if request.method in UNSAFE_METHODS:
        supplied = request.headers.get("x-csrf-token")
        if not supplied:
            form = await request.form()
            value = form.get("csrf_token")
            supplied = value if isinstance(value, str) else None
        if not tokens_match(session.csrf_token, supplied):
            raise CsrfFailed()
    return session
```
`verify_csrf` depends on `require_session`, not `require_access`, so a no-role user can still sign out. Routes that mutate links depend on both `verify_csrf` and `require_access`.

`admin/src/shortener_admin/routes/__init__.py`: empty.

`admin/src/shortener_admin/routes/health.py`:
```python
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from shortener_admin.deps import AdminDeps, get_deps

router = APIRouter()


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(deps: Annotated[AdminDeps, Depends(get_deps)]) -> JSONResponse:
    ok = await deps.sessions.ping()
    return JSONResponse({"status": "ok" if ok else "unavailable"}, status_code=200 if ok else 503)
```

`admin/src/shortener_admin/routes/auth.py`:
```python
import logging
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response

from shortener_admin.api_client import ApiError
from shortener_admin.auth import SID_COOKIE, set_sid_cookie, verify_csrf
from shortener_admin.deps import AdminDeps, get_deps
from shortener_admin.oidc import OidcError
from shortener_admin.security import safe_next_path
from shortener_admin.sessions import Session
from shortener_admin.views import render

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth")
Deps = Annotated[AdminDeps, Depends(get_deps)]


@router.get("/login")
async def login(request: Request, deps: Deps, next: str = "/", expired: str | None = None) -> Response:
    next_path = safe_next_path(next)
    if expired == "1":
        return render(request, "session_expired.html", login_url=f"/auth/login?next={quote(next_path, safe='/')}")
    try:
        return await deps.oidc.begin_login(request, next_path)
    except OidcError:
        logger.exception("cannot start login")
        return render(request, "login_failed.html", status_code=503)


@router.get("/callback")
async def callback(request: Request, deps: Deps) -> Response:
    try:
        tokens, next_path = await deps.oidc.complete_login(request)
        me = await deps.api.me(tokens.access_token)
    except (OidcError, ApiError):
        logger.warning("login callback failed", exc_info=True)
        return render(request, "login_failed.html", status_code=400)
    now = deps.clock()
    await deps.sessions.purge_expired(now)
    if previous := request.cookies.get(SID_COOKIE):
        await deps.sessions.delete(previous)
    session = await deps.sessions.create(
        sub=me["sub"], username=me["username"], roles=frozenset(me["roles"]), tokens=tokens, now=now
    )
    response = RedirectResponse(next_path, status_code=303)
    set_sid_cookie(response, session.id, deps.settings)
    return response


@router.post("/logout")
async def logout(deps: Deps, session: Annotated[Session, Depends(verify_csrf)]) -> Response:
    home = f"{str(deps.settings.public_base_url).rstrip('/')}/"
    try:
        target = await deps.oidc.end_session_url(session.tokens.id_token, home)
    except OidcError:
        target = "/"
    await deps.sessions.delete(session.id)
    response = RedirectResponse(target, status_code=303)
    response.delete_cookie(SID_COOKIE, path="/")
    return response
```

`admin/src/shortener_admin/main.py`:
```python
"""App factory and production wiring."""

import logging
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from shortener_admin.api_client import ApiError
from shortener_admin.auth import SID_COOKIE, CsrfFailed, LoginRequired, NoAccess
from shortener_admin.deps import AdminDeps, get_deps
from shortener_admin.routes import auth, health
from shortener_admin.views import is_htmx, render

logger = logging.getLogger(__name__)
PACKAGE_DIR = Path(__file__).parent


def make_templates() -> Jinja2Templates:
    return Jinja2Templates(directory=PACKAGE_DIR / "templates")


def _login_url(next_path: str, expired: bool) -> str:
    url = f"/auth/login?next={quote(next_path, safe='/')}"
    return url + "&expired=1" if expired else url


def _to_login(request: Request, next_path: str, expired: bool) -> Response:
    url = _login_url(next_path, expired)
    if is_htmx(request):
        return Response(status_code=200, headers={"HX-Redirect": url})
    return RedirectResponse(url, status_code=303)


def create_app(deps: AdminDeps) -> FastAPI:
    app = FastAPI(title="Shortener admin", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.deps = deps
    app.add_middleware(
        SessionMiddleware,
        secret_key=deps.settings.cookie_secret.get_secret_value(),
        session_cookie="login_state",
        path="/auth",
        max_age=600,
        same_site="lax",
        https_only=deps.settings.cookie_secure,
    )
    app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")
    app.include_router(health.router)
    app.include_router(auth.router)

    @app.exception_handler(LoginRequired)
    async def _login_required(request: Request, exc: Exception) -> Response:
        error = exc if isinstance(exc, LoginRequired) else LoginRequired("/")
        return _to_login(request, error.next_path, error.expired)

    @app.exception_handler(NoAccess)
    async def _no_access(request: Request, exc: Exception) -> Response:
        return render(request, "no_access.html", status_code=403)

    @app.exception_handler(CsrfFailed)
    async def _csrf(request: Request, exc: Exception) -> Response:
        return render(request, "error.html", status_code=403, title="Request expired",
                      message="Your form expired. Reload the page and try again.")  # fmt: skip

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: Exception) -> Response:
        error = exc if isinstance(exc, ApiError) else ApiError(500, "Error")
        if error.status == 401:  # token revoked between refreshes
            if sid := request.cookies.get(SID_COOKIE):
                await get_deps(request).sessions.delete(sid)
            return _to_login(request, request.url.path, expired=True)
        return render(request, "error.html", status_code=error.status, title=error.title,
                      message=error.detail or "")  # fmt: skip

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> Response:
        logger.exception("unhandled error")
        return render(request, "error.html", status_code=500, title="Something went wrong",
                      message="Please try again.")  # fmt: skip

    return app
```
`build_deps` and `create_app_from_env` are added in Task 9.

Templates. Every one extends `base.html`. Jinja2 autoescaping is on for `.html`, so never add `|safe`.

`admin/src/shortener_admin/templates/base.html`:
```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block title %}Shortener admin{% endblock %}</title>
  <link rel="stylesheet" href="{{ url_for('static', path='vendor/pico.min.css') }}">
  <link rel="stylesheet" href="{{ url_for('static', path='app.css') }}">
  <script src="{{ url_for('static', path='vendor/htmx.min.js') }}" defer></script>
  <script src="{{ url_for('static', path='vendor/chart.umd.js') }}" defer></script>
  <script src="{{ url_for('static', path='app.js') }}" defer></script>
</head>
<body hx-boost="true"{% if session %} hx-headers='{{ {"X-CSRF-Token": session.csrf_token} | tojson }}'{% endif %}>
  <header class="container">
    <nav>
      <ul>
        <li><strong><a href="/">Shortener</a></strong></li>
        {% if session and session.has_access %}
        <li><a href="/links">Links</a></li>
        {% if session.can_create %}<li><a href="/links/new">New link</a></li>{% endif %}
        {% endif %}
      </ul>
      {% if session %}
      <ul>
        <li>{{ session.username }} <small class="muted">({{ session.roles_label }})</small></li>
        <li>
          {# hx-boost off: logout ends on Keycloak (cross-origin), which an AJAX request cannot follow #}
          <form method="post" action="/auth/logout" hx-boost="false" class="inline">
            <input type="hidden" name="csrf_token" value="{{ session.csrf_token }}">
            <button type="submit" class="secondary outline">Sign out</button>
          </form>
        </li>
      </ul>
      {% endif %}
    </nav>
  </header>
  <main class="container">{% block content %}{% endblock %}</main>
</body>
</html>
```
`tojson` produces `{"X-CSRF-Token": "…"}`, and Jinja's `tojson` escapes `'`, `<`, `>` and `&`, so the single-quoted attribute can't be broken out of. `test_base_template_carries_csrf_in_hx_headers` asserts this exact rendering. If Jinja renders it with different spacing, adjust the **test string** to the real output. Don't hand-build the JSON.

`admin/src/shortener_admin/templates/login_failed.html`:
```html
{% extends "base.html" %}
{% block title %}Sign-in failed{% endblock %}
{% block content %}
<article>
  <h1>Sign-in failed</h1>
  <p>We couldn't complete your sign-in. This can happen if the sign-in page was open too long.</p>
  <a role="button" href="/auth/login">Try again</a>
</article>
{% endblock %}
```

`admin/src/shortener_admin/templates/session_expired.html`:
```html
{% extends "base.html" %}
{% block title %}Session expired{% endblock %}
{% block content %}
<article>
  <h1>Your session expired</h1>
  <p>Please sign in again to continue.</p>
  <a role="button" href="{{ login_url }}">Sign in</a>
</article>
{% endblock %}
```

`admin/src/shortener_admin/templates/no_access.html`:
```html
{% extends "base.html" %}
{% block title %}No access{% endblock %}
{% block content %}
<article>
  <h1>You don't have access</h1>
  <p>Your account has no role in the URL shortener. Ask an administrator to grant you
     <em>viewer</em>, <em>editor</em>, or <em>admin</em>.</p>
</article>
{% endblock %}
```

`admin/src/shortener_admin/templates/error.html`:
```html
{% extends "base.html" %}
{% block title %}{{ title }}{% endblock %}
{% block content %}
<article>
  <h1>{{ title }}</h1>
  {% if message %}<p>{{ message }}</p>{% endif %}
  <a href="/">Back to the dashboard</a>
</article>
{% endblock %}
```

Run: `uv run pytest admin/tests -q`
Expected: all PASS.

- [ ] **Step 4: Gates and commit**

```bash
uv run ruff format . && make check && git add admin && git commit -m "feat(admin): login/logout, session refresh, CSRF, base templates, vendored assets

Co-Authored-By: <implementing model> <noreply@anthropic.com>"
```

---
### Task 6: Dashboard and the links list (search, filter, paging, HTMX partials)

**Files:**
- Create: `admin/src/shortener_admin/routes/pages.py`, `admin/src/shortener_admin/routes/links.py`
- Create: `admin/src/shortener_admin/templates/{dashboard,links}.html`, `admin/src/shortener_admin/templates/partials/links_table.html`
- Modify: `admin/src/shortener_admin/main.py` (template filters; include routers)
- Test: `admin/tests/unit/test_dashboard_and_list.py`

**Interfaces:**
- Consumes: `require_access`, `render`, `is_htmx` (Task 5); `ApiClient.summary` and `list_links` (Task 4).
- Produces:
  - **Template filter `as_of`:** ISO-8601 string → `"YYYY-MM-DD HH:MM:SS UTC"`.
  - **`GET /`:** the dashboard, from `/api/v1/stats/summary`.
  - **`GET /links?q=&status=&page=`:** the full page, or `partials/links_table.html` for HTMX. Unknown `status` values and non-positive or non-numeric `page` values are dropped (page 1) and never sent to the API. The table root is `<div id="links-table">`.
  - **`routes.links.router`:** created here and extended in Tasks 7 and 8.

- [ ] **Step 1: Write the failing tests**

`admin/tests/unit/test_dashboard_and_list.py`:
```python
import httpx

SUMMARY = {
    "link_count": 3,
    "clicks_7d": 42,
    "top_links": [{"id": "11111111-1111-1111-1111-111111111111", "code": "aZ3kQ9x", "clicks_7d": 30}],
    "data_as_of": "2026-10-01T11:59:00Z",
}


def page(items, total=None, page_no=1):
    return {"items": items, "total": len(items) if total is None else total, "page": page_no, "page_size": 20}


LINK = {
    "id": "11111111-1111-1111-1111-111111111111", "code": "aZ3kQ9x", "short_url": "http://localhost:8000/aZ3kQ9x",
    "target_url": "https://example.com/", "owner_username": "eddie", "status": "active", "is_active": True,
    "blocked_at": None, "blocked_reason": None, "created_at": "2026-10-01T10:00:00Z", "updated_at": "2026-10-01T10:00:00Z",
}  # fmt: skip


async def test_dashboard_shows_summary(client, mocks, ids, login_as):
    await login_as()
    route = mocks.get(f"{ids['API']}/api/v1/stats/summary").respond(json=SUMMARY)
    html = (await client.get("/")).text
    assert 'id="link-count">3<' in html and 'id="clicks-7d">42<' in html
    assert 'href="/links/11111111-1111-1111-1111-111111111111">aZ3kQ9x</a>' in html
    assert "Data as of 2026-10-01 11:59:00 UTC" in html
    assert route.calls.last.request.headers["authorization"] == "Bearer access-eddie"


async def test_dashboard_before_any_processing(client, mocks, ids, login_as):
    await login_as()
    mocks.get(f"{ids['API']}/api/v1/stats/summary").respond(
        json={"link_count": 0, "clicks_7d": 0, "top_links": [], "data_as_of": None}
    )
    html = (await client.get("/")).text
    assert "No clicks in the last 7 days" in html and "No clicks processed yet" in html


async def test_dashboard_for_a_user_without_roles_never_calls_the_api(client, mocks, ids, login_as):
    await login_as("nora", ("offline_access",))
    route = mocks.get(f"{ids['API']}/api/v1/stats/summary")
    assert (await client.get("/")).status_code == 403
    assert not route.called


async def test_api_down_renders_503_page(client, mocks, ids, login_as):
    await login_as()
    mocks.get(f"{ids['API']}/api/v1/stats/summary").mock(side_effect=httpx.ConnectError("down"))
    response = await client.get("/")
    assert response.status_code == 503
    assert "API unavailable" in response.text


async def test_links_full_page_and_htmx_partial(client, mocks, ids, login_as):
    await login_as()
    mocks.get(f"{ids['API']}/api/v1/links").respond(json=page([LINK]))
    full = (await client.get("/links")).text
    assert "<html" in full and 'id="links-table"' in full and 'name="q"' in full
    partial = (await client.get("/links", headers={"HX-Request": "true"})).text
    assert "<html" not in partial and partial.lstrip().startswith('<div id="links-table"')


async def test_filters_are_passed_and_bad_values_dropped(client, mocks, ids, login_as):
    await login_as()
    route = mocks.get(f"{ids['API']}/api/v1/links").respond(json=page([]))
    await client.get("/links", params={"q": " exa ", "status": "blocked", "page": "2"})
    assert dict(route.calls.last.request.url.params) == {"q": "exa", "status": "blocked", "page": "2", "page_size": "20"}
    await client.get("/links", params={"status": "everything", "page": "-3"})
    assert dict(route.calls.last.request.url.params) == {"page": "1", "page_size": "20"}


async def test_pagination_links_keep_filters(client, mocks, ids, login_as):
    await login_as()
    mocks.get(f"{ids['API']}/api/v1/links").respond(json=page([LINK], total=45, page_no=2))
    html = (await client.get("/links", params={"q": "a b", "page": "2"})).text
    assert "Page 2 of 3" in html
    assert 'href="/links?q=a+b&amp;page=1"' in html and 'href="/links?q=a+b&amp;page=3"' in html


async def test_table_escapes_api_data(client, mocks, ids, login_as):
    await login_as()
    nasty = LINK | {"target_url": "https://x.example/<script>alert(1)</script>", "owner_username": "<b>eve</b>"}
    mocks.get(f"{ids['API']}/api/v1/links").respond(json=page([nasty]))
    html = (await client.get("/links")).text
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html
    assert "<b>eve</b>" not in html


async def test_empty_list_message(client, mocks, ids, login_as):
    await login_as()
    mocks.get(f"{ids['API']}/api/v1/links").respond(json=page([]))
    assert "No links match" in (await client.get("/links")).text
```
Run: `uv run pytest admin/tests/unit/test_dashboard_and_list.py -q`
Expected: FAIL. `/` and `/links` return 404 (no routes yet).

- [ ] **Step 2: Implement filters, routes, and templates**

In `main.py`:
- Add `from datetime import UTC, datetime`.
- Change `make_templates`:
```python
def _as_of(value: str) -> str:
    moment = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    return moment.strftime("%Y-%m-%d %H:%M:%S UTC")


def make_templates() -> Jinja2Templates:
    templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")
    templates.env.filters["as_of"] = _as_of
    return templates
```
- Import `links, pages` from `shortener_admin.routes`, and add `app.include_router(pages.router)` and `app.include_router(links.router)` after the auth router.

`admin/src/shortener_admin/routes/pages.py`:
```python
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from shortener_admin.auth import require_access
from shortener_admin.deps import AdminDeps, get_deps
from shortener_admin.sessions import Session
from shortener_admin.views import render

router = APIRouter()


@router.get("/")
async def dashboard(
    request: Request,
    deps: Annotated[AdminDeps, Depends(get_deps)],
    session: Annotated[Session, Depends(require_access)],
) -> HTMLResponse:
    summary = await deps.api.summary(session.tokens.access_token)
    return render(request, "dashboard.html", summary=summary)
```

`admin/src/shortener_admin/routes/links.py`:
```python
import math
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from shortener_admin.auth import require_access
from shortener_admin.deps import AdminDeps, get_deps
from shortener_admin.sessions import Session
from shortener_admin.views import is_htmx, render

router = APIRouter()
Deps = Annotated[AdminDeps, Depends(get_deps)]
Access = Annotated[Session, Depends(require_access)]
STATUSES = ("active", "disabled", "blocked")


def _page_number(raw: str) -> int:
    return int(raw) if raw.isdigit() and int(raw) >= 1 else 1


@router.get("/links")
async def list_links(
    request: Request, deps: Deps, session: Access, q: str = "", status: str = "", page: str = "1"
) -> HTMLResponse:
    query = q.strip()
    status_filter = status if status in STATUSES else ""
    page_no = _page_number(page)
    result: dict[str, Any] = await deps.api.list_links(
        session.tokens.access_token, q=query or None, status=status_filter or None, page=page_no
    )
    pages = max(1, math.ceil(result["total"] / result["page_size"]))

    def page_url(n: int) -> str:
        params = {k: v for k, v in (("q", query), ("status", status_filter)) if v} | {"page": n}
        return f"/links?{urlencode(params)}"

    template = "partials/links_table.html" if is_htmx(request) else "links.html"
    return render(request, template, result=result, q=query, status=status_filter, page=page_no,
                  pages=pages, page_url=page_url, statuses=STATUSES,
                  deleted=request.query_params.get("deleted") == "1")  # fmt: skip
```

`admin/src/shortener_admin/templates/dashboard.html`:
```html
{% extends "base.html" %}
{% block title %}Dashboard{% endblock %}
{% block content %}
<h1>Dashboard</h1>
<div class="grid">
  <article><header>Links</header><p class="stat" id="link-count">{{ summary.link_count }}</p></article>
  <article><header>Clicks (last 7 days)</header><p class="stat" id="clicks-7d">{{ summary.clicks_7d }}</p></article>
</div>
<h2>Top links</h2>
{% if summary.top_links %}
<table>
  <thead><tr><th>Code</th><th>Clicks (7 days)</th></tr></thead>
  <tbody>
  {% for link in summary.top_links %}
    <tr><td><a href="/links/{{ link.id }}">{{ link.code }}</a></td><td>{{ link.clicks_7d }}</td></tr>
  {% endfor %}
  </tbody>
</table>
{% else %}
<p class="muted">No clicks in the last 7 days.</p>
{% endif %}
<p class="muted"><small>{% if summary.data_as_of %}Data as of {{ summary.data_as_of | as_of }}{% else %}No clicks processed yet{% endif %}</small></p>
{% endblock %}
```

`admin/src/shortener_admin/templates/links.html`:
```html
{% extends "base.html" %}
{% block title %}Links{% endblock %}
{% block content %}
<h1>Links</h1>
{% if deleted %}<p role="status">Link deleted.</p>{% endif %}
<form method="get" action="/links" hx-get="/links" hx-target="#links-table" hx-swap="outerHTML" hx-push-url="true"
      hx-trigger="submit, input changed delay:300ms from:find input[name=q], change from:find select">
  <div class="grid">
    <input type="search" name="q" value="{{ q }}" placeholder="Search code or target" aria-label="Search">
    <select name="status" aria-label="Status">
      <option value="">Any status</option>
      {% for value in statuses %}<option value="{{ value }}"{% if value == status %} selected{% endif %}>{{ value }}</option>{% endfor %}
    </select>
  </div>
</form>
{% include "partials/links_table.html" %}
{% endblock %}
```

`admin/src/shortener_admin/templates/partials/links_table.html`:
```html
<div id="links-table">
{% if result['items'] %}
<table>
  <thead><tr><th>Short link</th><th>Target</th><th>Owner</th><th>Status</th><th>Created</th></tr></thead>
  <tbody>
  {% for link in result['items'] %}
    <tr>
      <td><a href="/links/{{ link.id }}">{{ link.code }}</a></td>
      <td class="target">{{ link.target_url }}</td>
      <td>{{ link.owner_username }}</td>
      <td><span class="badge {{ link.status }}">{{ link.status }}</span></td>
      <td>{{ link.created_at | as_of }}</td>
    </tr>
  {% endfor %}
  </tbody>
</table>
<nav aria-label="Pagination">
  <ul>
    {% if page > 1 %}<li><a href="{{ page_url(page - 1) }}" hx-get="{{ page_url(page - 1) }}" hx-target="#links-table" hx-swap="outerHTML" hx-push-url="true">Previous</a></li>{% endif %}
    <li class="muted">Page {{ page }} of {{ pages }} · {{ result.total }} links</li>
    {% if page < pages %}<li><a href="{{ page_url(page + 1) }}" hx-get="{{ page_url(page + 1) }}" hx-target="#links-table" hx-swap="outerHTML" hx-push-url="true">Next</a></li>{% endif %}
  </ul>
</nav>
{% else %}
<p class="muted">No links match.</p>
{% endif %}
</div>
```
Always use `result['items']`, never `result.items`: on a dict, Jinja's attribute lookup finds the `.items()` method first.

Run: `uv run pytest admin/tests -q`
Expected: all PASS.

- [ ] **Step 3: Gates and commit**

```bash
uv run ruff format . && make check && git add admin && git commit -m "feat(admin): dashboard and links list with HTMX search, filter, and paging

Co-Authored-By: <implementing model> <noreply@anthropic.com>"
```

---

### Task 7: Create, detail, edit, toggle, delete

**Files:**
- Modify: `admin/src/shortener_admin/routes/links.py`, `admin/src/shortener_admin/auth.py` (add `verify_csrf_with_access`), `admin/src/shortener_admin/views.py` (add `api_message`)
- Create: `admin/src/shortener_admin/templates/{link_new,link_detail}.html`
- Test: `admin/tests/unit/test_link_pages.py`

**Interfaces:**
- Consumes: Tasks 4–6.
- Produces:
  - `auth.verify_csrf_with_access`: CSRF-checked, then `NoAccess` for users without a role.
  - `views.api_message(error: ApiError) -> str`, which is `"<title>: <blocked_reason>"` when the API sent a block reason, and otherwise `detail` or `title`.
  - Routes:
    - `GET /links/new`: 403 page when the user can't create links.
    - `POST /links` (form field `target_url`): 303 to `/links/{id}?created=1`. A 4xx re-renders the form with the error and the submitted value, keeping the API's status.
    - `GET /links/{id}`: 404 page for a non-UUID id; banners for `?created=1` and `?updated=1`.
    - `POST /links/{id}/edit` (`target_url`), `POST /links/{id}/toggle` (`is_active` = `"true"`/`"false"`), and `POST /links/{id}/delete` (303 to `/links?deleted=1`).
  - **API errors on the POST routes:** a 4xx re-renders the detail page with an inline error, keeping the API's status code. 401 and 503 propagate to the app's handlers.
  - **`link_detail.html`** includes the moderation section (`can_block`) and the stats section (`stats`), which Task 8 fills in. `stats` is `None` until then, and the template guards it.

- [ ] **Step 1: Write the failing tests**

`admin/tests/unit/test_link_pages.py`:
```python
import json

LID = "11111111-1111-1111-1111-111111111111"
LINK = {
    "id": LID, "code": "aZ3kQ9x", "short_url": "http://localhost:8000/aZ3kQ9x",
    "target_url": "https://example.com/", "owner_username": "eddie", "status": "active", "is_active": True,
    "blocked_at": None, "blocked_reason": None, "created_at": "2026-10-01T10:00:00Z", "updated_at": "2026-10-01T10:00:00Z",
}  # fmt: skip


def link_route(mocks, ids, link=LINK):
    # From Task 8 on, the detail page also fetches stats; a 4xx there just omits the chart.
    mocks.get(f"{ids['API']}/api/v1/links/{LID}/stats").respond(422, json={"title": "n/a", "status": 422})
    return mocks.get(f"{ids['API']}/api/v1/links/{LID}").respond(json=link)


async def test_new_link_form_for_editors_only(client, login_as):
    await login_as()
    assert 'name="target_url"' in (await client.get("/links/new")).text
    await login_as("victor", ("viewer",))
    assert (await client.get("/links/new")).status_code == 403


async def test_create_posts_to_api_and_redirects_to_detail(client, mocks, ids, login_as):
    session = await login_as()
    route = mocks.post(f"{ids['API']}/api/v1/links").respond(201, json=LINK)
    response = await client.post("/links", data={"csrf_token": session.csrf_token, "target_url": "https://example.com/"})
    assert response.status_code == 303
    assert response.headers["location"] == f"/links/{LID}?created=1"
    assert json.loads(route.calls.last.request.content) == {"target_url": "https://example.com/"}


async def test_create_rejected_rerenders_form_with_message_and_value(client, mocks, ids, login_as):
    session = await login_as()
    mocks.post(f"{ids['API']}/api/v1/links").respond(
        422, json={"title": "Invalid target URL", "status": 422, "detail": "URL scheme must be http or https"}
    )
    response = await client.post("/links", data={"csrf_token": session.csrf_token, "target_url": "ftp://x"})
    assert response.status_code == 422
    assert "URL scheme must be http or https" in response.text and 'value="ftp://x"' in response.text


async def test_post_without_csrf_never_reaches_the_api(client, mocks, ids, login_as):
    await login_as()
    create = mocks.post(f"{ids['API']}/api/v1/links")
    delete = mocks.delete(f"{ids['API']}/api/v1/links/{LID}")
    assert (await client.post("/links", data={"target_url": "https://x.example"})).status_code == 403
    assert (await client.post(f"/links/{LID}/delete")).status_code == 403
    assert not create.called and not delete.called


async def test_detail_shows_link_and_owner_controls(client, mocks, ids, login_as):
    await login_as()
    link_route(mocks, ids)
    html = (await client.get(f"/links/{LID}?created=1")).text
    assert "Short link created" in html
    assert 'data-copy="http://localhost:8000/aZ3kQ9x"' in html
    assert f'action="/links/{LID}/edit"' in html and f'action="/links/{LID}/delete"' in html
    assert f'action="/links/{LID}/block"' not in html  # editors don't moderate


async def test_viewer_sees_no_edit_controls(client, mocks, ids, login_as):
    await login_as("victor", ("viewer",))
    link_route(mocks, ids)
    html = (await client.get(f"/links/{LID}")).text
    assert f'action="/links/{LID}/edit"' not in html


async def test_detail_escapes_block_reason(client, mocks, ids, login_as):
    await login_as()
    link_route(mocks, ids, LINK | {"status": "blocked", "blocked_reason": "<script>alert('x')</script>"})
    html = (await client.get(f"/links/{LID}")).text
    assert "<script>alert" not in html and "&lt;script&gt;" in html
    assert f'action="/links/{LID}/edit"' not in html  # owners can't edit blocked links


async def test_non_uuid_id_is_a_404_page(client, mocks, ids, login_as):
    await login_as()
    response = await client.get("/links/not-a-uuid")
    assert response.status_code == 404
    assert "<html" in response.text


async def test_unknown_link_shows_api_404(client, mocks, ids, login_as):
    await login_as()
    mocks.get(f"{ids['API']}/api/v1/links/{LID}").respond(404, json={"title": "Link not found", "status": 404})
    response = await client.get(f"/links/{LID}")
    assert response.status_code == 404 and "Link not found" in response.text


async def test_edit_updates_target_and_redirects(client, mocks, ids, login_as):
    session = await login_as()
    route = mocks.patch(f"{ids['API']}/api/v1/links/{LID}").respond(json=LINK)
    response = await client.post(f"/links/{LID}/edit", data={"csrf_token": session.csrf_token, "target_url": "https://new.example/"})
    assert response.headers["location"] == f"/links/{LID}?updated=1"
    assert json.loads(route.calls.last.request.content) == {"target_url": "https://new.example/"}


async def test_edit_on_blocked_link_shows_reason_inline(client, mocks, ids, login_as):
    session = await login_as()
    mocks.patch(f"{ids['API']}/api/v1/links/{LID}").respond(
        409, json={"title": "Link is blocked", "status": 409, "detail": "Blocked by an administrator: spam",
                   "blocked_reason": "spam"}
    )  # fmt: skip
    link_route(mocks, ids, LINK | {"status": "blocked", "blocked_reason": "spam"})
    response = await client.post(f"/links/{LID}/edit", data={"csrf_token": session.csrf_token, "target_url": "https://x.example/"})
    assert response.status_code == 409
    assert "Link is blocked: spam" in response.text


async def test_toggle_sends_is_active(client, mocks, ids, login_as):
    session = await login_as()
    route = mocks.patch(f"{ids['API']}/api/v1/links/{LID}").respond(json=LINK)
    await client.post(f"/links/{LID}/toggle", data={"csrf_token": session.csrf_token, "is_active": "false"})
    assert json.loads(route.calls.last.request.content) == {"is_active": False}


async def test_delete_redirects_to_list(client, mocks, ids, login_as):
    session = await login_as()
    mocks.delete(f"{ids['API']}/api/v1/links/{LID}").respond(204)
    response = await client.post(f"/links/{LID}/delete", data={"csrf_token": session.csrf_token})
    assert response.headers["location"] == "/links?deleted=1"
```

Run: `uv run pytest admin/tests/unit/test_link_pages.py -q`
Expected: FAIL (the routes don't exist yet).

- [ ] **Step 2: Implement**

Append to `auth.py`:
```python
async def verify_csrf_with_access(session: Annotated[Session, Depends(verify_csrf)]) -> Session:
    if not session.has_access:
        raise NoAccess()
    return session
```

Append to `views.py` (and `from shortener_admin.api_client import ApiError`):
```python
def api_message(error: ApiError) -> str:
    reason = error.extra.get("blocked_reason")
    if reason:
        return f"{error.title}: {reason}"
    return error.detail or error.title
```

Add to `routes/links.py`. Merge these into the module's imports: `from collections.abc import Awaitable, Callable`, `from uuid import UUID`, `from fastapi import Form`, `from fastapi.responses import RedirectResponse, Response`, `from shortener_admin.api_client import ApiError`, `from shortener_admin.auth import verify_csrf_with_access`, `from shortener_admin.views import api_message`. Then add:
```python
Mutate = Annotated[Session, Depends(verify_csrf_with_access)]
PASS_THROUGH = {401, 503}  # handled app-wide: session expiry / API down


def _uuid_or_none(raw: str) -> str | None:
    try:
        return str(UUID(raw))
    except ValueError:
        return None


def _can_edit(session: Session, link: dict[str, Any]) -> bool:
    if session.is_admin:
        return True
    return session.can_create and link["owner_username"] == session.username and link["status"] != "blocked"


def _not_found(request: Request) -> HTMLResponse:
    return render(request, "error.html", status_code=404, title="Link not found", message="")


async def _detail(
    request: Request, deps: AdminDeps, session: Session, link_id: str, *,
    notice: str | None = None, error: ApiError | None = None, bucket: str = "hour",
) -> HTMLResponse:  # fmt: skip
    link = await deps.api.get_link(session.tokens.access_token, link_id)
    return render(
        request, "link_detail.html", status_code=error.status if error else 200, link=link, notice=notice,
        error=api_message(error) if error else None, can_edit=_can_edit(session, link),
        can_block=session.is_admin, stats=None, bucket=bucket,
    )  # fmt: skip


async def _act(
    request: Request, deps: AdminDeps, session: Session, link_id: str,
    call: Callable[[], Awaitable[Any]], success_url: str,
) -> Response:  # fmt: skip
    try:
        await call()
    except ApiError as exc:
        if exc.status in PASS_THROUGH:
            raise
        return await _detail(request, deps, session, link_id, error=exc)
    return RedirectResponse(success_url, status_code=303)


@router.get("/links/new")
async def new_link(request: Request, session: Access) -> HTMLResponse:
    if not session.can_create:
        return render(request, "error.html", status_code=403, title="Not allowed",
                      message="Your role can't create links.")  # fmt: skip
    return render(request, "link_new.html", target_url="", error=None)


@router.post("/links")
async def create_link(
    request: Request, deps: Deps, session: Mutate, target_url: Annotated[str, Form()] = ""
) -> Response:
    try:
        link = await deps.api.create_link(session.tokens.access_token, target_url)
    except ApiError as exc:
        if exc.status in PASS_THROUGH:
            raise
        return render(request, "link_new.html", status_code=exc.status, target_url=target_url,
                      error=api_message(exc))  # fmt: skip
    return RedirectResponse(f"/links/{link['id']}?created=1", status_code=303)


@router.get("/links/{link_id}")
async def link_detail(request: Request, link_id: str, deps: Deps, session: Access) -> HTMLResponse:
    lid = _uuid_or_none(link_id)
    if lid is None:
        return _not_found(request)
    params = request.query_params
    notice = ("Short link created." if params.get("created") == "1"
              else "Link updated." if params.get("updated") == "1" else None)  # fmt: skip
    return await _detail(request, deps, session, lid, notice=notice)


@router.post("/links/{link_id}/edit")
async def edit_link(
    request: Request, link_id: str, deps: Deps, session: Mutate, target_url: Annotated[str, Form()] = ""
) -> Response:
    lid = _uuid_or_none(link_id)
    if lid is None:
        return _not_found(request)
    token = session.tokens.access_token
    return await _act(request, deps, session, lid,
                      lambda: deps.api.update_link(token, lid, target_url=target_url),
                      f"/links/{lid}?updated=1")  # fmt: skip


@router.post("/links/{link_id}/toggle")
async def toggle_link(
    request: Request, link_id: str, deps: Deps, session: Mutate, is_active: Annotated[str, Form()] = ""
) -> Response:
    lid = _uuid_or_none(link_id)
    if lid is None or is_active not in ("true", "false"):
        return _not_found(request)
    token = session.tokens.access_token
    return await _act(request, deps, session, lid,
                      lambda: deps.api.update_link(token, lid, is_active=is_active == "true"),
                      f"/links/{lid}?updated=1")  # fmt: skip


@router.post("/links/{link_id}/delete")
async def delete_link(request: Request, link_id: str, deps: Deps, session: Mutate) -> Response:
    lid = _uuid_or_none(link_id)
    if lid is None:
        return _not_found(request)
    token = session.tokens.access_token
    return await _act(request, deps, session, lid, lambda: deps.api.delete_link(token, lid),
                      "/links?deleted=1")  # fmt: skip
```
`/links/new` must be registered **before** `/links/{link_id}`; it is above, because of the order in the file.

The CSRF dependency reads `request.form()` before FastAPI parses the `Form()` parameters. Starlette caches the parsed form, so both see the same data. `test_create_posts_to_api_and_redirects_to_detail` checks this.

`admin/src/shortener_admin/templates/link_new.html`:
```html
{% extends "base.html" %}
{% block title %}New link{% endblock %}
{% block content %}
<h1>New short link</h1>
{% if error %}<p role="alert">{{ error }}</p>{% endif %}
<form method="post" action="/links">
  <input type="hidden" name="csrf_token" value="{{ session.csrf_token }}">
  <label>Target URL
    <input type="url" name="target_url" value="{{ target_url }}" required maxlength="2048" placeholder="https://example.com/page">
  </label>
  <button type="submit">Create</button>
</form>
{% endblock %}
```

`admin/src/shortener_admin/templates/link_detail.html`:
```html
{% extends "base.html" %}
{% block title %}{{ link.code }}{% endblock %}
{% block content %}
{% if notice %}<p role="status">{{ notice }}</p>{% endif %}
{% if error %}<p role="alert">{{ error }}</p>{% endif %}
<hgroup>
  <h1>{{ link.code }} <span class="badge {{ link.status }}">{{ link.status }}</span></h1>
  <p><a href="{{ link.short_url }}">{{ link.short_url }}</a>
     <button type="button" class="outline secondary" data-copy="{{ link.short_url }}">Copy</button></p>
</hgroup>
{% if link.status == "blocked" %}
<article><strong>Blocked by an administrator.</strong> Reason: {{ link.blocked_reason }}</article>
{% endif %}
<dl>
  <dt>Target</dt><dd class="target">{{ link.target_url }}</dd>
  <dt>Owner</dt><dd>{{ link.owner_username }}</dd>
  <dt>Created</dt><dd>{{ link.created_at | as_of }}</dd>
  <dt>Updated</dt><dd>{{ link.updated_at | as_of }}</dd>
</dl>

{% if can_edit %}
<section>
  <h2>Edit</h2>
  <form method="post" action="/links/{{ link.id }}/edit">
    <input type="hidden" name="csrf_token" value="{{ session.csrf_token }}">
    <label>Target URL <input type="url" name="target_url" value="{{ link.target_url }}" required maxlength="2048"></label>
    <button type="submit">Save</button>
  </form>
  <form method="post" action="/links/{{ link.id }}/toggle" class="inline">
    <input type="hidden" name="csrf_token" value="{{ session.csrf_token }}">
    <input type="hidden" name="is_active" value="{{ 'false' if link.is_active else 'true' }}">
    <button type="submit" class="secondary">{{ 'Disable' if link.is_active else 'Enable' }}</button>
  </form>
  <details>
    <summary role="button" class="secondary outline">Delete…</summary>
    <p>This permanently deletes the link and its click history.</p>
    <form method="post" action="/links/{{ link.id }}/delete">
      <input type="hidden" name="csrf_token" value="{{ session.csrf_token }}">
      <button type="submit" class="contrast">Yes, delete {{ link.code }}</button>
    </form>
  </details>
</section>
{% endif %}

{% if can_block %}
<section>
  <h2>Moderation</h2>
  {% if link.status == "blocked" %}
  <form method="post" action="/links/{{ link.id }}/unblock">
    <input type="hidden" name="csrf_token" value="{{ session.csrf_token }}">
    <button type="submit" class="secondary">Unblock</button>
  </form>
  {% else %}
  <form method="post" action="/links/{{ link.id }}/block">
    <input type="hidden" name="csrf_token" value="{{ session.csrf_token }}">
    <label>Reason (visible to the owner) <input type="text" name="reason" required maxlength="1000"></label>
    <button type="submit" class="contrast">Block link</button>
  </form>
  {% endif %}
</section>
{% endif %}

{% if stats %}
<section>
  <h2>Clicks</h2>
  {% include "partials/link_stats.html" %}
</section>
{% endif %}
{% endblock %}
```
The target URL is shown as text, never as a link, so a hostile target (already rejected by the API) can't be clicked from the admin UI.

Run: `uv run pytest admin/tests -q`
Expected: all PASS.

- [ ] **Step 3: Gates and commit**

```bash
uv run ruff format . && make check && git add admin && git commit -m "feat(admin): create, detail, edit, toggle, and delete link pages

Co-Authored-By: <implementing model> <noreply@anthropic.com>"
```

---

### Task 8: Moderation (block/unblock) and the click chart

**Files:**
- Modify: `admin/src/shortener_admin/routes/links.py`, `admin/src/shortener_admin/views.py` (add `chart_data`)
- Create: `admin/src/shortener_admin/templates/partials/link_stats.html`
- Test: `admin/tests/unit/test_moderation_and_stats.py`

**Interfaces:**
- Consumes: Task 7's `_detail`, `_act`, `_uuid_or_none`, and `link_detail.html`'s `stats`/`can_block` sections; `ApiClient.link_stats`/`block_link`/`unblock_link`.
- Produces:
  - `views.chart_data(stats: dict[str, Any], bucket: str) -> dict[str, list[Any]]`, returning `{"labels": [...], "counts": [...]}`. Labels are `"MM-DD HH:00"` for hourly buckets and `"YYYY-MM-DD"` for daily ones.
  - **`POST /links/{id}/block`** (form field `reason`) and **`POST /links/{id}/unblock`**: 303 to `/links/{id}?updated=1`; API 4xx is shown inline.
  - **`GET /links/{id}/stats?bucket=hour|day`:** the `partials/link_stats.html` fragment for HTMX, or a 303 to `/links/{id}?bucket=…` otherwise. Unknown buckets become `hour`.
  - **Ranges:** `hour` uses the API default (last 7 days); `day` sends `from = clock() - 30 days`.
  - **`GET /links/{id}`** now also fetches stats (honouring `?bucket=`), and the detail page renders the chart. A stats failure other than 401/503 doesn't break the page: the stats section is simply omitted.

- [ ] **Step 1: Write the failing tests**

`admin/tests/unit/test_moderation_and_stats.py`:
```python
import html as html_lib
import json
import re
from datetime import timedelta

LID = "11111111-1111-1111-1111-111111111111"
LINK = {
    "id": LID, "code": "aZ3kQ9x", "short_url": "http://localhost:8000/aZ3kQ9x",
    "target_url": "https://example.com/", "owner_username": "eddie", "status": "active", "is_active": True,
    "blocked_at": None, "blocked_reason": None, "created_at": "2026-10-01T10:00:00Z", "updated_at": "2026-10-01T10:00:00Z",
}  # fmt: skip
STATS = {
    "total": 5, "bucket": "hour", "from": "2026-10-01T10:00:00Z", "to": "2026-10-01T12:00:00Z",
    "series": [{"ts": "2026-10-01T10:00:00Z", "count": 3}, {"ts": "2026-10-01T11:00:00Z", "count": 2}],
    "top_referrers": [{"referrer_host": "news.example", "count": 4}, {"referrer_host": "<b>x</b>", "count": 1}],
    "data_as_of": "2026-10-01T11:59:00Z",
}  # fmt: skip


def detail_routes(mocks, ids, link=LINK, stats=STATS):
    mocks.get(f"{ids['API']}/api/v1/links/{LID}").respond(json=link)
    return mocks.get(f"{ids['API']}/api/v1/links/{LID}/stats").respond(json=stats)


async def test_admin_sees_block_form_and_blocks_with_reason(client, mocks, ids, login_as):
    session = await login_as("alice", ("admin",))
    detail_routes(mocks, ids)
    assert f'action="/links/{LID}/block"' in (await client.get(f"/links/{LID}")).text
    route = mocks.post(f"{ids['API']}/api/v1/links/{LID}/block").respond(json=LINK)
    response = await client.post(f"/links/{LID}/block", data={"csrf_token": session.csrf_token, "reason": "phishing"})
    assert response.headers["location"] == f"/links/{LID}?updated=1"
    assert json.loads(route.calls.last.request.content) == {"reason": "phishing"}


async def test_double_block_shows_conflict_inline(client, mocks, ids, login_as):
    session = await login_as("alice", ("admin",))
    detail_routes(mocks, ids)
    mocks.post(f"{ids['API']}/api/v1/links/{LID}/block").respond(409, json={"title": "Link is already blocked", "status": 409})
    response = await client.post(f"/links/{LID}/block", data={"csrf_token": session.csrf_token, "reason": "again"})
    assert response.status_code == 409 and "Link is already blocked" in response.text


async def test_unblock(client, mocks, ids, login_as):
    session = await login_as("alice", ("admin",))
    route = mocks.post(f"{ids['API']}/api/v1/links/{LID}/unblock").respond(json=LINK)
    response = await client.post(f"/links/{LID}/unblock", data={"csrf_token": session.csrf_token})
    assert response.status_code == 303 and route.called


async def test_detail_renders_chart_referrers_and_data_as_of(client, mocks, ids, login_as):
    await login_as()
    detail_routes(mocks, ids)
    page = (await client.get(f"/links/{LID}")).text
    assert "<strong>5</strong> clicks" in page
    assert "Data as of 2026-10-01 11:59:00 UTC" in page
    assert "news.example" in page and "<b>x</b>" not in page and "&lt;b&gt;x&lt;/b&gt;" in page


async def test_chart_data_attribute_is_escaped(client, mocks, ids, login_as):
    await login_as()
    detail_routes(mocks, ids)
    page = (await client.get(f"/links/{LID}")).text
    [raw] = re.findall(r"data-chart='([^']*)'", page)  # attribute value contains no raw single quote
    assert json.loads(html_lib.unescape(raw)) == {"labels": ["10-01 10:00", "10-01 11:00"], "counts": [3, 2]}


async def test_stats_partial_for_htmx_with_day_bucket(client, mocks, ids, login_as, clock):
    await login_as()
    route = mocks.get(f"{ids['API']}/api/v1/links/{LID}/stats").respond(json=STATS | {"bucket": "day"})
    response = await client.get(f"/links/{LID}/stats", params={"bucket": "day"}, headers={"HX-Request": "true"})
    assert response.text.lstrip().startswith('<div id="stats"')
    params = dict(route.calls.last.request.url.params)
    assert params["bucket"] == "day"
    assert params["from"] == (clock() - timedelta(days=30)).isoformat()


async def test_stats_without_htmx_redirects_to_detail(client, login_as):
    await login_as()
    response = await client.get(f"/links/{LID}/stats", params={"bucket": "nonsense"})
    assert response.status_code == 303 and response.headers["location"] == f"/links/{LID}?bucket=hour"


async def test_stats_failure_does_not_break_the_detail_page(client, mocks, ids, login_as):
    await login_as()
    mocks.get(f"{ids['API']}/api/v1/links/{LID}").respond(json=LINK)
    mocks.get(f"{ids['API']}/api/v1/links/{LID}/stats").respond(422, json={"title": "Invalid range", "status": 422})
    response = await client.get(f"/links/{LID}")
    assert response.status_code == 200 and 'id="stats"' not in response.text
```

Run: `uv run pytest admin/tests/unit/test_moderation_and_stats.py -q`
Expected: FAIL (the moderation and stats routes don't exist, and the detail page has no chart yet).

- [ ] **Step 2: Implement**

Add `from datetime import UTC, datetime` to `views.py`'s imports, then append:
```python
def chart_data(stats: dict[str, Any], bucket: str) -> dict[str, list[Any]]:
    fmt = "%m-%d %H:00" if bucket == "hour" else "%Y-%m-%d"
    labels: list[Any] = []
    counts: list[Any] = []
    for point in stats.get("series", []):
        moment = datetime.fromisoformat(str(point["ts"]).replace("Z", "+00:00")).astimezone(UTC)
        labels.append(moment.strftime(fmt))
        counts.append(int(point["count"]))
    return {"labels": labels, "counts": counts}
```
In `routes/links.py`:
- Add the imports `from datetime import timedelta` and `from shortener_admin.views import chart_data`.
- Add `BUCKETS = ("hour", "day")`.
- Add the helper:
```python
async def _stats(deps: AdminDeps, session: Session, link_id: str, bucket: str) -> dict[str, Any] | None:
    start = deps.clock() - timedelta(days=30) if bucket == "day" else None
    try:
        stats: dict[str, Any] = await deps.api.link_stats(
            session.tokens.access_token, link_id, bucket=bucket, start=start
        )
    except ApiError as exc:
        if exc.status in PASS_THROUGH:
            raise
        return None
    return stats
```
- Replace `_detail` with this version, which fetches the stats and builds the chart data:
```python
async def _detail(
    request: Request, deps: AdminDeps, session: Session, link_id: str, *,
    notice: str | None = None, error: ApiError | None = None, bucket: str = "hour",
) -> HTMLResponse:  # fmt: skip
    link = await deps.api.get_link(session.tokens.access_token, link_id)
    stats = await _stats(deps, session, link_id, bucket)
    return render(
        request, "link_detail.html", status_code=error.status if error else 200, link=link, notice=notice,
        error=api_message(error) if error else None, can_edit=_can_edit(session, link),
        can_block=session.is_admin, stats=stats, chart=chart_data(stats, bucket) if stats else None,
        bucket=bucket,
    )  # fmt: skip
```
- In `link_detail`, compute `bucket = params.get("bucket") if params.get("bucket") in BUCKETS else "hour"` and call `_detail(request, deps, session, lid, notice=notice, bucket=bucket)`.
- Add the routes:
```python
@router.get("/links/{link_id}/stats")
async def link_stats(request: Request, link_id: str, deps: Deps, session: Access, bucket: str = "hour") -> Response:
    lid = _uuid_or_none(link_id)
    if lid is None:
        return _not_found(request)
    bucket = bucket if bucket in BUCKETS else "hour"
    if not is_htmx(request):
        return RedirectResponse(f"/links/{lid}?bucket={bucket}", status_code=303)
    stats = await _stats(deps, session, lid, bucket)
    return render(request, "partials/link_stats.html", link={"id": lid}, stats=stats, bucket=bucket,
                  chart=chart_data(stats, bucket) if stats else None)  # fmt: skip


@router.post("/links/{link_id}/block")
async def block_link(
    request: Request, link_id: str, deps: Deps, session: Mutate, reason: Annotated[str, Form()] = ""
) -> Response:
    lid = _uuid_or_none(link_id)
    if lid is None:
        return _not_found(request)
    token = session.tokens.access_token
    return await _act(request, deps, session, lid, lambda: deps.api.block_link(token, lid, reason),
                      f"/links/{lid}?updated=1")  # fmt: skip


@router.post("/links/{link_id}/unblock")
async def unblock_link(request: Request, link_id: str, deps: Deps, session: Mutate) -> Response:
    lid = _uuid_or_none(link_id)
    if lid is None:
        return _not_found(request)
    token = session.tokens.access_token
    return await _act(request, deps, session, lid, lambda: deps.api.unblock_link(token, lid),
                      f"/links/{lid}?updated=1")  # fmt: skip
```

`admin/src/shortener_admin/templates/partials/link_stats.html`:
```html
<div id="stats">
{% if stats %}
<form hx-get="/links/{{ link.id }}/stats" hx-target="#stats" hx-swap="outerHTML" hx-trigger="change">
  <label>Range
    <select name="bucket">
      <option value="hour"{% if bucket == "hour" %} selected{% endif %}>Last 7 days, hourly</option>
      <option value="day"{% if bucket == "day" %} selected{% endif %}>Last 30 days, daily</option>
    </select>
  </label>
</form>
<p><strong>{{ stats.total }}</strong> clicks</p>
<canvas data-chart='{{ chart | tojson }}' height="120" aria-label="Clicks over time" role="img"></canvas>
<h3>Top referrers</h3>
{% if stats.top_referrers %}
<table>
  <thead><tr><th>Referrer</th><th>Clicks</th></tr></thead>
  <tbody>
  {% for row in stats.top_referrers %}<tr><td>{{ row.referrer_host }}</td><td>{{ row.count }}</td></tr>{% endfor %}
  </tbody>
</table>
{% else %}
<p class="muted">No referrers yet.</p>
{% endif %}
<p class="muted"><small>{% if stats.data_as_of %}Data as of {{ stats.data_as_of | as_of }}{% else %}No clicks processed yet{% endif %}</small></p>
{% else %}
<p class="muted">Stats are unavailable right now.</p>
{% endif %}
</div>
```
`tojson` escapes `'`, `<`, `>` and `&`, so `data-chart='…'` is safe even if a label ever carried user data. `app.js` (Task 5) builds the chart from that attribute on page load and after every HTMX swap.

Run: `uv run pytest admin/tests -q`
Expected: all PASS. `test_stats_failure_does_not_break_the_detail_page` checks that the `id="stats"` wrapper is absent: `link_detail.html` includes the partial only `{% if stats %}`.

- [ ] **Step 3: Gates and commit**

```bash
uv run ruff format . && make check && git add admin && git commit -m "feat(admin): block/unblock moderation and the click chart with hour/day ranges

Co-Authored-By: <implementing model> <noreply@anthropic.com>"
```

---
### Task 9: Production wiring, compose service, CI, end-to-end login, docs

**Files:**
- Modify: `admin/src/shortener_admin/main.py` (`build_deps`, `create_app_from_env`)
- Create: `admin/Dockerfile`, `tests/e2e/test_admin.py`, `admin/tests/unit/test_main.py`
- Modify: `docker-compose.yml` (the `admin` service), `Makefile` (`up`), `.env.example` (`ADMIN_COOKIE_SECRET`), `.github/workflows/ci.yml` (images matrix), `tests/e2e/conftest.py` (`admin_url`), `README.md`, `docs/superpowers/specs/2026-10-01-url-shortener-design.md`

**Interfaces:**
- Produces:
  - `main.build_deps(settings) -> AdminDeps`, with no network I/O at construction. It wires:
    - `PostgresSessionStore`, on an engine with `connect_timeout=5` and `statement_timeout=5000`
    - `KeycloakOidc`
    - `ApiClient`, over `httpx.AsyncClient(base_url=api_base_url, timeout=http_timeout_seconds)`
    - a UTC clock
  - `main.create_app_from_env() -> FastAPI`, the uvicorn `--factory` entrypoint
  - compose service `admin` on `localhost:8001`
  - `E2ESettings.admin_url = "http://localhost:8001"`

- [ ] **Step 1: Write the failing wiring tests**

`admin/tests/unit/test_main.py`:
```python
import pytest

from shortener_admin.api_client import ApiClient
from shortener_admin.main import build_deps, create_app_from_env
from shortener_admin.oidc import KeycloakOidc
from shortener_admin.sessions import PostgresSessionStore

ENV = {
    "DATABASE_URL": "postgresql+psycopg://admin_user:pw@127.0.0.1:1/shortener",
    "API_BASE_URL": "http://api:8000",
    "PUBLIC_BASE_URL": "http://localhost:8001",
    "OIDC_INTERNAL_URL": "http://keycloak:8080/realms/shortener",
    "OIDC_CLIENT_SECRET": "secret",
    "COOKIE_SECRET": "c" * 32,
}


def test_build_deps_uses_real_components_without_network(settings):
    deps = build_deps(settings)
    assert isinstance(deps.sessions, PostgresSessionStore)
    assert isinstance(deps.oidc, KeycloakOidc)
    assert isinstance(deps.api, ApiClient)
    assert deps.clock().tzinfo is not None


def test_create_app_from_env(monkeypatch):
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    assert create_app_from_env().title == "Shortener admin"


def test_create_app_from_env_fails_fast(monkeypatch):
    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(Exception, match="database_url"):
        create_app_from_env()
```

Run: `uv run pytest admin/tests/unit/test_main.py -q`
Expected: FAIL with `ImportError: cannot import name 'build_deps'`.

- [ ] **Step 2: Implement the production wiring**

Append to `main.py`. Merge these into its imports: `from datetime import UTC, datetime`, `import httpx`, `from sqlalchemy.ext.asyncio import create_async_engine`, `from shortener_admin.api_client import ApiClient`, `from shortener_admin.oidc import KeycloakOidc`, `from shortener_admin.sessions import PostgresSessionStore`, `from shortener_admin.settings import AdminSettings, load_admin_settings`.
```python
def utc_now() -> datetime:
    return datetime.now(UTC)


def build_deps(settings: AdminSettings) -> AdminDeps:
    """Real implementations. Nothing here touches the network until first use."""
    engine = create_async_engine(
        str(settings.database_url),
        pool_pre_ping=True,
        connect_args={"connect_timeout": 5, "options": "-c statement_timeout=5000"},
    )
    api_http = httpx.AsyncClient(base_url=str(settings.api_base_url), timeout=settings.http_timeout_seconds)
    return AdminDeps(
        settings=settings,
        sessions=PostgresSessionStore(engine),
        oidc=KeycloakOidc(settings, clock=utc_now),
        api=ApiClient(api_http),
        clock=utc_now,
        templates=make_templates(),
    )


def create_app_from_env() -> FastAPI:
    """uvicorn --factory entrypoint."""
    return create_app(build_deps(load_admin_settings()))
```

Run: `uv run pytest admin/tests -q`
Expected: all PASS.

- [ ] **Step 3: Image, compose, Make, CI, env**

`admin/Dockerfile`:
```dockerfile
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
COPY processor/pyproject.toml processor/
COPY admin/pyproject.toml admin/
COPY tools/keycloak-tools/pyproject.toml tools/keycloak-tools/
RUN uv sync --frozen --no-dev --no-install-workspace --package shortener-admin
COPY . .
RUN uv sync --frozen --no-dev --no-editable --package shortener-admin

FROM python:3.12-slim
ARG GIT_SHA=dev
ENV PATH=/app/.venv/bin:$PATH SERVICE_VERSION=$GIT_SHA PYTHONUNBUFFERED=1
RUN useradd --uid 10001 --no-create-home app
COPY --from=build /app/.venv /app/.venv
USER 10001
EXPOSE 8001
CMD ["uvicorn", "--factory", "shortener_admin.main:create_app_from_env", "--host", "0.0.0.0", "--port", "8001", "--proxy-headers", "--forwarded-allow-ips", "*"]
```
The non-editable install puts `templates/` and `static/` inside the installed package. Confirm with `docker run --rm --entrypoint python shortener-admin:dev -c "import shortener_admin, pathlib; p = pathlib.Path(shortener_admin.__file__).parent; print(sorted(x.name for x in (p / 'static' / 'vendor').iterdir()))"`. It should list the three vendored files. If they're missing, hatchling excluded them. In that case add `[tool.hatch.build.targets.wheel] artifacts = ["src/shortener_admin/templates", "src/shortener_admin/static"]` to `admin/pyproject.toml` and rebuild.

`.env.example`: add, after `SHORTENER_ADMIN_CLIENT_SECRET`:
```dotenv
# Signs the admin UI's short-lived login handshake cookie (>= 32 chars).
ADMIN_COOKIE_SECRET=dev-only-admin-cookie-secret-change-me-0001
```
Copy the same line into your local `.env`.

`docker-compose.yml`, add after `click-processor`:
```yaml
  admin:
    build:
      context: .
      dockerfile: admin/Dockerfile
      args:
        GIT_SHA: ${GIT_SHA:-dev}
    image: shortener-admin:${GIT_SHA:-dev}
    environment:
      DATABASE_URL: postgresql+psycopg://admin_user:${ADMIN_DB_PASSWORD:?set ADMIN_DB_PASSWORD in .env (see .env.example)}@postgres:5432/shortener
      API_BASE_URL: http://api:8000
      PUBLIC_BASE_URL: http://localhost:8001
      OIDC_INTERNAL_URL: http://keycloak:8080/realms/shortener
      OIDC_CLIENT_ID: shortener-admin
      OIDC_CLIENT_SECRET: ${SHORTENER_ADMIN_CLIENT_SECRET:?set SHORTENER_ADMIN_CLIENT_SECRET in .env (see .env.example)}
      COOKIE_SECRET: ${ADMIN_COOKIE_SECRET:?set ADMIN_COOKIE_SECRET in .env (see .env.example)}
      COOKIE_SECURE: "false"   # local http only; true behind TLS
      SERVICE_VERSION: ${GIT_SHA:-dev}
    ports:
      - "8001:8001"
    depends_on:
      postgres:
        condition: service_healthy
      migrate:
        condition: service_completed_successfully
      keycloak:
        condition: service_healthy
      api:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8001/readyz', timeout=2)"]
      interval: 5s
      timeout: 3s
      retries: 20
      start_period: 5s
```

`Makefile`: change the last line of `up` to `$(COMPOSE) up -d --build --wait api click-processor admin`.

`.github/workflows/ci.yml`: add `- admin/Dockerfile` to the `images` matrix.

Run:
```bash
docker build -q -f admin/Dockerfile . && make up && uv run pytest tests/test_env_example.py -q
curl -s -o /dev/null -w '%{http_code} %{redirect_url}\n' 'http://localhost:8001/'
```
Expected: the image builds, the stack is healthy, and the env contract passes. The curl prints `303 http://localhost:8001/auth/login?next=/`.

- [ ] **Step 4: Write the end-to-end login scenarios (real Keycloak form)**

Add to `E2ESettings` in `tests/e2e/conftest.py`:
```python
    admin_url: str = "http://localhost:8001"
```

`tests/e2e/test_admin.py`:
```python
"""Admin UI end to end over HTTP: real Keycloak login form, real API, real Postgres sessions."""

import html
import re

import httpx
import pytest

pytestmark = pytest.mark.e2e
CSRF = re.compile(r'name="csrf_token" value="([^"]+)"')


def keycloak_login(client: httpx.Client, admin_url: str, username: str, next_path: str = "/") -> httpx.Response:
    """Follow /auth/login to Keycloak, submit its login form, and land back on the admin UI."""
    page = client.get(f"{admin_url}/auth/login", params={"next": next_path}, follow_redirects=True)
    form_tag = re.search(r'<form[^>]*\bid="kc-form-login"[^>]*>', page.text)
    assert form_tag, f"Keycloak login form not found (status {page.status_code})"
    action = html.unescape(re.search(r'action="([^"]+)"', form_tag.group(0)).group(1))
    return client.post(action, data={"username": username, "password": "password"}, follow_redirects=True)


@pytest.fixture
def browser():
    with httpx.Client(timeout=15) as client:
        yield client


def csrf_of(page: httpx.Response) -> str:
    match = CSRF.search(page.text)
    assert match, "no CSRF token on page"
    return match.group(1)


def test_eddie_logs_in_and_lands_on_next(browser, e2e_settings):
    landed = keycloak_login(browser, e2e_settings.admin_url, "eddie", "/links")
    assert landed.status_code == 200
    assert str(landed.url) == f"{e2e_settings.admin_url}/links"
    assert "eddie" in landed.text and "Sign out" in landed.text


def test_editor_creates_and_deletes_a_link_through_the_ui(browser, e2e_settings):
    base = e2e_settings.admin_url
    form = keycloak_login(browser, base, "eddie", "/links/new")
    created = browser.post(f"{base}/links", data={"csrf_token": csrf_of(form), "target_url": "https://example.com/ui"},
                           follow_redirects=True)  # fmt: skip
    assert created.status_code == 200 and "Short link created" in created.text
    link_path = created.url.path
    deleted = browser.post(f"{base}{link_path}/delete", data={"csrf_token": csrf_of(created)}, follow_redirects=True)
    assert "Link deleted" in deleted.text


def test_post_without_csrf_is_rejected(browser, e2e_settings):
    keycloak_login(browser, e2e_settings.admin_url, "eddie")
    response = browser.post(f"{e2e_settings.admin_url}/links", data={"target_url": "https://example.com/x"})
    assert response.status_code == 403


def test_user_without_roles_sees_no_access(browser, e2e_settings):
    landed = keycloak_login(browser, e2e_settings.admin_url, "nora")
    assert landed.status_code == 403 and "don't have access" in landed.text


def test_viewer_cannot_create(browser, e2e_settings):
    keycloak_login(browser, e2e_settings.admin_url, "victor")
    assert browser.get(f"{e2e_settings.admin_url}/links/new").status_code == 403


def test_logout_ends_the_session(browser, e2e_settings):
    base = e2e_settings.admin_url
    home = keycloak_login(browser, base, "eddie")
    out = browser.post(f"{base}/auth/logout", data={"csrf_token": csrf_of(home)})
    assert out.status_code == 303 and "/protocol/openid-connect/logout" in out.headers["location"]
    after = browser.get(f"{base}/")
    assert after.status_code == 303 and after.headers["location"].startswith("/auth/login")
```
The admin UI and Keycloak share the host `localhost` on different ports, so one `httpx.Client` cookie jar carries both sets of cookies. The names don't collide (`sid` and `login_state` versus Keycloak's `KEYCLOAK_*`/`AUTH_SESSION_ID`).

Run: `make e2e`
Expected: every e2e test passes, including the six new admin scenarios. If `keycloak_login` can't find `kc-form-login`, save `page.text` to the scratchpad and check the theme's form id. Keycloak 26's default login theme uses `kc-form-login`; if yours differs, update the regex.

- [ ] **Step 5: Docs**

`README.md`:
- Add the services-table row `| Admin UI | http://localhost:8001 | Sign in as a seeded user (password \`password\`) |`.
- Add a short `## Admin UI` section:
  - Sign in at http://localhost:8001 as `alice` (admin), `eddie`/`erin` (editor), `victor` (viewer) or `nora` (no access).
  - Every action goes through the API with your own token, so what you can do is exactly what the API allows.
  - Sessions are server-side in `admin.sessions`; the browser only holds an opaque `sid`.

Spec `docs/superpowers/specs/2026-10-01-url-shortener-design.md`:
- **§7.4 step 1:** replace "Authlib builds the authorization request (state, nonce, PKCE)." with "Authlib (Starlette client, Keycloak discovery over the internal URL) builds the authorization request (state, nonce, PKCE S256). That handshake data, plus the post-login path, lives in a signed, 10-minute `login_state` cookie scoped to `/auth` (Starlette `SessionMiddleware`). It never holds tokens."
- **§7.4 step 2:** append "The user's roles come from the API's `GET /api/v1/me`, called with the new access token, not from decoding the token."
- **§7.4 step 3:** append "Roles are re-read from `/me` on every refresh."
- **§7.4 step 4:** change "`/auth/logout`" to "`POST /auth/logout` (CSRF-checked)".
- **§3.3:** replace the `admin/` block's `src/` list with the files this plan created (`settings.py`, `security.py`, `sessions.py`, `db.py`, `oidc.py`, `api_client.py`, `deps.py`, `auth.py`, `views.py`, `main.py`, `routes/{auth,health,pages,links}.py`).
- **§12**, add these items:
  - **httpx → httpx2 migration:** Authlib 1.8 already prefers `httpx2`, and the repo silences its deprecation warning. Migrate every package at once when respx (or a replacement) supports `httpx2`, and review `httpx2`'s behaviour changes (for example, it trusts the OS certificate store via `truststore`).
  - **Encrypt session tokens at rest in `admin.sessions`:** use envelope encryption with a KMS data key. Today they rely on database encryption at rest.
  - **Show `trace_id` on admin error pages:** this comes with tracing in Plan 5.

- [ ] **Step 6: Final gates and commit**

```bash
uv run ruff format . && make check && make e2e && git add admin docker-compose.yml Makefile .env.example .github/workflows/ci.yml tests/e2e README.md docs/superpowers/specs/2026-10-01-url-shortener-design.md && git commit -m "feat(admin): production wiring, compose service, CI image, e2e login through Keycloak, docs

Co-Authored-By: <implementing model> <noreply@anthropic.com>"
```
`.env` is git-ignored, so update your local copy by hand.

---

## Plan 4 Done When

- `make down && make up` brings up the stack with `admin` healthy on `:8001`, and `/` redirects to sign-in.
- `make check` passes: lint, `mypy --strict`, tests, coverage ≥ 80% overall, and ≥ 90% on `security.py` and the existing pure modules. The vendored-asset hash test passes.
- `make e2e` passes. It shows:
  - a real Keycloak form login landing on `next`
  - creating and deleting a link through the UI
  - CSRF enforcement
  - no-access and viewer limits
  - a logout that ends the session
- Spec §3.3, §7.4 and §12 reflect the deviations and follow-ups listed at the top of this plan.
