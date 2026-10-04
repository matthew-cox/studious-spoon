from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from shortener_api.auth import current_principal
from shortener_api.deps import AppDeps, get_deps
from shortener_api.links_repo import LinkQuery, LinkRepository
from shortener_api.policy import Principal
from shortener_api.schemas import (
    LinkCreate,
    LinkEventOut,
    LinkOut,
    LinkPage,
    LinkUpdate,
    MeOut,
    ModerationRequest,
)
from shortener_api.service import LinkService

router = APIRouter(prefix="/api/v1", tags=["links"])
CurrentPrincipal = Annotated[Principal, Depends(current_principal)]


def get_link_service(deps: Annotated[AppDeps, Depends(get_deps)]) -> LinkService:
    return LinkService(
        LinkRepository(deps.engine),
        clock=deps.clock,
        rng=deps.rng,
        public_base_url=str(deps.settings.public_base_url),
        telemetry=deps.telemetry,
    )


Service = Annotated[LinkService, Depends(get_link_service)]
Deps = Annotated[AppDeps, Depends(get_deps)]


def _base(deps: AppDeps) -> str:
    return str(deps.settings.public_base_url)


@router.get("/me")
async def me(principal: CurrentPrincipal) -> MeOut:
    """Any authenticated user, including one with no roles (the admin UI shows 'no access')."""
    return MeOut(
        sub=principal.sub, username=principal.username, roles=sorted(principal.managed_roles)
    )


@router.post("/links", status_code=201)
async def create_link(
    body: LinkCreate, principal: CurrentPrincipal, service: Service, deps: Deps
) -> LinkOut:
    return LinkOut.of(await service.create(principal, body.target_url), _base(deps))


@router.get("/links")
async def list_links(
    principal: CurrentPrincipal,
    service: Service,
    deps: Deps,
    q: Annotated[str | None, Query(max_length=200)] = None,
    status: Literal["active", "disabled", "blocked"] | None = None,
    owner: Annotated[
        str | None,
        Query(max_length=255, description="owner username, exact match (narrows, never widens)"),
    ] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> LinkPage:
    query = LinkQuery(q=q, status=status, owner_username=owner, page=page, page_size=page_size)
    return LinkPage.of(await service.list(principal, query), _base(deps))


@router.get("/links/{link_id}")
async def get_link(
    link_id: UUID, principal: CurrentPrincipal, service: Service, deps: Deps
) -> LinkOut:
    return LinkOut.of(await service.get(principal, link_id), _base(deps))


@router.patch("/links/{link_id}")
async def update_link(
    link_id: UUID, body: LinkUpdate, principal: CurrentPrincipal, service: Service, deps: Deps
) -> LinkOut:
    link = await service.update(
        principal, link_id, target_url=body.target_url, is_active=body.is_active
    )
    return LinkOut.of(link, _base(deps))


@router.delete("/links/{link_id}", status_code=204)
async def delete_link(link_id: UUID, principal: CurrentPrincipal, service: Service) -> Response:
    await service.delete(principal, link_id)
    return Response(status_code=204)


@router.post("/links/{link_id}/block")
async def block_link(
    link_id: UUID,
    body: ModerationRequest,
    principal: CurrentPrincipal,
    service: Service,
    deps: Deps,
) -> LinkOut:
    return LinkOut.of(await service.block(principal, link_id, body.reason), _base(deps))


@router.post("/links/{link_id}/unblock")
async def unblock_link(
    link_id: UUID,
    body: ModerationRequest,
    principal: CurrentPrincipal,
    service: Service,
    deps: Deps,
) -> LinkOut:
    return LinkOut.of(await service.unblock(principal, link_id, body.reason), _base(deps))


@router.get("/links/{link_id}/events")
async def link_events(
    link_id: UUID, principal: CurrentPrincipal, service: Service
) -> list[LinkEventOut]:
    """Moderation history (block, unblock, delete), oldest first. Admin and support only."""
    return [LinkEventOut.of(e) for e in await service.events(principal, link_id)]
