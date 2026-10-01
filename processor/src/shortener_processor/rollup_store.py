"""Rollup persistence (spec §5.3 step 4): one transaction per batch, additive upserts."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from shortener_processor.aggregate import RollupDeltas
from shortener_processor.db import link_clicks_hourly, link_referrers_daily, links, pipeline_status


@dataclass(frozen=True)
class CommitResult:
    committed_links: frozenset[UUID]
    skipped_links: frozenset[UUID]


class RollupStore(Protocol):
    async def commit(self, deltas: RollupDeltas, now: datetime) -> CommitResult: ...


async def _upsert(conn: AsyncConnection, table: sa.Table, rows: list[dict[str, object]]) -> None:
    if not rows:
        return
    stmt = pg_insert(table).values(rows)
    keys = [column for column in table.primary_key.columns]
    stmt = stmt.on_conflict_do_update(
        index_elements=keys, set_={"count": table.c["count"] + stmt.excluded["count"]}
    )
    await conn.execute(stmt)


class PostgresRollupStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def commit(self, deltas: RollupDeltas, now: datetime) -> CommitResult:
        wanted = deltas.link_ids
        async with self._engine.begin() as conn:
            existing: frozenset[UUID] = frozenset()
            if wanted:
                found = await conn.execute(sa.select(links.c.id).where(links.c.id.in_(wanted)))
                existing = frozenset(row[0] for row in found.all())
            skipped = wanted - existing
            kept = deltas.without(skipped)
            # Sorted by primary key so concurrent processors lock rows in the same order.
            await _upsert(
                conn,
                link_clicks_hourly,
                [
                    {"link_id": link_id, "bucket_start": hour, "count": count}
                    for (link_id, hour), count in sorted(kept.hourly.items())
                ],
            )
            await _upsert(
                conn,
                link_referrers_daily,
                [
                    {"link_id": link_id, "bucket_date": day, "referrer_host": host, "count": count}
                    for (link_id, day, host), count in sorted(kept.referrers.items())
                ],
            )
            await conn.execute(
                sa.update(pipeline_status)
                .where(pipeline_status.c.id == 1)
                .values(
                    last_committed_at=sa.func.greatest(pipeline_status.c.last_committed_at, now)
                )
            )
        return CommitResult(committed_links=existing, skipped_links=frozenset(skipped))
