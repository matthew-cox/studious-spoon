from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from shortener_admin.deps import AdminDeps, get_deps

router = APIRouter()


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(deps: Annotated[AdminDeps, Depends(get_deps)]) -> JSONResponse:
    ok = await deps.sessions.ping()
    return JSONResponse({"status": "ok" if ok else "unavailable"}, status_code=200 if ok else 503)
