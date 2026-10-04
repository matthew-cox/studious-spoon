from datetime import UTC, datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PlainSerializer,
    StringConstraints,
    model_validator,
)

from shortener_api.links_repo import Link, LinkEvent, LinkEventAction, LinkStatus, Page


class MeOut(BaseModel):
    sub: str
    username: str
    roles: list[str]


class LinkCreate(BaseModel):
    target_url: str = Field(max_length=4096)


class LinkUpdate(BaseModel):
    target_url: str | None = Field(default=None, max_length=4096)
    is_active: bool | None = None

    @model_validator(mode="after")
    def _something_to_change(self) -> Self:
        if self.target_url is None and self.is_active is None:
            raise ValueError("provide target_url and/or is_active")
        return self


class ModerationRequest(BaseModel):
    """Body for block and unblock: why, recorded in the moderation history."""

    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class LinkOut(BaseModel):
    id: UUID
    code: str
    short_url: str
    target_url: str
    owner_username: str
    status: LinkStatus
    is_active: bool
    blocked_at: datetime | None
    blocked_reason: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, link: Link, public_base_url: str) -> "LinkOut":
        return cls(
            id=link.id,
            code=link.code,
            short_url=f"{public_base_url.rstrip('/')}/{link.code}",
            target_url=link.target_url,
            owner_username=link.owner_username,
            status=link.status,
            is_active=link.is_active,
            blocked_at=link.blocked_at,
            blocked_reason=link.blocked_reason,
            created_at=link.created_at,
            updated_at=link.updated_at,
        )


class LinkEventOut(BaseModel):
    """A moderation action on a link. Moderators only: it names who acted."""

    id: int
    link_id: UUID
    link_code: str
    action: LinkEventAction
    actor_username: str
    reason: str | None
    occurred_at: datetime

    @classmethod
    def of(cls, event: LinkEvent) -> "LinkEventOut":
        return cls(
            id=event.id,
            link_id=event.link_id,
            link_code=event.link_code,
            action=event.action,
            actor_username=event.actor_username,
            reason=event.reason,
            occurred_at=event.occurred_at,
        )


class LinkPage(BaseModel):
    items: list[LinkOut]
    total: int
    page: int
    page_size: int

    @classmethod
    def of(cls, page: Page[Link], public_base_url: str) -> "LinkPage":
        return cls(
            items=[LinkOut.of(link, public_base_url) for link in page.items],
            total=page.total,
            page=page.page,
            page_size=page.page_size,
        )


UtcDatetime = Annotated[
    datetime,
    PlainSerializer(lambda v: v.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), return_type=str),
]


class SeriesPoint(BaseModel):
    ts: UtcDatetime
    count: int


class ReferrerCount(BaseModel):
    referrer_host: str
    count: int


class LinkStatsOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    total: int
    bucket: Literal["hour", "day"]
    from_: UtcDatetime = Field(serialization_alias="from")
    to: UtcDatetime
    series: list[SeriesPoint]
    top_referrers: list[ReferrerCount]
    data_as_of: UtcDatetime | None


class TopLinkOut(BaseModel):
    id: UUID
    code: str
    clicks_7d: int


class SummaryOut(BaseModel):
    link_count: int
    clicks_7d: int
    top_links: list[TopLinkOut]
    data_as_of: UtcDatetime | None
