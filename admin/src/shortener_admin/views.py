from datetime import UTC, datetime
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse

from shortener_admin.api_client import ApiError
from shortener_admin.deps import get_deps


def is_htmx(request: Request) -> bool:
    """True for HTMX fragment requests. Boosted navigations want whole pages."""
    return (
        request.headers.get("hx-request") == "true" and request.headers.get("hx-boosted") != "true"
    )


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


def chart_data(stats: dict[str, Any], bucket: str) -> dict[str, list[Any]]:
    fmt = "%m-%d %H:00" if bucket == "hour" else "%Y-%m-%d"
    labels: list[Any] = []
    counts: list[Any] = []
    for point in stats.get("series", []):
        moment = datetime.fromisoformat(str(point["ts"]).replace("Z", "+00:00")).astimezone(UTC)
        labels.append(moment.strftime(fmt))
        counts.append(int(point["count"]))
    return {"labels": labels, "counts": counts}
