import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from shortener_api.auth import InvalidTokenError, StaticJwksProvider, TokenValidator

ISSUER = "http://localhost:8080/realms/shortener"


@pytest.fixture
def validator(signing_key):
    return TokenValidator(
        StaticJwksProvider({"test-key": signing_key.public_key()}), ISSUER, "shortener-api"
    )


async def test_valid_token_yields_principal_with_roles(validator, mint_token):
    principal = await validator.principal(
        mint_token("sub-1", "eddie", ["editor", "offline_access"])
    )
    assert principal.sub == "sub-1"
    assert principal.username == "eddie"
    assert principal.roles == frozenset({"editor", "offline_access"})


async def test_audience_may_be_a_list(validator, mint_token):
    token = mint_token(aud=["account", "shortener-api"])
    assert (await validator.principal(token)).sub == "sub-eddie"


async def test_missing_realm_access_means_no_roles(validator, mint_token):
    principal = await validator.principal(mint_token(drop=["realm_access"]))
    assert principal.roles == frozenset()


async def test_missing_username_falls_back_to_sub(validator, mint_token):
    principal = await validator.principal(mint_token(drop=["preferred_username"]))
    assert principal.username == "sub-eddie"


async def test_expiry_within_leeway_is_accepted(validator, mint_token):
    assert await validator.principal(mint_token(expires_in=-10))


@pytest.mark.parametrize(
    "overrides",
    [
        {"expires_in": -120},
        {"aud": "account"},
        {"iss": "http://keycloak:8080/realms/shortener"},
        {"iss": ISSUER + "/"},
        {"kid": "unknown-key"},
        {"drop": ["sub"]},
        {"drop": ["exp"]},
    ],
    ids=["expired", "wrong-aud", "internal-issuer", "issuer-slash", "unknown-kid", "no-sub",
         "no-exp"],
)  # fmt: skip
async def test_rejected_tokens(validator, mint_token, overrides):
    with pytest.raises(InvalidTokenError):
        await validator.principal(mint_token(**overrides))


async def test_token_signed_by_another_key_is_rejected(validator, mint_token):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(InvalidTokenError):
        await validator.principal(mint_token(key=other))


@pytest.mark.filterwarnings("ignore::jwt.warnings.InsecureKeyLengthWarning")
async def test_hs256_token_is_rejected(validator, mint_token):
    with pytest.raises(InvalidTokenError):
        await validator.principal(mint_token(key="guessable-secret", algorithm="HS256"))


async def test_alg_none_token_is_rejected(validator):
    token = jwt.encode(
        {"sub": "x", "iss": ISSUER, "aud": "shortener-api", "exp": 9999999999},
        key=None,
        algorithm="none",
        headers={"kid": "test-key"},
    )
    with pytest.raises(InvalidTokenError):
        await validator.principal(token)


async def test_garbage_is_rejected(validator):
    with pytest.raises(InvalidTokenError):
        await validator.principal("not-a-jwt")


async def test_me_requires_a_token(client):
    response = await client.get("/api/v1/me")
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Bearer")
    assert response.headers["content-type"] == "application/problem+json"


async def test_me_rejects_an_invalid_token(client):
    response = await client.get("/api/v1/me", headers={"Authorization": "Bearer nope"})
    assert response.status_code == 401
    assert 'error="invalid_token"' in response.headers["www-authenticate"]


async def test_me_returns_managed_roles_only(client, token_for):
    response = await client.get("/api/v1/me", headers=token_for("alice"))
    assert response.status_code == 200
    assert response.json() == {"sub": "sub-alice", "username": "alice", "roles": ["admin"]}


async def test_me_works_for_a_user_without_roles(client, token_for):
    response = await client.get("/api/v1/me", headers=token_for("nora"))
    assert response.status_code == 200
    assert response.json()["roles"] == []
