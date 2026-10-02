"""The conditional-write race: an admin blocks between the owner's read and write (spec §4)."""

import random
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from shortener_api.errors import ProblemError
from shortener_api.links_repo import Link
from shortener_api.policy import Principal
from shortener_api.service import LinkService
from shortener_api.telemetry import ApiTelemetry

T0 = datetime(2026, 10, 1, tzinfo=UTC)
OWNER = Principal("sub-eddie", "eddie", frozenset({"editor"}))
ADMIN = Principal("sub-alice", "alice", frozenset({"admin"}))


class RacingRepo:
    """get() first shows the link unblocked; once a write was attempted it shows it blocked.

    Conditional writes (require_unblocked=True) match nothing, like a row an admin just blocked.
    """

    def __init__(self) -> None:
        self.link = Link(
            id=uuid4(),
            code="aZ3kQ9x",
            target_url="https://example.com",
            owner_sub="sub-eddie",
            owner_username="eddie",
            is_active=True,
            blocked_at=None,
            blocked_by=None,
            blocked_reason=None,
            created_at=T0,
            updated_at=T0,
        )
        self.write_flags: list[bool] = []
        self._written = False

    async def get(self, link_id):
        if not self._written:
            return self.link
        return replace(self.link, blocked_at=T0, blocked_by="sub-alice", blocked_reason="phishing")

    async def update(
        self, link_id, *, now, target_url=None, is_active=None, require_unblocked=False
    ):
        self.write_flags.append(require_unblocked)
        self._written = True
        return None if require_unblocked else self.link

    async def delete(self, link_id, *, actor, now, require_unblocked=False):
        self.write_flags.append(require_unblocked)
        self._written = True
        return not require_unblocked


@pytest.fixture
def repo() -> RacingRepo:
    return RacingRepo()


@pytest.fixture
def service(repo, clock, meter) -> LinkService:
    return LinkService(
        repo,  # type: ignore[arg-type]
        clock=clock,
        rng=random.Random(1),
        public_base_url="http://sho.rt",
        telemetry=ApiTelemetry(meter),
    )


async def test_owner_patch_losing_the_race_is_409_with_reason(service, repo):
    with pytest.raises(ProblemError) as caught:
        await service.update(OWNER, repo.link.id, target_url=None, is_active=False)
    assert caught.value.status == 409
    assert caught.value.extra == {"blocked_reason": "phishing"}
    assert repo.write_flags == [True]


async def test_owner_delete_losing_the_race_is_409_with_reason(service, repo):
    with pytest.raises(ProblemError) as caught:
        await service.delete(OWNER, repo.link.id)
    assert caught.value.status == 409
    assert caught.value.extra == {"blocked_reason": "phishing"}
    assert repo.write_flags == [True]


async def test_admin_writes_are_unconditional(service, repo):
    await service.update(ADMIN, repo.link.id, target_url=None, is_active=False)
    await service.delete(ADMIN, repo.link.id)
    assert repo.write_flags == [False, False]
