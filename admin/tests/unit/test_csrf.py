from typing import Annotated, Any

import pytest
from fastapi import Depends

from shortener_admin.auth import verify_csrf


@pytest.fixture
def probe(app):
    @app.post("/__mutate")
    async def _mutate(session: Annotated[Any, Depends(verify_csrf)]):
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
    assert (
        await client.post("/__mutate", data={"csrf_token": other.csrf_token})
    ).status_code == 403


async def test_csrf_header_is_accepted(probe, client, login_as):
    session = await login_as()
    assert (
        await client.post("/__mutate", headers={"X-CSRF-Token": session.csrf_token})
    ).status_code == 200


async def test_csrf_form_field_is_accepted(probe, client, login_as):
    session = await login_as()
    assert (
        await client.post("/__mutate", data={"csrf_token": session.csrf_token})
    ).status_code == 200
