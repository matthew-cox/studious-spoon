from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from shortener_events import ClickEvent

LINK_ID = UUID("6f1c2d3e-4b5a-4c6d-8e7f-001122334455")


@pytest.fixture
def make_event() -> Callable[..., ClickEvent]:
    def _make(**overrides: Any) -> ClickEvent:
        fields: dict[str, Any] = {
            "event_id": "evt-1",
            "occurred_at": datetime(2026, 10, 1, 12, 0, tzinfo=UTC),
            "source": "api",
            "code": "aZ3kQ9x",
            "link_id": LINK_ID,
            "referrer": "https://news.ycombinator.com/item?id=1",
            "user_agent": "Mozilla/5.0",
        }
        fields.update(overrides)
        return ClickEvent(**fields)

    return _make
