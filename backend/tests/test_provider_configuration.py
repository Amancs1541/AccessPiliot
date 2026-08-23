import os
from uuid import uuid4

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
from app.security import auth
from app.api.v1.providers import provider_manage, provider_read
from app.models import IdentityProvider
from app.services.provider_configuration import _connector
from app.providers.entra import EntraProvider
from app.security.auth import AuthenticatedUser

class TestSession:
    def __init__(self): self.engine = create_async_engine("sqlite+aiosqlite:///:memory:"); self.factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

@pytest_asyncio.fixture
async def db_override():
    database = TestSession()
    async with database.engine.begin() as conn: await conn.run_sync(Base.metadata.create_all)
    async def override():
        async with database.factory() as session: yield session
    app.dependency_overrides[get_db] = override
    yield
    app.dependency_overrides.clear(); await database.engine.dispose()

def user(role: str):
    async def dependency(): return AuthenticatedUser("actor-1", "Admin", "admin@example.com", "tenant", (role,), {})
    return dependency

def test_connector_uses_database_provider_record():
    provider = IdentityProvider(type="ENTRA", status="CONFIGURED", name="Entra DEV", tenant_id="db-tenant", client_id="db-client", authority="https://login.microsoftonline.com/db-tenant", api_audience="api://db-client", api_scope="api://db-client/access_as_user")
    connector = _connector(provider)
    assert isinstance(connector, EntraProvider)
    assert connector.provider is provider
    assert connector.provider.tenant_id == "db-tenant"

@pytest.mark.asyncio
async def test_admin_provider_crud_and_mock_connection(db_override):
    app.dependency_overrides[provider_manage] = user("AccessPilot.Admin")
    app.dependency_overrides[provider_read] = user("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {"name":"Local Mock","provider_type":"MOCK","tenant_id":"tenant-1","client_id":"client-1","api_audience":"api://client-1","api_scope":"api://client-1/access_as_user","redirect_uri_metadata":{"development":"http://localhost:5173"}}
        created = await client.post("/api/v1/providers", json=payload)
        assert created.status_code == 201
        provider_id = created.json()["id"]
        listed = await client.get("/api/v1/providers")
        assert listed.status_code == 200 and len(listed.json()) == 1
        tested = await client.post(f"/api/v1/providers/{provider_id}/test-connection")
        assert tested.status_code == 200 and tested.json()["status"] == "CONNECTED"
        updated = await client.patch(f"/api/v1/providers/{provider_id}", json={"name":"Updated Mock"})
        assert updated.status_code == 200 and updated.json()["name"] == "Updated Mock"
        deleted = await client.delete(f"/api/v1/providers/{provider_id}")
        assert deleted.status_code == 204

@pytest.mark.asyncio
async def test_user_provider_access_is_denied():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/providers")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_provider_sync_persists_run_and_is_listed(db_override, monkeypatch):
    from app.providers.base import NormalizedGroup, NormalizedRole, NormalizedUser
    from app.api.v1.providers import provider_manage, provider_read, provider_sync, sync_read

    app.dependency_overrides[provider_manage] = user("AccessPilot.Admin")
    app.dependency_overrides[provider_read] = user("AccessPilot.Admin")
    app.dependency_overrides[provider_sync] = user("AccessPilot.Admin")
    app.dependency_overrides[sync_read] = user("AccessPilot.Admin")

    async def fake_get_users(self, query=None): return [NormalizedUser("u1", "u1@x.com", "User One")]
    async def fake_get_groups(self, query=None): return [NormalizedGroup("g1", "Group One")]
    async def fake_get_group_members(self, external_id): return []
    async def fake_get_roles(self, query=None): return [NormalizedRole("r1", "Reports Reader")]
    monkeypatch.setattr("app.providers.entra.EntraProvider.get_users", fake_get_users)
    monkeypatch.setattr("app.providers.entra.EntraProvider.get_groups", fake_get_groups)
    monkeypatch.setattr("app.providers.entra.EntraProvider.get_group_members", fake_get_group_members)
    monkeypatch.setattr("app.providers.entra.EntraProvider.get_roles", fake_get_roles)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/providers", json={"name": "Entra", "provider_type": "ENTRA", "tenant_id": "tenant-1", "client_id": "client-1", "authority": "https://login.microsoftonline.com/tenant-1", "api_audience": "api://client-1", "api_scope": "api://client-1/access_as_user"})
        provider_id = created.json()["id"]
        sync_response = await client.post(f"/api/v1/providers/{provider_id}/sync")
        runs_response = await client.get(f"/api/v1/providers/{provider_id}/sync-runs")

    assert sync_response.status_code == 200
    assert sync_response.json()["status"] == "COMPLETED"
    assert sync_response.json()["users_processed"] == 1
    assert len(runs_response.json()) == 1


@pytest.mark.asyncio
async def test_delete_provider_succeeds_with_foreign_keys_enforced_and_dependent_audit_rows():
    """Regression test: PostgreSQL enforces the audit_logs/sync_runs FKs; SQLite does not unless enabled.
    This reproduces that enforcement so a delete_provider() ordering bug can't hide behind SQLite's default laxity."""
    from sqlalchemy import event, text
    from app.models import AuditLog, SyncRun
    from app.services.provider_configuration import create_provider, delete_provider, list_providers
    from app.schemas.providers import ProviderCreate

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_fk(dbapi_connection, _):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        provider = await create_provider(session, ProviderCreate(name="Entra", provider_type="ENTRA", tenant_id="t"), "req-1")
        session.add(SyncRun(provider_id=provider.id, status="COMPLETED", started_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc)))
        session.add(AuditLog(action="USER_SYNCED", target_type="PROVIDER", target_id=provider.id, provider_id=provider.id, request_id="req-2", result="SUCCESS"))
        await session.commit()

        await delete_provider(session, provider.id, "req-3")
        assert len(await list_providers(session)) == 0

        audit_rows = (await session.execute(text("SELECT provider_id FROM audit_logs"))).fetchall()
        assert len(audit_rows) == 3  # PROVIDER_CREATED (from create_provider) + USER_SYNCED (seeded) + PROVIDER_DELETED
        assert all(row[0] is None for row in audit_rows)
    await engine.dispose()
