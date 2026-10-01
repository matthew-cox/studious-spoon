from datetime import datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from shortener_api.deps import AppDeps, get_deps
from shortener_api.errors import ProblemError
from shortener_api.policy import Action, decide, visible_owner
from shortener_api.routes.links import CurrentPrincipal, Service
from shortener_api.schemas import LinkStatsOut, ReferrerCount, SeriesPoint, SummaryOut, TopLinkOut
from shortener_api.service import enforce
from shortener_api.stats import InvalidRange, fill_series, normalize_range
from shortener_api.stats_repo import StatsRepository

router = APIRouter(prefix="/api/v1", tags=["stats"])
Deps = Annotated[AppDeps, Depends(get_deps)]


@router.get("/links/{link_id}/stats", response_model_by_alias=True)
async def link_stats(
    link_id: UUID,
    principal: CurrentPrincipal,
    service: Service,
    deps: Deps,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
    bucket: Literal["hour", "day"] = "hour",
) -> LinkStatsOut:
    await service.get(principal, link_id, Action.STATS)
    try:
        start, end = normalize_range(from_, to, bucket, deps.clock.now())
    except InvalidRange as exc:
        raise ProblemError(422, "Invalid range", str(exc)) from exc
    repo = StatsRepository(deps.engine)
    series = fill_series(await repo.bucket_counts(link_id, start, end, bucket), start, end, bucket)
    return LinkStatsOut(
        total=sum(count for _, count in series),
        bucket=bucket,
        from_=start,
        to=end,
        series=[SeriesPoint(ts=ts, count=count) for ts, count in series],
        top_referrers=[
            ReferrerCount(referrer_host=host, count=count)
            for host, count in await repo.top_referrers(link_id, start, end)
        ],
        data_as_of=await repo.data_as_of(),
    )


@router.get("/stats/summary")
async def summary(principal: CurrentPrincipal, deps: Deps) -> SummaryOut:
    enforce(decide(principal, Action.LIST))
    repo = StatsRepository(deps.engine)
    row = await repo.summary(visible_owner(principal), deps.clock.now() - timedelta(days=7))
    return SummaryOut(
        link_count=row.link_count,
        clicks_7d=row.clicks,
        top_links=[TopLinkOut(id=t.id, code=t.code, clicks_7d=t.clicks) for t in row.top_links],
        data_as_of=await repo.data_as_of(),
    )
