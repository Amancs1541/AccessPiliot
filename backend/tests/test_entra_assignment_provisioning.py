from types import SimpleNamespace

import httpx
import pytest

from app.core.config import get_settings
from app.providers.entra import EntraProvider
from app.providers.graph_client import GraphClient, GraphError


def provider_row():
    return SimpleNamespace(tenant_id="tenant-1", client_id="client-1", authority="https://login.microsoftonline.com/tenant-1")


@pytest.fixture(autouse=True)
def configure_secret(monkeypatch):
    monkeypatch.setenv("ENTRA_API_CLIENT_SECRET", "test-secret")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def install_transport(monkeypatch, handler):
    original_init = GraphClient.__init__

    def patched_init(self, credentials, *, http_client=None):
        original_init(self, credentials, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    monkeypatch.setattr(GraphClient, "__init__", patched_init)


def token_response() -> httpx.Response:
    return httpx.Response(200, json={"access_token": "fake-token", "expires_in": 3600})


@pytest.mark.asyncio
async def test_add_group_member_posts_odata_ref(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/v2.0/token"):
            return token_response()
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["body"] = request.content.decode()
        return httpx.Response(204)

    install_transport(monkeypatch, handler)
    result = await EntraProvider(provider_row()).add_group_member("group-1", "user-1")
    assert result is True
    assert captured["method"] == "POST"
    assert captured["path"] == "/v1.0/groups/group-1/members/$ref"
    assert "directoryObjects/user-1" in captured["body"]


@pytest.mark.asyncio
async def test_add_group_member_already_member_is_idempotent_success(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/v2.0/token"):
            return token_response()
        return httpx.Response(409, json={"error": {"message": "already a member"}})

    install_transport(monkeypatch, handler)
    result = await EntraProvider(provider_row()).add_group_member("group-1", "user-1")
    assert result is True


@pytest.mark.asyncio
async def test_add_group_member_already_member_via_real_graph_400_is_idempotent_success(monkeypatch):
    """Regression: Microsoft Graph reports a duplicate group member add as HTTP 400 (not 409) with
    "...already exist..." in the error message — confirmed against a live tenant. Must still be treated
    as an idempotent success, or approving an assignment for an already-member user fails forever."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/v2.0/token"):
            return token_response()
        return httpx.Response(400, json={"error": {"code": "Request_BadRequest", "message": "One or more added object references already exist for the following modified properties: 'members'."}})

    install_transport(monkeypatch, handler)
    result = await EntraProvider(provider_row()).add_group_member("group-1", "user-1")
    assert result is True


@pytest.mark.asyncio
async def test_add_group_member_genuine_400_still_raises(monkeypatch):
    """A 400 that is NOT the duplicate-member quirk must still surface as a real failure."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/v2.0/token"):
            return token_response()
        return httpx.Response(400, json={"error": {"code": "Request_BadRequest", "message": "Invalid object identifier"}})

    install_transport(monkeypatch, handler)
    with pytest.raises(GraphError) as error:
        await EntraProvider(provider_row()).add_group_member("group-1", "user-1")
    assert error.value.code == "PROVIDER_UNAVAILABLE"


@pytest.mark.asyncio
async def test_add_group_member_permission_denied_raises(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/v2.0/token"):
            return token_response()
        return httpx.Response(403, json={"error": {"message": "Insufficient privileges"}})

    install_transport(monkeypatch, handler)
    with pytest.raises(GraphError) as error:
        await EntraProvider(provider_row()).add_group_member("group-1", "user-1")
    assert error.value.code == "PROVIDER_PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_remove_group_member_deletes_ref(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/v2.0/token"):
            return token_response()
        captured["method"] = request.method
        captured["path"] = request.url.path
        return httpx.Response(204)

    install_transport(monkeypatch, handler)
    result = await EntraProvider(provider_row()).remove_group_member("group-1", "user-1")
    assert result is True
    assert captured["method"] == "DELETE"
    assert captured["path"] == "/v1.0/groups/group-1/members/user-1/$ref"


@pytest.mark.asyncio
async def test_remove_group_member_already_gone_is_idempotent_success(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/v2.0/token"):
            return token_response()
        return httpx.Response(404, json={})

    install_transport(monkeypatch, handler)
    result = await EntraProvider(provider_row()).remove_group_member("group-1", "user-1")
    assert result is True


@pytest.mark.asyncio
async def test_activate_assignment_dispatches_to_role_member_add(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/v2.0/token"):
            return token_response()
        captured["path"] = request.url.path
        return httpx.Response(204)

    install_transport(monkeypatch, handler)
    result = await EntraProvider(provider_row()).activate_assignment({"resource_type": "ROLE", "target_external_id": "role-1", "user_external_id": "user-1"})
    assert result is True
    assert captured["path"] == "/v1.0/directoryRoles/role-1/members/$ref"


@pytest.mark.asyncio
async def test_revoke_assignment_dispatches_to_group_member_remove(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/v2.0/token"):
            return token_response()
        captured["method"] = request.method
        captured["path"] = request.url.path
        return httpx.Response(204)

    install_transport(monkeypatch, handler)
    result = await EntraProvider(provider_row()).revoke_assignment({"resource_type": "GROUP", "target_external_id": "group-1", "user_external_id": "user-1"})
    assert result is True
    assert captured["method"] == "DELETE"
    assert captured["path"] == "/v1.0/groups/group-1/members/user-1/$ref"


@pytest.mark.asyncio
async def test_activate_assignment_missing_fields_raises_validation_error():
    with pytest.raises(GraphError) as error:
        await EntraProvider(provider_row()).activate_assignment({"resource_type": "GROUP"})
    assert error.value.code == "VALIDATION_ERROR"
