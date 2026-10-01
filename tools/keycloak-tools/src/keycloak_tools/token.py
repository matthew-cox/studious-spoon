"""DEV ONLY: fetch an access token for a seeded user via the `shortener-dev` client."""

import httpx


class TokenError(RuntimeError):
    pass


def _error_detail(response: httpx.Response) -> str:
    try:
        return str(response.json().get("error_description", response.text))
    except ValueError:
        return response.text


def fetch_token(
    base_url: str,
    realm: str,
    username: str,
    password: str,
    client_id: str = "shortener-dev",
    http: httpx.Client | None = None,
) -> str:
    client = http or httpx.Client(timeout=10.0)
    response = client.post(
        f"{base_url}/realms/{realm}/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": client_id,
            "username": username,
            "password": password,
            "scope": "openid",
        },
    )
    if response.status_code != 200:
        raise TokenError(
            f"token request for {username!r} failed ({response.status_code}): "
            f"{_error_detail(response)}"
        )
    return str(response.json()["access_token"])
