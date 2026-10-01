async def test_healthz_is_always_ok(client):
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readyz_is_503_problem_when_database_is_unreachable(client):
    response = await client.get("/readyz")
    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["status"] == 503
    assert body["title"] == "Service Unavailable"
    assert body["type"] == "about:blank"


async def test_unknown_api_route_is_problem_json_404(client):
    response = await client.get("/api/v1/does-not-exist")
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["title"] == "Not Found"


async def test_redirect_is_503_problem_when_database_is_unreachable(client):
    response = await client.get("/aZ3kQ9x")
    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"


async def test_redirect_records_no_metric_when_the_lookup_fails(client, metric_value):
    response = await client.get("/aZ3kQ9x")
    assert response.status_code == 503
    assert metric_value("shortener.redirects") == 0
    assert metric_value("shortener.redirect.duration") == 0


async def test_unexpected_error_is_a_problem_json_500(deps):
    from httpx import ASGITransport, AsyncClient

    from shortener_api.main import create_app

    app = create_app(deps)

    @app.get("/api/v1/boom")
    async def boom() -> None:
        raise RuntimeError("secret internals")

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://sho.rt") as http:
        response = await http.get("/api/v1/boom")
    assert response.status_code == 500
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["status"] == 500
    assert "secret internals" not in response.text
