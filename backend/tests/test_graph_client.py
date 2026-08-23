import httpx
import pytest

from app.providers.graph_client import GraphClient, GraphCredentials, GraphError

CREDENTIALS = GraphCredentials(tenant_id="tenant-1", client_id="client-1", client_secret="secret-1", authority="https://login.microsoftonline.com/tenant-1")


def token_response() -> httpx.Response:
    return httpx.Response(200, json={"access_token": "fake-token", "expires_in": 3600})


@pytest.mark.asyncio
async def test_get_all_follows_odata_next_link():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/oauth2/v2.0/token"):
            return token_response()
        if "page2" in str(request.url):
            return httpx.Response(200, json={"value": [{"id": "2"}]})
        return httpx.Response(200, json={"value": [{"id": "1"}], "@odata.nextLink": "https://graph.microsoft.com/v1.0/users?page2"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = GraphClient(CREDENTIALS, http_client=http_client)
        items = await client.get_all("/users")
    assert [item["id"] for item in items] == ["1", "2"]


@pytest.mark.asyncio
async def test_token_reused_across_requests():
    token_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls
        if request.url.path.endswith("/oauth2/v2.0/token"):
            token_calls += 1
            return token_response()
        return httpx.Response(200, json={"value": []})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = GraphClient(CREDENTIALS, http_client=http_client)
        await client.get_all("/users")
        await client.get_all("/groups")
    assert token_calls == 1


@pytest.mark.parametrize("status,expected_code", [(401, "PROVIDER_AUTHENTICATION_FAILED"), (403, "PROVIDER_PERMISSION_DENIED"), (404, "PROVIDER_RESOURCE_NOT_FOUND"), (409, "PROVIDER_CONFLICT"), (429, "GRAPH_THROTTLED"), (500, "PROVIDER_UNAVAILABLE")])
@pytest.mark.asyncio
async def test_error_status_codes_map_to_stable_codes(status, expected_code):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/v2.0/token"):
            return token_response()
        return httpx.Response(status, json={"error": {"message": "boom"}})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = GraphClient(CREDENTIALS, http_client=http_client)
        with pytest.raises(GraphError) as error:
            await client.request("GET", "/users/1")
    assert error.value.code == expected_code


@pytest.mark.asyncio
async def test_token_request_failure_is_authentication_failed():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_client"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = GraphClient(CREDENTIALS, http_client=http_client)
        with pytest.raises(GraphError) as error:
            await client.get_all("/users")
    assert error.value.code == "PROVIDER_AUTHENTICATION_FAILED"


@pytest.mark.asyncio
async def test_get_one_returns_none_on_404():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/v2.0/token"):
            return token_response()
        return httpx.Response(404, json={})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = GraphClient(CREDENTIALS, http_client=http_client)
        result = await client.get_one("/users/missing")
    assert result is None
