"""Stats range arithmetic (spec §6). Pure: no framework imports."""

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Literal

Bucket = Literal["hour", "day"]
STEP: dict[Bucket, timedelta] = {"hour": timedelta(hours=1), "day": timedelta(days=1)}
MAX_BUCKETS: dict[Bucket, int] = {"hour": 24 * 31, "day": 366}
DEFAULT_SPAN = timedelta(days=7)


class InvalidRange(ValueError):
    pass


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def floor(value: datetime, bucket: Bucket) -> datetime:
    value = _utc(value).replace(minute=0, second=0, microsecond=0)
    return value.replace(hour=0) if bucket == "day" else value


def _ceil(value: datetime, bucket: Bucket) -> datetime:
    floored = floor(value, bucket)
    return floored if floored == _utc(value) else floored + STEP[bucket]


def normalize_range(
    start: datetime | None, end: datetime | None, bucket: Bucket, now: datetime
) -> tuple[datetime, datetime]:
    try:
        end_utc = _utc(end) if end is not None else _utc(now)
        start_utc = _utc(start) if start is not None else end_utc - DEFAULT_SPAN
        if start_utc >= end_utc:  # check the raw instants: rounding could widen a bad range
            raise InvalidRange("'from' must be before 'to'")
        lo, hi = floor(start_utc, bucket), _ceil(end_utc, bucket)
    except OverflowError as exc:  # datetime arithmetic or UTC conversion at min/max
        raise InvalidRange("date out of range") from exc
    if (hi - lo) / STEP[bucket] > MAX_BUCKETS[bucket]:
        raise InvalidRange(f"range too large: at most {MAX_BUCKETS[bucket]} {bucket} buckets")
    return lo, hi


def fill_series(
    counts: Mapping[datetime, int], start: datetime, end: datetime, bucket: Bucket
) -> list[tuple[datetime, int]]:
    series: list[tuple[datetime, int]] = []
    cursor = start
    while cursor < end:
        series.append((cursor, counts.get(cursor, 0)))
        cursor += STEP[bucket]
    return series
