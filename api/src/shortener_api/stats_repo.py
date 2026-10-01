from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from shortener_api.db.tables import link_clicks_hourly as hourly
from shortener_api.db.tables import link_referrers_daily as referrers
from shortener_api.db.tables import links, pipeline_status
from shortener_api.stats import Bucket


@dataclass(frozen=True)
class TopLink:
    id: UUID
    code: str
    clicks: int


@dataclass(frozen=True)
class SummaryRow:
    link_count: int
    clicks: int
    top_links: list[TopLink]


class StatsRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def bucket_counts(
        self, link_id: UUID, start: datetime, end: datetime, bucket: Bucket
    ) -> dict[datetime, int]:
        ts = (
            hourly.c.bucket_start
            if bucket == "hour"
            else sa.func.date_trunc("day", hourly.c.bucket_start, "UTC")
        ).label("ts")
        stmt = (
            sa.select(ts, sa.func.sum(hourly.c.count))
            .where(
                hourly.c.link_id == link_id,
                hourly.c.bucket_start >= start,
                hourly.c.bucket_start < end,
            )
            .group_by(ts)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return {row[0]: int(row[1]) for row in rows}

    async def top_referrers(
        self, link_id: UUID, start: datetime, end: datetime, limit: int = 10
    ) -> list[tuple[str, int]]:
        total = sa.func.sum(referrers.c.count).label("total")
        last_day = (end - timedelta(microseconds=1)).date()  # `end` is exclusive
        stmt = (
            sa.select(referrers.c.referrer_host, total)
            .where(
                referrers.c.link_id == link_id,
                referrers.c.bucket_date >= start.date(),
                referrers.c.bucket_date <= last_day,
            )
            .group_by(referrers.c.referrer_host)
            .order_by(total.desc(), referrers.c.referrer_host)
            .limit(limit)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [(str(row[0]), int(row[1])) for row in rows]

    async def data_as_of(self) -> datetime | None:
        stmt = sa.select(pipeline_status.c.last_committed_at)
        async with self._engine.connect() as conn:
            value: datetime | None = (await conn.execute(stmt)).scalar_one_or_none()
        return value

    async def summary(self, owner_sub: str | None, since: datetime, limit: int = 5) -> SummaryRow:
        owner_filter = [links.c.owner_sub == owner_sub] if owner_sub is not None else []
        clicks = sa.func.sum(hourly.c.count).label("clicks")
        recent = sa.and_(hourly.c.link_id == links.c.id, hourly.c.bucket_start >= since)
        count_stmt = sa.select(sa.func.count()).select_from(links).where(*owner_filter)
        clicks_stmt = (
            sa.select(sa.func.coalesce(sa.func.sum(hourly.c.count), 0))
            .select_from(hourly.join(links, recent))
            .where(*owner_filter)
        )
        top_stmt = (
            sa.select(links.c.id, links.c.code, clicks)
            .select_from(hourly.join(links, recent))
            .where(*owner_filter)
            .group_by(links.c.id, links.c.code)
            .order_by(clicks.desc(), links.c.code)
            .limit(limit)
        )
        async with self._engine.connect() as conn:
            link_count = int((await conn.execute(count_stmt)).scalar_one())
            total = int((await conn.execute(clicks_stmt)).scalar_one())
            top = [TopLink(r[0], r[1], int(r[2])) for r in (await conn.execute(top_stmt)).all()]
        return SummaryRow(link_count=link_count, clicks=total, top_links=top)
