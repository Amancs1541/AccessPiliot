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
