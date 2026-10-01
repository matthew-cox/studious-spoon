from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from shortener_admin.auth import require_access
from shortener_admin.deps import AdminDeps, get_deps
from shortener_admin.sessions import Session
from shortener_admin.views import render

router = APIRouter()


@router.get("/")
async def dashboard(
    request: Request,
    deps: Annotated[AdminDeps, Depends(get_deps)],
    session: Annotated[Session, Depends(require_access)],
) -> HTMLResponse:
    summary = await deps.api.summary(session.tokens.access_token)
    return render(request, "dashboard.html", summary=summary)
