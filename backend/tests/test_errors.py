import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_missing_route_does_not_expose_internal_details() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/missing", headers={"X-Request-ID": "missing-1"})
    assert response.status_code == 404
    payload = response.json()["error"]
    assert payload["code"] == "RESOURCE_NOT_FOUND"
    assert payload["requestId"] == "missing-1"
    assert "traceback" not in response.text.lower()
