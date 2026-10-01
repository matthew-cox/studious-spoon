"""Server-side sessions (spec §4.5, D7). Tokens never reach the browser."""

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Protocol

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from shortener_admin.db import sessions as table
from shortener_admin.security import new_token

MANAGED_ROLES = frozenset({"admin", "editor", "viewer"})


@dataclass(frozen=True)
class TokenSet:
    access_token: str
    refresh_token: str
    id_token: str
    access_expires_at: datetime
    refresh_expires_at: datetime


@dataclass(frozen=True)
class Session:
    id: str
    sub: str
    username: str
    roles: frozenset[str]
    tokens: TokenSet
    csrf_token: str
    created_at: datetime
    last_seen_at: datetime

    @property
    def managed_roles(self) -> frozenset[str]:
        return self.roles & MANAGED_ROLES

    @property
    def has_access(self) -> bool:
        return bool(self.managed_roles)

    @property
    def is_admin(self) -> bool:
        return "admin" in self.roles

    @property
    def can_create(self) -> bool:
        return bool(self.roles & {"admin", "editor"})

    @property
    def roles_label(self) -> str:
        return ", ".join(sorted(self.managed_roles)) or "no roles"


class SessionStore(Protocol):
    async def create(
        self, *, sub: str, username: str, roles: frozenset[str], tokens: TokenSet, now: datetime
    ) -> Session: ...
    async def get(self, session_id: str, now: datetime) -> Session | None: ...
    async def update_tokens(
        self, session_id: str, *, tokens: TokenSet, roles: frozenset[str], now: datetime
    ) -> Session | None: ...
    async def delete(self, session_id: str) -> None: ...
    async def purge_expired(self, now: datetime) -> int: ...
    async def ping(self) -> bool: ...


def _new_session(
    *, sub: str, username: str, roles: frozenset[str], tokens: TokenSet, now: datetime
) -> Session:
    return Session(
        id=new_token(),
        sub=sub,
        username=username,
        roles=frozenset(roles),
        tokens=tokens,
        csrf_token=new_token(),
        created_at=now,
        last_seen_at=now,
    )


class InMemorySessionStore:
    """Test double with the same contract as PostgresSessionStore."""

    def __init__(self) -> None:
        self.sessions: dict[str, Session] = {}

    async def create(
        self, *, sub: str, username: str, roles: frozenset[str], tokens: TokenSet, now: datetime
    ) -> Session:
        session = _new_session(sub=sub, username=username, roles=roles, tokens=tokens, now=now)
        self.sessions[session.id] = session
        return session

    async def get(self, session_id: str, now: datetime) -> Session | None:
        session = self.sessions.get(session_id)
        if session is None:
            return None
        if session.tokens.refresh_expires_at <= now:
            del self.sessions[session_id]
            return None
        session = replace(session, last_seen_at=now)
        self.sessions[session_id] = session
        return session

    async def update_tokens(
        self, session_id: str, *, tokens: TokenSet, roles: frozenset[str], now: datetime
    ) -> Session | None:
        session = self.sessions.get(session_id)
        if session is None:
            return None
        session = replace(session, tokens=tokens, roles=frozenset(roles), last_seen_at=now)
        self.sessions[session_id] = session
        return session

    async def delete(self, session_id: str) -> None:
        self.sessions.pop(session_id, None)

    async def purge_expired(self, now: datetime) -> int:
        expired = [k for k, s in self.sessions.items() if s.tokens.refresh_expires_at <= now]
        for key in expired:
            del self.sessions[key]
        return len(expired)

    async def ping(self) -> bool:
        return True


def _from_row(row: Any) -> Session:
    m = row._mapping
    return Session(
        id=m["id"],
        sub=m["sub"],
        username=m["username"],
        roles=frozenset(m["roles"]),
        tokens=TokenSet(
            access_token=m["access_token"],
            refresh_token=m["refresh_token"],
            id_token=m["id_token"],
            access_expires_at=m["access_expires_at"],
            refresh_expires_at=m["refresh_expires_at"],
        ),
        csrf_token=m["csrf_token"],
        created_at=m["created_at"],
        last_seen_at=m["last_seen_at"],
    )


def _token_values(tokens: TokenSet) -> dict[str, Any]:
    return {
        "access_token": tokens.access_token,
        "refresh_token": tokens.refresh_token,
        "id_token": tokens.id_token,
        "access_expires_at": tokens.access_expires_at,
        "refresh_expires_at": tokens.refresh_expires_at,
    }


class PostgresSessionStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def create(
        self, *, sub: str, username: str, roles: frozenset[str], tokens: TokenSet, now: datetime
    ) -> Session:
        session = _new_session(sub=sub, username=username, roles=roles, tokens=tokens, now=now)
        async with self._engine.begin() as conn:
            await conn.execute(
                sa.insert(table).values(
                    id=session.id,
                    sub=sub,
                    username=username,
                    roles=sorted(session.roles),
                    csrf_token=session.csrf_token,
                    created_at=now,
                    last_seen_at=now,
                    **_token_values(tokens),
                )
            )
        return session

    async def get(self, session_id: str, now: datetime) -> Session | None:
        async with self._engine.begin() as conn:
            row = (
                await conn.execute(
                    sa.update(table)
                    .where(table.c.id == session_id, table.c.refresh_expires_at > now)
                    .values(last_seen_at=now)
                    .returning(*table.c)
                )
            ).one_or_none()
            if row is None:
                await conn.execute(sa.delete(table).where(table.c.id == session_id))
                return None
        return _from_row(row)

    async def update_tokens(
        self, session_id: str, *, tokens: TokenSet, roles: frozenset[str], now: datetime
    ) -> Session | None:
        async with self._engine.begin() as conn:
            row = (
                await conn.execute(
                    sa.update(table)
                    .where(table.c.id == session_id)
                    .values(roles=sorted(roles), last_seen_at=now, **_token_values(tokens))
                    .returning(*table.c)
                )
            ).one_or_none()
        return _from_row(row) if row is not None else None

    async def delete(self, session_id: str) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(sa.delete(table).where(table.c.id == session_id))

    async def purge_expired(self, now: datetime) -> int:
        async with self._engine.begin() as conn:
            result = await conn.execute(
                sa.delete(table).where(table.c.refresh_expires_at <= now).returning(table.c.id)
            )
            return len(result.all())

    async def ping(self) -> bool:
        try:
            async with self._engine.connect() as conn:
                await conn.execute(sa.text("SELECT 1"))
        except (SQLAlchemyError, OSError):
            return False
        return True
