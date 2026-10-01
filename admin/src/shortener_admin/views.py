from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse

from shortener_admin.api_client import ApiError
from shortener_admin.deps import get_deps


def is_htmx(request: Request) -> bool:
    return request.headers.get("hx-request") == "true"


def render(
    request: Request, template: str, *, status_code: int = 200, **context: Any
) -> HTMLResponse:
    deps = get_deps(request)
    context = {"session": getattr(request.state, "session", None), **context}
    return deps.templates.TemplateResponse(request, template, context, status_code=status_code)


def api_message(error: ApiError) -> str:
    reason = error.extra.get("blocked_reason")
    if reason:
        return f"{error.title}: {reason}"
    return error.detail or error.title
