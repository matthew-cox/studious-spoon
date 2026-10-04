import math
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Annotated, Any
from urllib.parse import urlencode
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from shortener_admin.api_client import ApiError
from shortener_admin.auth import require_access, verify_csrf_with_access
from shortener_admin.deps import AdminDeps, get_deps
from shortener_admin.sessions import Session
from shortener_admin.views import api_message, chart_data, is_htmx, render

router = APIRouter()
Deps = Annotated[AdminDeps, Depends(get_deps)]
Access = Annotated[Session, Depends(require_access)]
STATUSES = ("active", "disabled", "blocked")


def _page_number(raw: str) -> int:
    if not (raw.isascii() and raw.isdigit()):
        return 1
    try:
        number = int(raw)
    except ValueError:  # digit string beyond Python's int-conversion limit
        return 1
    return number if number >= 1 else 1


@router.get("/links")
async def list_links(
    request: Request, deps: Deps, session: Access,
    q: str = "", status: str = "", owner: str = "", page: str = "1",
) -> HTMLResponse:  # fmt: skip
    query = q.strip()
    status_filter = status if status in STATUSES else ""
    owner_filter = owner.strip()
    page_no = _page_number(page)
    result: dict[str, Any] = await deps.api.list_links(
        session.tokens.access_token,
        q=query or None,
        status=status_filter or None,
        owner=owner_filter or None,
        page=page_no,
    )
    pages = max(1, math.ceil(result["total"] / result["page_size"]))
    filters = {k: v for k, v in (("q", query), ("status", status_filter)) if v}

    def page_url(n: int) -> str:
        owner_param = {"owner": owner_filter} if owner_filter else {}
        return f"/links?{urlencode(filters | owner_param | {'page': n})}"

    clear_owner_url = f"/links?{urlencode(filters)}" if filters else "/links"
    template = "partials/links_table.html" if is_htmx(request) else "links.html"
    return render(request, template, result=result, q=query, status=status_filter,
                  owner=owner_filter, clear_owner_url=clear_owner_url, page=page_no,
                  pages=pages, page_url=page_url, statuses=STATUSES,
                  deleted=request.query_params.get("deleted") == "1")  # fmt: skip


Mutate = Annotated[Session, Depends(verify_csrf_with_access)]
BUCKETS = ("hour", "day")
PASS_THROUGH = {401, 503}  # handled app-wide: session expiry / API down


def _uuid_or_none(raw: str) -> str | None:
    try:
        return str(UUID(raw))
    except ValueError:
        return None


def _can_edit(session: Session, link: dict[str, Any]) -> bool:
    if session.is_admin:
        return True
    return (
        session.can_create
        and link["owner_username"] == session.username
        and link["status"] != "blocked"
    )


def _not_found(request: Request) -> HTMLResponse:
    return render(request, "error.html", status_code=404, title="Link not found", message="")


async def _stats(
    deps: AdminDeps, session: Session, link_id: str, bucket: str
) -> dict[str, Any] | None:
    start = deps.clock() - timedelta(days=30) if bucket == "day" else None
    try:
        stats: dict[str, Any] = await deps.api.link_stats(
            session.tokens.access_token, link_id, bucket=bucket, start=start
        )
    except ApiError as exc:
        if exc.status in PASS_THROUGH:
            raise
        return None
    return stats


async def _history(deps: AdminDeps, session: Session, link_id: str) -> list[Any] | None:
    """Moderation history for moderators (the API refuses everyone else); None if unavailable."""
    try:
        events: list[Any] = await deps.api.link_events(session.tokens.access_token, link_id)
    except ApiError as exc:
        if exc.status in PASS_THROUGH:
            raise
        return None
    return events


async def _owner_summary(deps: AdminDeps, session: Session, owner: str) -> dict[str, int] | None:
    """How many links the owner has, and how many are blocked; None if unavailable."""
    token = session.tokens.access_token
    try:
        everything = await deps.api.list_links(token, owner=owner, page_size=1)
        blocked = await deps.api.list_links(token, owner=owner, status="blocked", page_size=1)
    except ApiError as exc:
        if exc.status in PASS_THROUGH:
            raise
        return None
    return {"total": everything["total"], "blocked": blocked["total"]}


async def _detail(
    request: Request, deps: AdminDeps, session: Session, link_id: str, *,
    notice: str | None = None, error: ApiError | None = None, bucket: str = "hour",
) -> HTMLResponse:  # fmt: skip
    link = await deps.api.get_link(session.tokens.access_token, link_id)
    stats = await _stats(deps, session, link_id, bucket)
    history = await _history(deps, session, link_id) if session.can_moderate else None
    owner_summary = await _owner_summary(deps, session, link["owner_username"])
    return render(
        request,
        "link_detail.html",
        status_code=error.status if error else 200,
        link=link,
        notice=notice,
        error=api_message(error) if error else None,
        can_edit=_can_edit(session, link),
        can_block=session.can_moderate,
        history=history,
        owner_summary=owner_summary,
        stats=stats,
        chart=chart_data(stats, bucket) if stats else None,
        bucket=bucket,
    )


async def _act(
    request: Request, deps: AdminDeps, session: Session, link_id: str,
    call: Callable[[], Awaitable[Any]], success_url: str,
) -> Response:  # fmt: skip
    try:
        await call()
    except ApiError as exc:
        if exc.status in PASS_THROUGH:
            raise
        return await _detail(request, deps, session, link_id, error=exc)
    return RedirectResponse(success_url, status_code=303)


@router.get("/links/new")
async def new_link(request: Request, session: Access) -> HTMLResponse:
    if not session.can_create:
        return render(request, "error.html", status_code=403, title="Not allowed",
                      message="Your role can't create links.")  # fmt: skip
    return render(request, "link_new.html", target_url="", error=None)


@router.post("/links")
async def create_link(
    request: Request, deps: Deps, session: Mutate, target_url: Annotated[str, Form()] = ""
) -> Response:
    try:
        link = await deps.api.create_link(session.tokens.access_token, target_url)
    except ApiError as exc:
        if exc.status in PASS_THROUGH:
            raise
        return render(request, "link_new.html", status_code=exc.status, target_url=target_url,
                      error=api_message(exc))  # fmt: skip
    return RedirectResponse(f"/links/{link['id']}?created=1", status_code=303)


@router.get("/links/{link_id}")
async def link_detail(request: Request, link_id: str, deps: Deps, session: Access) -> HTMLResponse:
    lid = _uuid_or_none(link_id)
    if lid is None:
        return _not_found(request)
    params = request.query_params
    notice = ("Short link created." if params.get("created") == "1"
              else "Link updated." if params.get("updated") == "1" else None)  # fmt: skip
    requested = params.get("bucket")
    bucket = requested if requested in BUCKETS else "hour"
    return await _detail(request, deps, session, lid, notice=notice, bucket=bucket)


@router.post("/links/{link_id}/edit")
async def edit_link(
    request: Request,
    link_id: str,
    deps: Deps,
    session: Mutate,
    target_url: Annotated[str, Form()] = "",
) -> Response:
    lid = _uuid_or_none(link_id)
    if lid is None:
        return _not_found(request)
    token = session.tokens.access_token
    return await _act(request, deps, session, lid,
                      lambda: deps.api.update_link(token, lid, target_url=target_url),
                      f"/links/{lid}?updated=1")  # fmt: skip


@router.post("/links/{link_id}/toggle")
async def toggle_link(
    request: Request,
    link_id: str,
    deps: Deps,
    session: Mutate,
    is_active: Annotated[str, Form()] = "",
) -> Response:
    lid = _uuid_or_none(link_id)
    if lid is None or is_active not in ("true", "false"):
        return _not_found(request)
    token = session.tokens.access_token
    return await _act(request, deps, session, lid,
                      lambda: deps.api.update_link(token, lid, is_active=is_active == "true"),
                      f"/links/{lid}?updated=1")  # fmt: skip


@router.post("/links/{link_id}/delete")
async def delete_link(request: Request, link_id: str, deps: Deps, session: Mutate) -> Response:
    lid = _uuid_or_none(link_id)
    if lid is None:
        return _not_found(request)
    token = session.tokens.access_token
    return await _act(request, deps, session, lid, lambda: deps.api.delete_link(token, lid),
                      "/links?deleted=1")  # fmt: skip


@router.get("/links/{link_id}/stats")
async def link_stats(
    request: Request, link_id: str, deps: Deps, session: Access, bucket: str = "hour"
) -> Response:
    lid = _uuid_or_none(link_id)
    if lid is None:
        return _not_found(request)
    bucket = bucket if bucket in BUCKETS else "hour"
    if not is_htmx(request):
        return RedirectResponse(f"/links/{lid}?bucket={bucket}", status_code=303)
    stats = await _stats(deps, session, lid, bucket)
    return render(request, "partials/link_stats.html", link={"id": lid}, stats=stats, bucket=bucket,
                  chart=chart_data(stats, bucket) if stats else None)  # fmt: skip


@router.post("/links/{link_id}/block")
async def block_link(
    request: Request, link_id: str, deps: Deps, session: Mutate, reason: Annotated[str, Form()] = ""
) -> Response:
    lid = _uuid_or_none(link_id)
    if lid is None:
        return _not_found(request)
    token = session.tokens.access_token
    return await _act(request, deps, session, lid, lambda: deps.api.block_link(token, lid, reason),
                      f"/links/{lid}?updated=1")  # fmt: skip


@router.post("/links/{link_id}/unblock")
async def unblock_link(
    request: Request, link_id: str, deps: Deps, session: Mutate, reason: Annotated[str, Form()] = ""
) -> Response:
    lid = _uuid_or_none(link_id)
    if lid is None:
        return _not_found(request)
    token = session.tokens.access_token
    return await _act(
        request,
        deps,
        session,
        lid,
        lambda: deps.api.unblock_link(token, lid, reason),
        f"/links/{lid}?updated=1",
    )
