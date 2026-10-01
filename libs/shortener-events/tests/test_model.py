from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from shortener_events import REFERRER_MAX, USER_AGENT_MAX


def test_type_and_version_default(make_event):
    event = make_event()
    assert event.type == "link.clicked"
    assert event.version == 1


def test_naive_occurred_at_is_rejected(make_event):
    with pytest.raises(ValidationError, match="timezone-aware"):
        make_event(occurred_at=datetime(2026, 10, 1, 12, 0))


def test_non_utc_occurred_at_is_normalized_to_utc(make_event):
    eastern = timezone(timedelta(hours=-4))
    event = make_event(occurred_at=datetime(2026, 10, 1, 8, 0, tzinfo=eastern))
    assert event.occurred_at == datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    assert event.occurred_at.utcoffset() == timedelta(0)


def test_oversized_referrer_is_truncated_not_rejected(make_event):
    event = make_event(referrer="https://example.com/" + "a" * 5000)
    assert event.referrer is not None
    assert len(event.referrer) == REFERRER_MAX


def test_oversized_user_agent_is_truncated_not_rejected(make_event):
    event = make_event(user_agent="x" * 5000)
    assert event.user_agent is not None
    assert len(event.user_agent) == USER_AGENT_MAX


def test_empty_referrer_and_user_agent_become_none(make_event):
    event = make_event(referrer="", user_agent="")
    assert event.referrer is None
    assert event.user_agent is None


def test_link_id_is_optional(make_event):
    assert make_event(link_id=None).link_id is None


def test_unknown_source_is_rejected(make_event):
    with pytest.raises(ValidationError):
        make_event(source="lambda")


@pytest.mark.parametrize("code", ["", "x" * 33])
def test_code_length_is_bounded(make_event, code):
    with pytest.raises(ValidationError):
        make_event(code=code)


def test_unknown_fields_are_rejected(make_event):
    with pytest.raises(ValidationError):
        make_event(country="CA")


def test_event_is_immutable(make_event):
    event = make_event()
    with pytest.raises(ValidationError):
        event.code = "other"


@pytest.mark.parametrize(
    "occurred_at",
    [
        datetime(9999, 12, 31, 23, 30, tzinfo=timezone(timedelta(hours=-5))),
        datetime(1, 1, 1, 0, 30, tzinfo=timezone(timedelta(hours=5))),
    ],
)
def test_out_of_range_occurred_at_is_a_validation_error(make_event, occurred_at):
    with pytest.raises(ValidationError, match="out of range"):
        make_event(occurred_at=occurred_at)
