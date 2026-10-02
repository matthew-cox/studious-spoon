from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from shortener_api.db.tables import link_events, links
from shortener_api.policy import LinkFacts, Principal

LinkStatus = Literal["active", "disabled", "blocked"]
LinkEventAction = Literal["block", "unblock", "delete"]


@dataclass(frozen=True)
class Link:
    id: UUID
    code: str
    target_url: str
    owner_sub: str
    owner_username: str
    is_active: bool
    blocked_at: datetime | None
    blocked_by: str | None
    blocked_reason: str | None
    created_at: datetime
    updated_at: datetime

    @property
    def status(self) -> LinkStatus:
        if self.blocked_at is not None:
            return "blocked"
        return "active" if self.is_active else "disabled"

    def facts(self) -> LinkFacts:
        return LinkFacts(owner_sub=self.owner_sub, blocked=self.blocked_at is not None)


@dataclass(frozen=True)
class LinkEvent:
    """One moderation action, kept after the link itself is deleted."""

    id: int
    link_id: UUID
    link_code: str
    action: LinkEventAction
    actor_sub: str
    actor_username: str
    reason: str | None
    occurred_at: datetime


@dataclass(frozen=True)
class LinkQuery:
    q: str | None = None
    status: LinkStatus | None = None
    owner_sub: str | None = None  # visibility scope (policy.visible_owner)
    owner_username: str | None = None  # caller's filter; exact match
    page: int = 1
    page_size: int = 20


@dataclass(frozen=True)
class Page[T]:
    items: list[T]
    total: int
    page: int
    page_size: int


class CodeTakenError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(f"short code already in use: {code}")
        self.code = code


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _to_link(row: sa.Row[Any]) -> Link:
    return Link(**row._mapping)


class LinkRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def insert(
        self, *, code: str, target_url: str, owner_sub: str, owner_username: str, now: datetime
    ) -> Link:
        stmt = (
            sa.insert(links)
            .values(
                code=code,
                target_url=target_url,
                owner_sub=owner_sub,
                owner_username=owner_username,
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            .returning(*links.c)
        )
        try:
            async with self._engine.begin() as conn:
                row = (await conn.execute(stmt)).one()
        except IntegrityError as exc:
            if "uq_links_code" in str(exc.orig):
                raise CodeTakenError(code) from exc
            raise
        return _to_link(row)

    async def _one(self, stmt: sa.Select[Any]) -> Link | None:
        async with self._engine.connect() as conn:
            row = (await conn.execute(stmt)).one_or_none()
        return _to_link(row) if row is not None else None

    async def get(self, link_id: UUID) -> Link | None:
        return await self._one(sa.select(links).where(links.c.id == link_id))

    async def get_by_code(self, code: str) -> Link | None:
        return await self._one(sa.select(links).where(links.c.code == code))

    def _conditions(self, query: LinkQuery) -> list[sa.ColumnElement[bool]]:
        conditions: list[sa.ColumnElement[bool]] = []
        if query.q:
            pattern = f"%{_escape_like(query.q)}%"
            conditions.append(
                sa.or_(
                    links.c.code.ilike(pattern, escape="\\"),
                    links.c.target_url.ilike(pattern, escape="\\"),
                )
            )
        if query.status == "blocked":
            conditions.append(links.c.blocked_at.is_not(None))
        elif query.status in ("active", "disabled"):
            conditions.append(links.c.blocked_at.is_(None))
            conditions.append(links.c.is_active.is_(query.status == "active"))
        if query.owner_sub is not None:
            conditions.append(links.c.owner_sub == query.owner_sub)
        if query.owner_username is not None:
            conditions.append(links.c.owner_username == query.owner_username)
        return conditions

    async def search(self, query: LinkQuery) -> Page[Link]:
        where = sa.and_(sa.true(), *self._conditions(query))
        count_stmt = sa.select(sa.func.count()).select_from(links).where(where)
        items_stmt = (
            sa.select(links)
            .where(where)
            .order_by(links.c.created_at.desc(), links.c.id)
            .limit(query.page_size)
            .offset((query.page - 1) * query.page_size)
        )
        async with self._engine.connect() as conn:
            total = (await conn.execute(count_stmt)).scalar_one()
            rows = (await conn.execute(items_stmt)).all()
        return Page([_to_link(r) for r in rows], int(total), query.page, query.page_size)

    async def _returning_one(self, stmt: Any) -> Link | None:
        async with self._engine.begin() as conn:
            row = (await conn.execute(stmt)).one_or_none()
        return _to_link(row) if row is not None else None

    async def update(
        self,
        link_id: UUID,
        *,
        now: datetime,
        target_url: str | None = None,
        is_active: bool | None = None,
        require_unblocked: bool = False,
    ) -> Link | None:
        values: dict[str, Any] = {"updated_at": now}
        if target_url is not None:
            values["target_url"] = target_url
        if is_active is not None:
            values["is_active"] = is_active
        stmt = sa.update(links).where(links.c.id == link_id).values(**values)
        if require_unblocked:
            stmt = stmt.where(links.c.blocked_at.is_(None))
        return await self._returning_one(stmt.returning(*links.c))

    async def delete(
        self,
        link_id: UUID,
        *,
        actor: Principal,
        now: datetime,
        require_unblocked: bool = False,
    ) -> bool:
        stmt = sa.delete(links).where(links.c.id == link_id)
        if require_unblocked:
            stmt = stmt.where(links.c.blocked_at.is_(None))
        async with self._engine.begin() as conn:
            row = (await conn.execute(stmt.returning(links.c.id, links.c.code))).one_or_none()
            if row is not None:
                await _record(conn, row.id, row.code, "delete", actor, now)
        return row is not None

    async def block(
        self, link_id: UUID, *, actor: Principal, reason: str, now: datetime
    ) -> Link | None:
        stmt = (
            sa.update(links)
            .where(links.c.id == link_id, links.c.blocked_at.is_(None))
            .values(blocked_at=now, blocked_by=actor.sub, blocked_reason=reason, updated_at=now)
            .returning(*links.c)
        )
        return await self._moderate(stmt, "block", actor, now, reason)

    async def unblock(self, link_id: UUID, *, actor: Principal, now: datetime) -> Link | None:
        stmt = (
            sa.update(links)
            .where(links.c.id == link_id, links.c.blocked_at.is_not(None))
            .values(blocked_at=None, blocked_by=None, blocked_reason=None, updated_at=now)
            .returning(*links.c)
        )
        return await self._moderate(stmt, "unblock", actor, now)

    async def _moderate(
        self,
        stmt: Any,
        action: LinkEventAction,
        actor: Principal,
        now: datetime,
        reason: str | None = None,
    ) -> Link | None:
        """Apply a moderation write and record it in the same transaction, or neither."""
        async with self._engine.begin() as conn:
            row = (await conn.execute(stmt)).one_or_none()
            if row is None:
                return None
            link = _to_link(row)
            await _record(conn, link.id, link.code, action, actor, now, reason)
        return link

    async def events(self, link_id: UUID) -> list[LinkEvent]:
        stmt = (
            sa.select(link_events)
            .where(link_events.c.link_id == link_id)
            .order_by(link_events.c.occurred_at, link_events.c.id)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(stmt)).all()
        return [LinkEvent(**row._mapping) for row in rows]


async def _record(
    conn: AsyncConnection,
    link_id: UUID,
    link_code: str,
    action: LinkEventAction,
    actor: Principal,
    now: datetime,
    reason: str | None = None,
) -> None:
    await conn.execute(
        sa.insert(link_events).values(
            link_id=link_id,
            link_code=link_code,
            action=action,
            actor_sub=actor.sub,
            actor_username=actor.username,
            reason=reason,
            occurred_at=now,
        )
    )
