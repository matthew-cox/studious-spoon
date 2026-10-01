"""Public redirects (spec §6, D8). Registered last so real routes are never shadowed."""

import time
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from shortener_api.codes import CODE_PATTERN
from shortener_api.deps import AppDeps, get_deps
from shortener_api.links_repo import LinkRepository
from shortener_events import ClickEvent

router = APIRouter()
NO_STORE = {"Cache-Control": "private, no-store"}

_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{title}</title>
<style>body{{font-family:system-ui,sans-serif;max-width:32rem;
margin:4rem auto;padding:0 1rem}}</style>
</head><body><h1>{title}</h1><p>{message}</p></body></html>"""
NOT_FOUND_PAGE = _PAGE.format(title="Link not found", message="This short link doesn't exist.")
BLOCKED_PAGE = _PAGE.format(
    title="Link disabled", message="This link has been disabled by an administrator."
)


@router.get("/{code}", include_in_schema=False)
async def follow(
    code: str, request: Request, deps: Annotated[AppDeps, Depends(get_deps)]
) -> Response:
    started = time.perf_counter()
    result = "not_found"
    try:
        link = None
        if CODE_PATTERN.match(code):
            link = await LinkRepository(deps.engine).get_by_code(code)
        if link is None:
            return HTMLResponse(NOT_FOUND_PAGE, status_code=404, headers=NO_STORE)
        if link.blocked_at is not None:
            result = "blocked"
            return HTMLResponse(BLOCKED_PAGE, status_code=410, headers=NO_STORE)
        if not link.is_active:
            result = "disabled"
            return HTMLResponse(NOT_FOUND_PAGE, status_code=404, headers=NO_STORE)
        result = "ok"
        deps.publisher.publish(
            ClickEvent(
                event_id=str(uuid4()),
                occurred_at=deps.clock.now(),
                source="api",
                code=link.code,
                link_id=link.id,
                referrer=request.headers.get("referer"),
                user_agent=request.headers.get("user-agent"),
            )
        )
        return RedirectResponse(link.target_url, status_code=302, headers=NO_STORE)
    finally:
        attributes = {"result": result}
        deps.telemetry.redirects.add(1, attributes)
        deps.telemetry.redirect_duration.record(time.perf_counter() - started, attributes)
