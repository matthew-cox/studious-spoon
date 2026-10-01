from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from shortener_api.auth import current_principal
from shortener_api.policy import Principal

router = APIRouter(prefix="/api/v1", tags=["links"])
CurrentPrincipal = Annotated[Principal, Depends(current_principal)]


class MeOut(BaseModel):
    sub: str
    username: str
    roles: list[str]


@router.get("/me")
async def me(principal: CurrentPrincipal) -> MeOut:
    """Any authenticated user, including one with no roles (the admin UI shows 'no access')."""
    return MeOut(
        sub=principal.sub, username=principal.username, roles=sorted(principal.managed_roles)
    )
