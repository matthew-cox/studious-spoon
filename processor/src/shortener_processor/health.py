"""Minimal stdlib HTTP health endpoints for the container healthcheck / load balancer."""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)
Check = Callable[[], Awaitable[bool]]
_REASONS = {200: "OK", 404: "Not Found", 503: "Service Unavailable"}
_READ_TIMEOUT = 5.0


async def _run_check(check: Check) -> bool:
    try:
        return await check()
    except Exception:
        logger.warning("health check raised", exc_info=True)
        return False


async def _handle(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, routes: dict[str, Check]
) -> None:
    try:
        request_line = await asyncio.wait_for(reader.readline(), _READ_TIMEOUT)
        parts = request_line.decode("latin-1").split()
        path = parts[1] if len(parts) >= 2 else ""
        while (await asyncio.wait_for(reader.readline(), _READ_TIMEOUT)) not in (
            b"\r\n",
            b"\n",
            b"",
        ):
            pass  # discard headers
        check = routes.get(path)
        if check is None:
            status, body = 404, {"status": "not found"}
        elif await _run_check(check):
            status, body = 200, {"status": "ok"}
        else:
            status, body = 503, {"status": "unavailable"}
        payload = json.dumps(body).encode()
        head = (
            f"HTTP/1.1 {status} {_REASONS[status]}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(payload)}\r\n"
            "Connection: close\r\n\r\n"
        )
        writer.write(head.encode() + payload)
        await writer.drain()
    except (TimeoutError, ConnectionError):
        pass
    finally:
        writer.close()


async def start_health_server(host: str, port: int, *, live: Check, ready: Check) -> asyncio.Server:
    routes = {"/healthz": live, "/readyz": ready}
    return await asyncio.start_server(lambda r, w: _handle(r, w, routes), host, port)
