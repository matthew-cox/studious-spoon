from datetime import UTC, datetime, timedelta, timezone

import pytest

from shortener_api.stats import InvalidRange, fill_series, normalize_range

NOW = datetime(2026, 10, 1, 12, 30, tzinfo=UTC)


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=UTC)


def test_default_range_is_last_seven_days_in_hours():
    start, end = normalize_range(None, None, "hour", NOW)
    assert start == utc(2026, 9, 24, 12)
    assert end == utc(2026, 10, 1, 13)  # current partial hour included


def test_day_buckets_floor_and_ceil_to_midnight():
    start, end = normalize_range(utc(2026, 9, 29, 5), utc(2026, 10, 1, 12), "day", NOW)
    assert (start, end) == (utc(2026, 9, 29), utc(2026, 10, 2))


def test_exact_boundaries_are_kept():
    assert normalize_range(utc(2026, 10, 1, 10), utc(2026, 10, 1, 12), "hour", NOW) == (
        utc(2026, 10, 1, 10),
        utc(2026, 10, 1, 12),
    )


def test_naive_inputs_are_treated_as_utc_and_offsets_converted():
    eastern_9am = datetime(2026, 10, 1, 9, 0, tzinfo=timezone(timedelta(hours=-4)))  # 13:00 UTC
    start, end = normalize_range(datetime(2026, 10, 1, 9, 15), eastern_9am, "hour", NOW)
    assert start == utc(2026, 10, 1, 9)
    assert end == utc(2026, 10, 1, 13)


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (utc(2026, 10, 1, 12), utc(2026, 10, 1, 12)),
        (utc(2026, 10, 2), utc(2026, 10, 1)),
        (utc(2026, 10, 1, 12, 30), utc(2026, 10, 1, 12, 10)),  # reversed within one hour
        (utc(2026, 10, 1, 12, 30), utc(2026, 10, 1, 12, 30)),  # equal, off a boundary
    ],
)
def test_empty_or_reversed_range_is_invalid(start, end):
    with pytest.raises(InvalidRange, match="before"):
        normalize_range(start, end, "hour", NOW)


def test_too_many_hour_buckets_is_invalid():
    with pytest.raises(InvalidRange, match="744"):
        normalize_range(NOW - timedelta(days=32), NOW, "hour", NOW)
    normalize_range(NOW - timedelta(days=30), NOW, "hour", NOW)  # within limit


def test_fill_series_zero_fills_in_order():
    counts = {utc(2026, 10, 1, 11): 2}
    assert fill_series(counts, utc(2026, 10, 1, 10), utc(2026, 10, 1, 13), "hour") == [
        (utc(2026, 10, 1, 10), 0),
        (utc(2026, 10, 1, 11), 2),
        (utc(2026, 10, 1, 12), 0),
    ]


def test_reversed_range_within_one_day_bucket_is_invalid():
    with pytest.raises(InvalidRange, match="before"):
        normalize_range(utc(2026, 10, 1, 15), utc(2026, 10, 1, 9), "day", NOW)


@pytest.mark.parametrize(
    ("start", "end", "bucket"),
    [
        (None, datetime(9999, 12, 31, 23, 30, tzinfo=UTC), "hour"),
        (None, datetime(1, 1, 2, tzinfo=UTC), "hour"),
        (
            datetime(1, 1, 1, tzinfo=timezone(timedelta(hours=1))),
            datetime(1, 1, 5, tzinfo=UTC),
            "hour",
        ),
        (None, datetime(9999, 12, 31, 12, tzinfo=UTC), "day"),
    ],
)
def test_dates_at_the_edge_of_datetime_are_an_invalid_range(start, end, bucket):
    with pytest.raises(InvalidRange, match="out of range"):
        normalize_range(start, end, bucket, NOW)
