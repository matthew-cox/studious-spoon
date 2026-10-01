"""code → link_id for events that arrive without link_id (spec §5.3 step 2)."""

import time
from collections.abc import Callable
from typing import Protocol
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from shortener_processor.db import links


class LinkResolver(Protocol):
    async def resolve(self, codes: set[str]) -> dict[str, UUID]: ...


class PostgresLinkResolver:
    """Caches hits for `ttl_seconds`; misses are never cached (the link may be created soon)."""

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        ttl_seconds: float = 60.0,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._engine = engine
        self._ttl = ttl_seconds
        self._monotonic = monotonic
        self._cache: dict[str, tuple[UUID, float]] = {}

    async def resolve(self, codes: set[str]) -> dict[str, UUID]:
        now = self._monotonic()
        found = {
            code: entry[0]
            for code in codes
            if (entry := self._cache.get(code)) is not None and entry[1] > now
        }
        missing = codes - found.keys()
        if missing:
            async with self._engine.connect() as conn:
                result = await conn.execute(
                    sa.select(links.c.code, links.c.id).where(links.c.code.in_(missing))
                )
                for code, link_id in result.all():
                    found[code] = link_id
                    self._cache[code] = (link_id, now + self._ttl)
        return found
