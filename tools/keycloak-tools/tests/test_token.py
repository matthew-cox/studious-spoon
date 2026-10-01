import httpx
import pytest
import respx

from keycloak_tools.token import TokenError, fetch_token

URL = "http://kc.test/realms/shortener/protocol/openid-connect/token"


@respx.mock
def test_fetch_token_uses_password_grant_on_dev_client():
    route = respx.post(URL).respond(json={"access_token": "tok"})
    token = fetch_token("http://kc.test", "shortener", "alice", "password", http=httpx.Client())
    assert token == "tok"
    form = dict(httpx.QueryParams(route.calls.last.request.content.decode()))
    assert form == {
        "grant_type": "password",
        "client_id": "shortener-dev",
        "username": "alice",
        "password": "password",
        "scope": "openid",
    }


@respx.mock
def test_rejected_credentials_raise_token_error_with_keycloak_message():
    respx.post(URL).respond(
        401, json={"error": "invalid_grant", "error_description": "Invalid user credentials"}
    )
    with pytest.raises(TokenError, match="Invalid user credentials"):
        fetch_token("http://kc.test", "shortener", "alice", "wrong", http=httpx.Client())


@respx.mock
def test_non_json_error_body_still_raises_token_error():
    respx.post(URL).respond(502, text="Bad Gateway")
    with pytest.raises(TokenError, match="Bad Gateway"):
        fetch_token("http://kc.test", "shortener", "alice", "pw", http=httpx.Client())
