"""Calls the API with the signed-in user's own token (token relay, spec §8). The API decides."""

import logging
from datetime import datetime
from http import HTTPStatus
from typing import Any

import httpx

logger = logging.getLogger(__name__)
_CORE_PROBLEM_KEYS = {"type", "title", "status", "detail"}


class ApiError(Exception):
    def __init__(
        self,
        status: int,
        title: str,
        detail: str | None = None,
        extra: dict[str, Any] | None = None,
    ):
        super().__init__(f"{status} {title}")
        self.status = status
        self.title = title
        self.detail = detail
        self.extra = extra or {}


def _phrase(status: int) -> str:
    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return "Error"


class ApiClient:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    @property
    def http_client(self) -> httpx.AsyncClient:
        return self._http

    async def _call(
        self,
        method: str,
        path: str,
        token: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        try:
            response = await self._http.request(
                method, path, params=params, json=json, headers={"Authorization": f"Bearer {token}"}
            )
        except httpx.HTTPError as exc:
            logger.warning("API request failed: %s %s: %r", method, path, exc)
            raise ApiError(503, "API unavailable") from exc
        if response.status_code >= 400:
            try:
                body = response.json()
            except ValueError:
                body = {}
            if not isinstance(body, dict):
                body = {}
            raise ApiError(
                response.status_code,
                str(body.get("title") or _phrase(response.status_code)),
                body.get("detail"),
                {k: v for k, v in body.items() if k not in _CORE_PROBLEM_KEYS},
            )
        if response.status_code == 204:
            return None
        return response.json()

    async def me(self, token: str) -> Any:
        return await self._call("GET", "/api/v1/me", token)

    async def summary(self, token: str) -> Any:
        return await self._call("GET", "/api/v1/stats/summary", token)

    async def list_links(
        self,
        token: str,
        *,
        q: str | None = None,
        status: str | None = None,
        owner: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Any:
        params: dict[str, Any] = {"page": page, "page_size": page_size}
        if q:
            params["q"] = q
        if status:
            params["status"] = status
        if owner:
            params["owner"] = owner
        return await self._call("GET", "/api/v1/links", token, params=params)

    async def create_link(self, token: str, target_url: str) -> Any:
        return await self._call("POST", "/api/v1/links", token, json={"target_url": target_url})

    async def get_link(self, token: str, link_id: str) -> Any:
        return await self._call("GET", f"/api/v1/links/{link_id}", token)

    async def update_link(
        self,
        token: str,
        link_id: str,
        *,
        target_url: str | None = None,
        is_active: bool | None = None,
    ) -> Any:
        body: dict[str, Any] = {}
        if target_url is not None:
            body["target_url"] = target_url
        if is_active is not None:
            body["is_active"] = is_active
        return await self._call("PATCH", f"/api/v1/links/{link_id}", token, json=body)

    async def delete_link(self, token: str, link_id: str) -> Any:
        return await self._call("DELETE", f"/api/v1/links/{link_id}", token)

    async def block_link(self, token: str, link_id: str, reason: str) -> Any:
        return await self._call(
            "POST", f"/api/v1/links/{link_id}/block", token, json={"reason": reason}
        )

    async def unblock_link(self, token: str, link_id: str, reason: str) -> Any:
        return await self._call(
            "POST", f"/api/v1/links/{link_id}/unblock", token, json={"reason": reason}
        )

    async def link_events(self, token: str, link_id: str) -> Any:
        return await self._call("GET", f"/api/v1/links/{link_id}/events", token)

    async def link_stats(
        self,
        token: str,
        link_id: str,
        *,
        bucket: str = "hour",
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> Any:
        params: dict[str, Any] = {"bucket": bucket}
        if start is not None:
            params["from"] = start.isoformat()
        if end is not None:
            params["to"] = end.isoformat()
        return await self._call("GET", f"/api/v1/links/{link_id}/stats", token, params=params)
