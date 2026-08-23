from types import SimpleNamespace

import httpx
import pytest

from app.core.config import get_settings
from app.providers.base import NewGroupRequest, NewUserRequest, ProviderConflictError
from app.providers.entra import EntraProvider
from app.providers.graph_client import GraphClient


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
async def test_get_users_maps_graph_fields(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/v2.0/token"):
            return token_response()
        return httpx.Response(200, json={"value": [{"id": "u1", "userPrincipalName": "a@b.com", "mail": "a@b.com", "displayName": "A B", "givenName": "A", "surname": "B", "department": "Eng", "jobTitle": "Engineer", "accountEnabled": True}]})

    install_transport(monkeypatch, handler)
    users = await EntraProvider(provider_row()).get_users()
    assert len(users) == 1
    assert users[0].external_id == "u1" and users[0].email == "a@b.com" and users[0].status == "ACTIVE"


@pytest.mark.asyncio
async def test_get_groups_marks_role_assignable_as_privileged(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/v2.0/token"):
            return token_response()
        return httpx.Response(200, json={"value": [{"id": "g1", "displayName": "Admins", "isAssignableToRole": True}]})

    install_transport(monkeypatch, handler)
    groups = await EntraProvider(provider_row()).get_groups()
    assert groups[0].is_privileged is True


@pytest.mark.asyncio
async def test_get_group_members_paginates(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/v2.0/token"):
            return token_response()
        if "page2" in str(request.url):
            return httpx.Response(200, json={"value": [{"id": "u2", "displayName": "U2", "mail": "u2@b.com", "accountEnabled": True}]})
        return httpx.Response(200, json={"value": [{"id": "u1", "displayName": "U1", "mail": "u1@b.com", "accountEnabled": True}], "@odata.nextLink": "https://graph.microsoft.com/v1.0/groups/g1/members?page2"})

    install_transport(monkeypatch, handler)
    members = await EntraProvider(provider_row()).get_group_members("g1")
    assert [m.external_id for m in members] == ["u1", "u2"]


@pytest.mark.asyncio
async def test_get_roles_flags_administrator_roles(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/v2.0/token"):
            return token_response()
        return httpx.Response(200, json={"value": [{"id": "r1", "displayName": "Global Administrator"}, {"id": "r2", "displayName": "Reports Reader"}]})

    install_transport(monkeypatch, handler)
    roles = await EntraProvider(provider_row()).get_roles()
    privileged = {role.name: role.is_privileged for role in roles}
    assert privileged["Global Administrator"] is True
    assert privileged["Reports Reader"] is False


@pytest.mark.asyncio
async def test_create_user_detects_duplicate(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/v2.0/token"):
            return token_response()
        if request.method == "GET":
            return httpx.Response(200, json={"value": [{"id": "existing"}]})
        raise AssertionError("should not attempt create when a duplicate exists")

    install_transport(monkeypatch, handler)
    with pytest.raises(ProviderConflictError):
        await EntraProvider(provider_row()).create_user(NewUserRequest(display_name="New User", user_principal_name="new.user@tenant.onmicrosoft.com", mail_nickname="newuser"))


@pytest.mark.asyncio
async def test_create_user_success_returns_password_once(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/v2.0/token"):
            return token_response()
        if request.method == "GET":
            return httpx.Response(200, json={"value": []})
        return httpx.Response(201, json={"id": "new-id", "userPrincipalName": "new.user@tenant.onmicrosoft.com", "mail": "new.user@tenant.onmicrosoft.com", "displayName": "New User", "accountEnabled": True})

    install_transport(monkeypatch, handler)
    created = await EntraProvider(provider_row()).create_user(NewUserRequest(display_name="New User", user_principal_name="new.user@tenant.onmicrosoft.com", mail_nickname="newuser"))
    assert created.user.external_id == "new-id"
    assert created.temporary_password


@pytest.mark.asyncio
async def test_create_group_detects_duplicate(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/v2.0/token"):
            return token_response()
        if request.method == "GET":
            return httpx.Response(200, json={"value": [{"id": "existing"}]})
        raise AssertionError("should not attempt create when a duplicate exists")

    install_transport(monkeypatch, handler)
    with pytest.raises(ProviderConflictError):
        await EntraProvider(provider_row()).create_group(NewGroupRequest(display_name="Existing Group"))


@pytest.mark.asyncio
async def test_graph_permission_denied_is_mapped(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/v2.0/token"):
            return token_response()
        return httpx.Response(403, json={"error": {"message": "Insufficient privileges"}})

    install_transport(monkeypatch, handler)
    from app.providers.graph_client import GraphError

    with pytest.raises(GraphError) as error:
        await EntraProvider(provider_row()).get_users()
    assert error.value.code == "PROVIDER_PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_missing_client_secret_raises_authentication_failed(monkeypatch):
    monkeypatch.delenv("ENTRA_API_CLIENT_SECRET", raising=False)
    get_settings.cache_clear()
    from app.providers.graph_client import GraphError

    with pytest.raises(GraphError) as error:
        await EntraProvider(provider_row()).get_users()
    assert error.value.code == "PROVIDER_AUTHENTICATION_FAILED"
