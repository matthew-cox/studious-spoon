"""Click events → rollup deltas (spec §5.3 step 3). Pure: no framework imports."""

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from uuid import UUID

HourKey = tuple[UUID, datetime]
ReferrerKey = tuple[UUID, date, str]


@dataclass(frozen=True)
class ResolvedClick:
    link_id: UUID
    occurred_at: datetime
    referrer_host: str


@dataclass(frozen=True)
class RollupDeltas:
    hourly: dict[HourKey, int] = field(default_factory=dict)
    referrers: dict[ReferrerKey, int] = field(default_factory=dict)

    @property
    def link_ids(self) -> frozenset[UUID]:
        return frozenset(key[0] for key in self.hourly) | frozenset(
            key[0] for key in self.referrers
        )

    @property
    def is_empty(self) -> bool:
        return not self.hourly and not self.referrers

    def without(self, link_ids: set[UUID] | frozenset[UUID]) -> "RollupDeltas":
        return RollupDeltas(
            hourly={k: v for k, v in self.hourly.items() if k[0] not in link_ids},
            referrers={k: v for k, v in self.referrers.items() if k[0] not in link_ids},
        )


def hour_bucket(ts: datetime) -> datetime:
    return ts.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def aggregate(clicks: Iterable[ResolvedClick]) -> RollupDeltas:
    hourly: Counter[HourKey] = Counter()
    referrers: Counter[ReferrerKey] = Counter()
    for click in clicks:
        hour = hour_bucket(click.occurred_at)
        hourly[(click.link_id, hour)] += 1
        referrers[(click.link_id, hour.date(), click.referrer_host)] += 1
    return RollupDeltas(hourly=dict(hourly), referrers=dict(referrers))
