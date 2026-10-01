import logging
from dataclasses import replace
from typing import NoReturn
from uuid import UUID

from shortener_api.clock import Clock
from shortener_api.codes import RandomSource, generate_code
from shortener_api.errors import ProblemError
from shortener_api.links_repo import CodeTakenError, Link, LinkQuery, LinkRepository, Page
from shortener_api.policy import Action, Decision, Principal, decide, visible_owner
from shortener_api.telemetry import ApiTelemetry
from shortener_api.urls import InvalidTargetUrl, validate_target_url

logger = logging.getLogger(__name__)
MAX_CODE_ATTEMPTS = 5


def _not_found() -> ProblemError:
    return ProblemError(404, "Link not found")


def _blocked(link: Link) -> ProblemError:
    reason = link.blocked_reason or ""
    return ProblemError(
        409,
        "Link is blocked",
        f"Blocked by an administrator: {reason}",
        extra={"blocked_reason": reason},
    )


def enforce(decision: Decision, link: Link | None = None) -> None:
    if decision is Decision.ALLOW:
        return
    if decision is Decision.NOT_FOUND:
        raise _not_found()
    if decision is Decision.FORBIDDEN:
        raise ProblemError(403, "Forbidden", "Your role does not allow this action")
    if link is None:
        raise ProblemError(500, "Internal error", "A blocked decision needs a link")
    raise _blocked(link)


class LinkService:
    def __init__(
        self,
        repo: LinkRepository,
        *,
        clock: Clock,
        rng: RandomSource,
        public_base_url: str,
        telemetry: ApiTelemetry,
    ) -> None:
        self._repo = repo
        self._clock = clock
        self._rng = rng
        self._base_url = public_base_url
        self._telemetry = telemetry

    def _validated(self, target_url: str) -> str:
        try:
            return validate_target_url(target_url, self._base_url)
        except InvalidTargetUrl as exc:
            raise ProblemError(422, "Invalid target URL", str(exc)) from exc

    async def _visible(self, principal: Principal, link_id: UUID, action: Action) -> Link:
        link = await self._repo.get(link_id)
        if link is None:
            raise _not_found()
        enforce(decide(principal, action, link.facts()), link)
        return link

    async def _raise_current_state(self, link_id: UUID) -> NoReturn:
        """A conditional write matched nothing: the link vanished or was blocked meanwhile."""
        current = await self._repo.get(link_id)
        if current is None:
            raise _not_found()
        raise _blocked(current)

    async def create(self, principal: Principal, target_url: str) -> Link:
        enforce(decide(principal, Action.CREATE))
        url = self._validated(target_url)
        for _ in range(MAX_CODE_ATTEMPTS):
            try:
                link = await self._repo.insert(
                    code=generate_code(self._rng),
                    target_url=url,
                    owner_sub=principal.sub,
                    owner_username=principal.username,
                    now=self._clock.now(),
                )
            except CodeTakenError:
                continue
            self._telemetry.links_created.add(1)
            return link
        logger.error("short code allocation failed after %d attempts", MAX_CODE_ATTEMPTS)
        raise ProblemError(500, "Could not allocate a short code")

    async def list(self, principal: Principal, query: LinkQuery) -> Page[Link]:
        enforce(decide(principal, Action.LIST))
        scope = visible_owner(principal)
        if scope is not None:  # editors only ever see their own links
            query = replace(query, owner_sub=scope)
        return await self._repo.search(query)

    async def get(self, principal: Principal, link_id: UUID, action: Action = Action.READ) -> Link:
        return await self._visible(principal, link_id, action)

    async def update(
        self, principal: Principal, link_id: UUID, *, target_url: str | None, is_active: bool | None
    ) -> Link:
        await self._visible(principal, link_id, Action.UPDATE)
        url = self._validated(target_url) if target_url is not None else None
        updated = await self._repo.update(
            link_id,
            now=self._clock.now(),
            target_url=url,
            is_active=is_active,
            require_unblocked=not principal.is_admin,
        )
        if updated is None:
            await self._raise_current_state(link_id)
        return updated

    async def delete(self, principal: Principal, link_id: UUID) -> None:
        await self._visible(principal, link_id, Action.DELETE)
        if not await self._repo.delete(link_id, require_unblocked=not principal.is_admin):
            await self._raise_current_state(link_id)
