import math
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from shortener_admin.auth import require_access
from shortener_admin.deps import AdminDeps, get_deps
from shortener_admin.sessions import Session
from shortener_admin.views import is_htmx, render

router = APIRouter()
Deps = Annotated[AdminDeps, Depends(get_deps)]
Access = Annotated[Session, Depends(require_access)]
STATUSES = ("active", "disabled", "blocked")


def _page_number(raw: str) -> int:
    return int(raw) if raw.isdigit() and int(raw) >= 1 else 1


@router.get("/links")
async def list_links(
    request: Request, deps: Deps, session: Access, q: str = "", status: str = "", page: str = "1"
) -> HTMLResponse:
    query = q.strip()
    status_filter = status if status in STATUSES else ""
    page_no = _page_number(page)
    result: dict[str, Any] = await deps.api.list_links(
        session.tokens.access_token, q=query or None, status=status_filter or None, page=page_no
    )
    pages = max(1, math.ceil(result["total"] / result["page_size"]))

    def page_url(n: int) -> str:
        params = {k: v for k, v in (("q", query), ("status", status_filter)) if v} | {"page": n}
        return f"/links?{urlencode(params)}"

    template = "partials/links_table.html" if is_htmx(request) else "links.html"
    return render(request, template, result=result, q=query, status=status_filter, page=page_no,
                  pages=pages, page_url=page_url, statuses=STATUSES,
                  deleted=request.query_params.get("deleted") == "1")  # fmt: skip
