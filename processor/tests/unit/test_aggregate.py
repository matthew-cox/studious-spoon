from datetime import UTC, date, datetime, timedelta, timezone
from uuid import UUID

from shortener_processor.aggregate import ResolvedClick, aggregate, hour_bucket

A = UUID("00000000-0000-0000-0000-00000000000a")
B = UUID("00000000-0000-0000-0000-00000000000b")


def click(link, ts, host="(direct)"):
    return ResolvedClick(link_id=link, occurred_at=ts, referrer_host=host)


def utc(*args):
    return datetime(*args, tzinfo=UTC)


def test_hour_bucket_truncates_in_utc():
    eastern = timezone(timedelta(hours=-4))
    assert hour_bucket(datetime(2026, 9, 30, 23, 59, 59, tzinfo=eastern)) == utc(2026, 10, 1, 3)


def test_counts_group_by_link_and_hour():
    deltas = aggregate(
        [
            click(A, utc(2026, 10, 1, 12, 5)),
            click(A, utc(2026, 10, 1, 12, 55)),
            click(A, utc(2026, 10, 1, 13, 0)),
            click(B, utc(2026, 10, 1, 12, 30)),
        ]
    )
    assert deltas.hourly == {
        (A, utc(2026, 10, 1, 12)): 2,
        (A, utc(2026, 10, 1, 13)): 1,
        (B, utc(2026, 10, 1, 12)): 1,
    }


def test_referrers_group_by_link_utc_date_and_host():
    eastern = timezone(timedelta(hours=-4))
    deltas = aggregate(
        [
            click(
                A, datetime(2026, 9, 30, 21, 0, tzinfo=eastern), "news.example"
            ),  # 01:00 UTC Oct 1
            click(A, utc(2026, 10, 1, 9), "news.example"),
            click(A, utc(2026, 9, 30, 9), "news.example"),
            click(A, utc(2026, 10, 1, 9)),
        ]
    )
    assert deltas.referrers == {
        (A, date(2026, 10, 1), "news.example"): 2,
        (A, date(2026, 9, 30), "news.example"): 1,
        (A, date(2026, 10, 1), "(direct)"): 1,
    }


def test_empty_input_is_empty():
    deltas = aggregate([])
    assert deltas.is_empty
    assert deltas.link_ids == frozenset()


def test_without_drops_links_from_both_tables():
    deltas = aggregate(
        [click(A, utc(2026, 10, 1, 12)), click(B, utc(2026, 10, 1, 12), "x.example")]
    )
    assert deltas.link_ids == {A, B}
    kept = deltas.without({B})
    assert kept.link_ids == {A}
    assert all(key[0] == A for key in kept.hourly)
    assert all(key[0] == A for key in kept.referrers)
    assert not kept.is_empty
