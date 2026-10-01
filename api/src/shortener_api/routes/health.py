from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends
from sqlalchemy.exc import SQLAlchemyError

from shortener_api.deps import AppDeps, get_deps
from shortener_api.errors import ProblemError

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(deps: Annotated[AppDeps, Depends(get_deps)]) -> dict[str, str]:
    """Ready when the database answers. SQS is deliberately not checked (spec §6)."""
    try:
        async with deps.engine.connect() as conn:
            await conn.execute(sa.text("SELECT 1"))
    except (SQLAlchemyError, OSError) as exc:
        raise ProblemError(503, detail="database unavailable") from exc
    return {"status": "ok"}
