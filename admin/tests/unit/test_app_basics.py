import hashlib
import re
from pathlib import Path
from typing import Annotated, Any

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
    for path in (
        "vendor/htmx.min.js",
        "vendor/pico.min.css",
        "vendor/chart.umd.js",
        "app.js",
        "app.css",
    ):
        assert (await client.get(f"/static/{path}")).status_code == 200


def test_vendored_assets_match_recorded_hashes():
    table = (STATIC / "VENDORED.md").read_text()
    rows = re.findall(r"\| (vendor/\S+) \| [\d.]+ \| \S+ \| `([0-9a-f]{64})` \|", table)
    assert {name for name, _ in rows} == {
        "vendor/htmx.min.js",
        "vendor/pico.min.css",
        "vendor/chart.umd.js",
    }
    for name, digest in rows:
        assert hashlib.sha256((STATIC / name).read_bytes()).hexdigest() == digest, name


async def test_base_template_carries_csrf_in_hx_headers(app, client, login_as):
    @app.get("/__page")
    async def _page(request: Request, session: Annotated[Any, Depends(require_access)]):
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
