import os

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["ENVIRONMENT"] = "test"
os.environ["PROVIDER_MODE"] = "mock"

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import IdentityProvider
from app.providers.base import NormalizedApplication, NormalizedGroup, NormalizedRole, NormalizedUser
from app.providers.graph_client import GraphError
from app.providers.okta import OktaProvider
from app.providers.okta_client import OktaClient
from app.security.auth import AuthenticatedUser
from app.security.credential_encryption import encrypt_credential
from app.services.provider_configuration import _connector


class TestSession:
    def __init__(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def db_override():
    database = TestSession()
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def override():
        async with database.factory() as session:
            yield session

    app.dependency_overrides[get_db] = override
    yield
    app.dependency_overrides.clear()
    await database.engine.dispose()


def user(role: str):
    async def dependency():
        return AuthenticatedUser("actor-1", "Admin", "admin@example.com", "tenant", (role,), {})
    return dependency


def test_connector_dispatches_okta_provider_for_okta_type_only():
    """The dispatch point (_connector) is the single place Okta activation is gated: it only ever returns
    an OktaProvider for a provider row explicitly configured with type="OKTA" — every ENTRA/MOCK row is
    completely unaffected by OktaProvider existing."""
    okta = IdentityProvider(type="OKTA", status="CONFIGURED", name="Okta UAT", tenant_id="okta-org", organization_url="https://example.okta.com")
    connector = _connector(okta)
    assert isinstance(connector, OktaProvider)
    assert connector.provider is okta

    entra = IdentityProvider(type="ENTRA", status="CONFIGURED", name="Entra DEV", tenant_id="db-tenant")
    assert not isinstance(_connector(entra), OktaProvider)


def test_okta_provider_requires_an_organization_url():
    provider = OktaProvider(IdentityProvider(type="OKTA", status="CONFIGURED", name="Okta", tenant_id="t"))
    with pytest.raises(ValueError):
        provider._org_url()


def test_user_mapping_falls_back_to_login_when_no_name_is_set():
    mapped = OktaProvider._user_from_okta({"id": "00u1", "status": "ACTIVE", "profile": {"login": "svc-account@example.com", "email": "svc-account@example.com"}})
    assert mapped.display_name == "svc-account@example.com"
    assert mapped.status == "ACTIVE"


def test_user_mapping_marks_non_active_status_as_disabled():
    mapped = OktaProvider._user_from_okta({"id": "00u2", "status": "SUSPENDED", "profile": {"firstName": "A", "lastName": "B", "email": "a@example.com"}})
    assert mapped.status == "DISABLED"
    assert mapped.display_name == "A B"


def test_application_mapping_falls_back_to_a_default_access_role():
    mapped = OktaProvider._application_from_okta({"id": "0oa1", "label": "Internal Reporting Service", "status": "ACTIVE"})
    assert mapped.name == "Internal Reporting Service"
    assert len(mapped.app_roles) == 1
    assert mapped.app_roles[0].name == "Default Access"


@pytest.mark.asyncio
async def test_okta_client_paginates_via_link_header():
    """Okta pages with an RFC 5988 Link header, unlike Graph's @odata.nextLink body field — this is the one
    piece of OktaClient that's meaningfully different from GraphClient and worth its own direct test."""
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        assert request.headers["Authorization"] == "SSWS test-token"
        if "after" not in request.url.params:
            return httpx.Response(200, json=[{"id": "1"}], headers={"Link": '<https://example.okta.com/api/v1/users?after=abc>; rel="next"'})
        return httpx.Response(200, json=[{"id": "2"}])

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = OktaClient("https://example.okta.com", "test-token", http_client=http_client)
        items = await client.get_all("/users")
    assert [item["id"] for item in items] == ["1", "2"]
    assert calls["count"] == 2


@pytest.mark.asyncio
async def test_okta_client_maps_401_to_authentication_failed():
    transport = httpx.MockTransport(lambda request: httpx.Response(401, json={"errorSummary": "Invalid token"}))
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = OktaClient("https://example.okta.com", "bad-token", http_client=http_client)
        with pytest.raises(GraphError) as exc_info:
            await client.request("GET", "/users")
    assert exc_info.value.code == "PROVIDER_AUTHENTICATION_FAILED"


def test_connector_uses_database_provider_record():
    from app.providers.entra import EntraProvider
    provider = IdentityProvider(type="ENTRA", status="CONFIGURED", name="Entra DEV", tenant_id="db-tenant", client_id="db-client", authority="https://login.microsoftonline.com/db-tenant", api_audience="api://db-client", api_scope="api://db-client/access_as_user")
    connector = _connector(provider)
    assert isinstance(connector, EntraProvider)


@pytest.mark.asyncio
async def test_admin_can_create_an_okta_provider_and_configure_its_credential(db_override):
    from app.api.v1.providers import provider_manage, provider_read
    app.dependency_overrides[provider_manage] = user("AccessPilot.Admin")
    app.dependency_overrides[provider_read] = user("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/providers", json={"name": "Okta UAT", "provider_type": "OKTA", "tenant_id": "okta-uat", "organization_url": "https://example.okta.com"})
        assert created.status_code == 201
        assert created.json()["provider_type"] == "OKTA"
        provider_id = created.json()["id"]

        configured = await client.patch(f"/api/v1/providers/{provider_id}/credentials", json={"graph_client_secret": "test-okta-api-token"})
        assert configured.status_code == 200
        assert configured.json()["credential_configured"] is True


@pytest.mark.asyncio
async def test_okta_sync_persists_run_and_is_listed(db_override, monkeypatch):
    from app.api.v1.providers import provider_manage, provider_read, provider_sync, sync_read

    app.dependency_overrides[provider_manage] = user("AccessPilot.Admin")
    app.dependency_overrides[provider_read] = user("AccessPilot.Admin")
    app.dependency_overrides[provider_sync] = user("AccessPilot.Admin")
    app.dependency_overrides[sync_read] = user("AccessPilot.Admin")

    async def fake_get_users(self, query=None):
        return [NormalizedUser("00u1", "svc@example.com", "Service Account")]
    async def fake_get_groups(self, query=None):
        return [NormalizedGroup("00g1", "Okta Group One")]
    async def fake_get_group_members(self, external_id):
        return []
    async def fake_get_roles(self, query=None):
        return [NormalizedRole("SUPER_ADMIN", "Super Administrator")]
    async def fake_get_applications(self, query=None):
        return [NormalizedApplication("0oa1", "Internal Tool")]
    monkeypatch.setattr("app.providers.okta.OktaProvider.get_users", fake_get_users)
    monkeypatch.setattr("app.providers.okta.OktaProvider.get_groups", fake_get_groups)
    monkeypatch.setattr("app.providers.okta.OktaProvider.get_group_members", fake_get_group_members)
    monkeypatch.setattr("app.providers.okta.OktaProvider.get_roles", fake_get_roles)
    monkeypatch.setattr("app.providers.okta.OktaProvider.get_applications", fake_get_applications)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/providers", json={"name": "Okta UAT", "provider_type": "OKTA", "tenant_id": "okta-uat", "organization_url": "https://example.okta.com"})
        provider_id = created.json()["id"]
        sync_response = await client.post(f"/api/v1/providers/{provider_id}/sync")
        runs_response = await client.get(f"/api/v1/providers/{provider_id}/sync-runs")

    assert sync_response.status_code == 200
    assert sync_response.json()["status"] == "COMPLETED"
    assert sync_response.json()["users_processed"] == 1
    assert len(runs_response.json()) == 1


@pytest.mark.asyncio
async def test_okta_provider_test_connection_reports_reachable_org_with_no_credential_yet(monkeypatch):
    """Mirrors EntraProvider.test_connection()'s own two-tier check: an unauthenticated tenant/org existence
    probe first, then (only if a credential is configured) an authenticated call. Without a credential, a
    reachable org is reported as True — same semantics as EntraProvider today."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/.well-known/okta-organization"
        return httpx.Response(200, json={"id": "org1", "pipeline": "idx"})

    class PatchedAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("app.providers.okta.httpx.AsyncClient", PatchedAsyncClient)

    provider_row = IdentityProvider(type="OKTA", status="CONFIGURED", name="Okta", tenant_id="okta-uat", organization_url="https://example.okta.com")
    provider = OktaProvider(provider_row)
    assert await provider.test_connection() is True


@pytest.fixture(autouse=True)
def _credential_key(monkeypatch):
    from app.core.config import get_settings
    from cryptography.fernet import Fernet
    monkeypatch.setenv("PROVIDER_CREDENTIAL_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _credentialed_okta_provider() -> OktaProvider:
    provider_row = IdentityProvider(type="OKTA", status="CONFIGURED", name="Okta", tenant_id="okta-uat", organization_url="https://example.okta.com", graph_client_secret_encrypted=encrypt_credential("test-token"))
    return OktaProvider(provider_row)


@pytest.mark.asyncio
async def test_set_application_enabled_calls_the_lifecycle_endpoint(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        return httpx.Response(200, json={})

    class PatchedAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("app.providers.okta_client.httpx.AsyncClient", PatchedAsyncClient)
    result = await _credentialed_okta_provider().set_application_enabled("0oa1", False)
    assert result is True
    assert seen["method"] == "POST"
    assert seen["path"].endswith("/apps/0oa1/lifecycle/deactivate")


@pytest.mark.asyncio
async def test_get_application_permissions_maps_okta_grants(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"issuer": "https://example.okta.com/oauth2/default", "scopeId": "okta.users.read"}])

    class PatchedAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("app.providers.okta_client.httpx.AsyncClient", PatchedAsyncClient)
    permissions = await _credentialed_okta_provider().get_application_permissions("0oa1")
    assert len(permissions) == 1
    assert permissions[0].role_name == "okta.users.read"
