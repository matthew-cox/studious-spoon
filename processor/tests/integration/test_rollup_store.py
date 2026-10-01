from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from shortener_processor.aggregate import RollupDeltas
from shortener_processor.rollup_store import PostgresRollupStore

pytestmark = pytest.mark.integration

H12 = datetime(2026, 10, 1, 12, tzinfo=UTC)
NOW = datetime(2026, 10, 1, 12, 30, tzinfo=UTC)


def rows(migrated, sql):
    with migrated.connect("migrator") as conn:
        return conn.execute(sql).fetchall()


async def test_commit_upserts_and_accumulates(processor_engine, insert_link, migrated):
    link = insert_link()
    store = PostgresRollupStore(processor_engine)
    deltas = RollupDeltas(
        hourly={(link, H12): 2}, referrers={(link, date(2026, 10, 1), "news.example"): 2}
    )
    first = await store.commit(deltas, NOW)
    await store.commit(deltas, NOW)
    assert first.committed_links == {link}
    assert first.skipped_links == frozenset()
    assert rows(migrated, "SELECT count FROM analytics.link_clicks_hourly") == [(4,)]
    assert rows(migrated, "SELECT referrer_host, count FROM analytics.link_referrers_daily") == [
        ("news.example", 4)
    ]


async def test_commit_records_pipeline_status(processor_engine, insert_link, migrated):
    link = insert_link()
    await PostgresRollupStore(processor_engine).commit(RollupDeltas(hourly={(link, H12): 1}), NOW)
    assert rows(migrated, "SELECT last_committed_at FROM analytics.pipeline_status") == [(NOW,)]


async def test_commit_skips_links_that_no_longer_exist(processor_engine, insert_link, migrated):
    kept, gone = insert_link(), uuid4()
    result = await PostgresRollupStore(processor_engine).commit(
        RollupDeltas(hourly={(kept, H12): 1, (gone, H12): 5}), NOW
    )
    assert result.committed_links == {kept}
    assert result.skipped_links == {gone}
    assert rows(migrated, "SELECT link_id, count FROM analytics.link_clicks_hourly") == [(kept, 1)]


async def test_commit_with_only_unknown_links_still_succeeds(processor_engine, migrated):
    gone = uuid4()
    result = await PostgresRollupStore(processor_engine).commit(
        RollupDeltas(hourly={(gone, H12): 1}), NOW
    )
    assert result.skipped_links == {gone}
    assert rows(migrated, "SELECT count(*) FROM analytics.link_clicks_hourly") == [(0,)]
