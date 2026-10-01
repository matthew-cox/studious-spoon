import httpx
import pytest

from shortener_processor.health import start_health_server


async def yes() -> bool:
    return True


async def no() -> bool:
    return False


async def boom() -> bool:
    raise RuntimeError("check crashed")


@pytest.fixture
async def serve_checks():
    servers = []

    async def _serve(live, ready) -> str:
        server = await start_health_server("127.0.0.1", 0, live=live, ready=ready)
        servers.append(server)
        port = server.sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}"

    yield _serve
    for server in servers:
        server.close()
        await server.wait_closed()


async def test_ok_checks_return_200_json(serve_checks):
    base = await serve_checks(yes, yes)
    async with httpx.AsyncClient() as http:
        for path in ("/healthz", "/readyz"):
            response = await http.get(base + path)
            assert response.status_code == 200
            assert response.headers["content-type"] == "application/json"
            assert response.json() == {"status": "ok"}


async def test_failing_checks_return_503(serve_checks):
    base = await serve_checks(no, no)
    async with httpx.AsyncClient() as http:
        assert (await http.get(base + "/healthz")).status_code == 503
        assert (await http.get(base + "/readyz")).json() == {"status": "unavailable"}


async def test_a_crashing_check_is_503_not_a_dropped_connection(serve_checks):
    base = await serve_checks(boom, yes)
    async with httpx.AsyncClient() as http:
        assert (await http.get(base + "/healthz")).status_code == 503


async def test_unknown_path_is_404(serve_checks):
    base = await serve_checks(yes, yes)
    async with httpx.AsyncClient() as http:
        assert (await http.get(base + "/metrics")).status_code == 404
