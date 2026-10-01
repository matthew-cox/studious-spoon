from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncEngine

from shortener_api.clock import Clock
from shortener_api.codes import RandomSource
from shortener_api.settings import ApiSettings

if TYPE_CHECKING:
    from shortener_api.auth import TokenValidator
    from shortener_api.telemetry import ApiTelemetry


@dataclass(kw_only=True)
class AppDeps:
    """Everything the app needs from the outside world; tests build it with fakes."""

    settings: ApiSettings
    engine: AsyncEngine
    clock: Clock
    rng: RandomSource
    token_validator: TokenValidator
    telemetry: ApiTelemetry


def get_deps(request: Request) -> AppDeps:
    return cast(AppDeps, request.app.state.deps)
