from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

EVENT_TYPE = "link.clicked"
REFERRER_MAX = 1024
USER_AGENT_MAX = 512


def _truncate(value: str | None, limit: int) -> str | None:
    if not value:
        return None
    return value[:limit]


class ClickEvent(BaseModel):
    """A single redirect served to a visitor (`link.clicked`, version 1)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: Literal["link.clicked"] = "link.clicked"
    version: Literal[1] = 1
    event_id: str = Field(min_length=1, max_length=128)
    occurred_at: datetime
    source: Literal["api", "cloudfront"]
    code: str = Field(min_length=1, max_length=32)
    link_id: UUID | None = None
    referrer: str | None = None
    user_agent: str | None = None

    @field_validator("occurred_at")
    @classmethod
    def _require_aware_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        try:
            return value.astimezone(UTC)
        except OverflowError as exc:  # e.g. 9999-12-31T23:30-05:00 has no UTC equivalent
            raise ValueError("occurred_at out of range") from exc

    @field_validator("referrer")
    @classmethod
    def _truncate_referrer(cls, value: str | None) -> str | None:
        return _truncate(value, REFERRER_MAX)

    @field_validator("user_agent")
    @classmethod
    def _truncate_user_agent(cls, value: str | None) -> str | None:
        return _truncate(value, USER_AGENT_MAX)
