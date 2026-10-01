import pytest

pytestmark = pytest.mark.integration


async def test_readyz_is_ok_with_database(client):
    response = await client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
