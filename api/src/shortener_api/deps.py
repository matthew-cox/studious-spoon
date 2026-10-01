from dataclasses import dataclass
from typing import cast

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncEngine

from shortener_api.clock import Clock
from shortener_api.codes import RandomSource
from shortener_api.settings import ApiSettings


@dataclass(kw_only=True)
class AppDeps:
    """Everything the app needs from the outside world; tests build it with fakes."""

    settings: ApiSettings
    engine: AsyncEngine
    clock: Clock
    rng: RandomSource


def get_deps(request: Request) -> AppDeps:
    return cast(AppDeps, request.app.state.deps)
