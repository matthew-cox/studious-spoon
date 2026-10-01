"""RFC 9457 problem+json responses for every error the API returns (spec §9)."""

import logging
from collections.abc import Mapping
from http import HTTPStatus
from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import InterfaceError, OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)
PROBLEM_JSON = "application/problem+json"


class ProblemError(Exception):
    def __init__(
        self,
        status: int,
        title: str | None = None,
        detail: str | None = None,
        *,
        headers: dict[str, str] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(detail or title)
        self.status = status
        self.title = title or HTTPStatus(status).phrase
        self.detail = detail
        self.headers = headers
        self.extra = extra or {}


def problem_response(
    status: int,
    title: str | None = None,
    detail: str | None = None,
    *,
    headers: Mapping[str, str] | None = None,
    extra: dict[str, Any] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": "about:blank",
        "title": title or HTTPStatus(status).phrase,
        "status": status,
    }
    if detail:
        body["detail"] = detail
    body.update(extra or {})
    return JSONResponse(body, status_code=status, headers=headers, media_type=PROBLEM_JSON)


async def _problem(_: Request, exc: Exception) -> JSONResponse:
    err = cast(ProblemError, exc)
    return problem_response(err.status, err.title, err.detail, headers=err.headers, extra=err.extra)


async def _http(_: Request, exc: Exception) -> JSONResponse:
    err = cast(StarletteHTTPException, exc)
    detail: str | None = err.detail if isinstance(err.detail, str) else None
    if detail == HTTPStatus(err.status_code).phrase:
        detail = None
    return problem_response(err.status_code, detail=detail, headers=err.headers)


async def _validation(_: Request, exc: Exception) -> JSONResponse:
    err = cast(RequestValidationError, exc)
    errors = [
        {"loc": list(error["loc"]), "msg": error["msg"], "type": error["type"]}
        for error in err.errors()
    ]
    return problem_response(422, extra={"errors": errors})


async def _unavailable(_: Request, exc: Exception) -> JSONResponse:
    return problem_response(503, detail="database unavailable")


async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
    logger.error("unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
    return problem_response(500)


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ProblemError, _problem)
    app.add_exception_handler(StarletteHTTPException, _http)
    app.add_exception_handler(RequestValidationError, _validation)
    app.add_exception_handler(OperationalError, _unavailable)
    app.add_exception_handler(InterfaceError, _unavailable)
    app.add_exception_handler(OSError, _unavailable)
    app.add_exception_handler(Exception, _unexpected)
