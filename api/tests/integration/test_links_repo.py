from datetime import timedelta
from uuid import uuid4

import pytest

from shortener_api.links_repo import CodeTakenError, LinkQuery, LinkRepository
from shortener_api.policy import Principal

ALICE = Principal("sub-alice", "alice", frozenset({"admin"}))

pytestmark = pytest.mark.integration


@pytest.fixture
def repo(engine):
    return LinkRepository(engine)


async def make(repo, clock, code="aaaaaaa", owner="sub-eddie", url="https://example.com/"):
    return await repo.insert(
        code=code, target_url=url, owner_sub=owner, owner_username=owner[4:], now=clock.now()
    )


async def test_insert_and_get(repo, clock):
    link = await make(repo, clock)
    assert link.code == "aaaaaaa"
    assert link.status == "active"
    assert link.created_at == link.updated_at == clock.now()
    assert await repo.get(link.id) == link
    assert await repo.get(uuid4()) is None


async def test_duplicate_code_raises_code_taken(repo, clock):
    await make(repo, clock)
    with pytest.raises(CodeTakenError):
        await make(repo, clock)


async def test_get_by_code_is_case_sensitive(repo, clock):
    await make(repo, clock, code="aZ3kQ9x")
    assert (await repo.get_by_code("aZ3kQ9x")) is not None
    assert await repo.get_by_code("aZ3kQ9X") is None


async def test_search_scopes_filters_and_paginates(repo, clock):
    for i in range(3):
        clock.advance(timedelta(seconds=1))
        await make(repo, clock, code=f"eddie0{i}", url=f"https://e.example/{i}")
    erin = await make(
        repo, clock, code="erin000", owner="sub-erin", url="https://x.example/50%_off"
    )
    page = await repo.search(LinkQuery(owner_sub="sub-eddie", page=1, page_size=2))
    assert page.total == 3
    assert [link.code for link in page.items] == ["eddie02", "eddie01"]  # newest first
    assert [
        link.code
        for link in (await repo.search(LinkQuery(owner_sub="sub-eddie", page=2, page_size=2))).items
    ] == ["eddie00"]
    assert (await repo.search(LinkQuery(q="50%"))).items == [erin]
    assert (await repo.search(LinkQuery(q="%"))).total == 1  # '%' is literal, not a wildcard
    assert (await repo.search(LinkQuery(q="E.EXAMPLE"))).total == 3  # case-insensitive


async def test_search_by_status(repo, clock):
    active = await make(repo, clock, code="active0")
    disabled = await make(repo, clock, code="disabl0")
    blocked = await make(repo, clock, code="blocke0")
    await repo.update(disabled.id, now=clock.now(), is_active=False)
    await repo.block(blocked.id, actor=ALICE, reason="spam", now=clock.now())
    found = (await repo.search(LinkQuery(status="active"))).items
    assert [link.id for link in found] == [active.id]
    found = (await repo.search(LinkQuery(status="disabled"))).items
    assert [link.id for link in found] == [disabled.id]
    found = (await repo.search(LinkQuery(status="blocked"))).items
    assert [link.id for link in found] == [blocked.id]


async def test_update_sets_fields_and_updated_at(repo, clock):
    link = await make(repo, clock)
    clock.advance(timedelta(minutes=1))
    updated = await repo.update(link.id, now=clock.now(), target_url="https://new.example/")
    assert updated is not None
    assert updated.target_url == "https://new.example/"
    assert updated.is_active is True
    assert updated.updated_at == clock.now()


async def test_owner_update_is_refused_once_blocked(repo, clock):
    link = await make(repo, clock)
    await repo.block(link.id, actor=ALICE, reason="phishing", now=clock.now())
    result = await repo.update(
        link.id, now=clock.now(), target_url="https://clean.example/", require_unblocked=True
    )
    assert result is None
    assert (await repo.get(link.id)).target_url == "https://example.com/"


async def test_owner_delete_is_refused_once_blocked(repo, clock):
    link = await make(repo, clock)
    await repo.block(link.id, actor=ALICE, reason="phishing", now=clock.now())
    assert await repo.delete(link.id, actor=ALICE, now=clock.now(), require_unblocked=True) is False
    assert await repo.get(link.id) is not None
    assert await repo.delete(link.id, actor=ALICE, now=clock.now()) is True  # the admin path
    assert await repo.get(link.id) is None


async def test_block_and_unblock_preserve_is_active(repo, clock):
    link = await make(repo, clock)
    await repo.update(link.id, now=clock.now(), is_active=False)
    blocked = await repo.block(link.id, actor=ALICE, reason="spam", now=clock.now())
    assert blocked.status == "blocked"
    assert blocked.facts().blocked is True
    assert await repo.block(link.id, actor=ALICE, reason="again", now=clock.now()) is None
    unblocked = await repo.unblock(link.id, actor=ALICE, now=clock.now())
    assert unblocked.status == "disabled"  # returns to the owner's previous choice
    assert unblocked.blocked_by is None and unblocked.blocked_reason is None
    assert await repo.unblock(link.id, actor=ALICE, now=clock.now()) is None
