"""Admin-only "Observability" nav link to the configured Grafana dashboard."""

import pytest

OBSERVABILITY = "http://localhost:3000/d/shortener-overview"
SUMMARY = {"link_count": 0, "clicks_7d": 0, "top_links": [], "data_as_of": None}
LINK_HTML = f'<a href="{OBSERVABILITY}" target="_blank" rel="noopener">Observability ↗</a>'


@pytest.fixture
def settings(settings):
    return settings.model_copy(update={"observability_url": OBSERVABILITY})


async def dashboard_html(client, mocks, ids) -> str:
    mocks.get(f"{ids['API']}/api/v1/stats/summary").respond(json=SUMMARY)
    response = await client.get("/")
    assert response.status_code in (200, 403)  # 403: the no-access page for users without roles
    return response.text


async def test_admin_sees_the_observability_link(client, mocks, ids, login_as):
    await login_as("alice", ("admin",))
    assert LINK_HTML in await dashboard_html(client, mocks, ids)


@pytest.mark.parametrize("roles", [("editor",), ("viewer",), ()])
async def test_non_admins_never_see_it(client, mocks, ids, login_as, roles):
    await login_as("someone", roles)
    assert "Observability" not in await dashboard_html(client, mocks, ids)


async def test_signed_out_pages_never_show_it(client):
    response = await client.get("/auth/callback", params={"code": "c", "state": "forged"})
    assert "Observability" not in response.text


async def test_hidden_when_not_configured(client, mocks, ids, login_as, deps):
    deps.settings = deps.settings.model_copy(update={"observability_url": None})
    await login_as("alice", ("admin",))
    assert "Observability" not in await dashboard_html(client, mocks, ids)
