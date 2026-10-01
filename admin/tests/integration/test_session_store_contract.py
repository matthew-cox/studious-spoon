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
def store(request):
    if request.param == "memory":
        return InMemorySessionStore()
    return PostgresSessionStore(request.getfixturevalue("admin_engine"))


async def make(store, roles=frozenset({"editor"}), **kw):
    return await store.create(
        sub="sub-eddie", username="eddie", roles=roles, tokens=tokens(**kw), now=NOW
    )


async def test_create_then_get(store):
    created = await make(store)
    assert len(created.id) >= 43 and len(created.csrf_token) >= 43
    assert created.id != created.csrf_token
    fetched = await store.get(created.id, NOW + timedelta(seconds=5))
    assert fetched is not None
    assert (fetched.sub, fetched.username, fetched.roles) == (
        "sub-eddie",
        "eddie",
        frozenset({"editor"}),
    )
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
