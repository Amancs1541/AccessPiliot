import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.security.auth import AuthenticatedUser, require_authenticated_user


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
    yield database
    app.dependency_overrides.clear()
    await database.engine.dispose()


def authenticate_as(role: str, subject: str = "admin-oid") -> None:
    async def dependency():
        return AuthenticatedUser(subject, "Admin", "admin@example.com", "tenant", (role,), {})
    app.dependency_overrides[require_authenticated_user] = dependency


@pytest.mark.asyncio
async def test_admin_can_add_departments(db_override):
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/api/v1/policies/departments", json={"name": "AppDev"})
        second = await client.post("/api/v1/policies/departments", json={"name": "IT"})
        listed = await client.get("/api/v1/policies/departments")
    assert first.status_code == 201
    assert second.status_code == 201
    names = [d["name"] for d in listed.json()]
    assert names == ["AppDev", "IT"]  # alphabetical


@pytest.mark.asyncio
async def test_duplicate_department_name_is_rejected(db_override):
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/policies/departments", json={"name": "IT"})
        second = await client.post("/api/v1/policies/departments", json={"name": "IT"})
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_admin_can_delete_a_department(db_override):
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/policies/departments", json={"name": "AppDev"})
        department_id = created.json()["id"]
        deleted = await client.delete(f"/api/v1/policies/departments/{department_id}")
        listed = await client.get("/api/v1/policies/departments")
    assert deleted.status_code == 204
    assert listed.json() == []


@pytest.mark.asyncio
async def test_a_normal_user_cannot_manage_departments(db_override):
    authenticate_as("AccessPilot.User", subject="regular-user-oid")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/policies/departments", json={"name": "AppDev"})
    assert response.status_code == 403


async def _seed_manager(db_override):
    from app.models import IdentityProvider, User
    async with db_override.factory() as session:
        provider = IdentityProvider(name="Entra", type="ENTRA", status="CONNECTED", tenant_id="t")
        session.add(provider)
        await session.flush()
        manager = User(provider_id=provider.id, external_id="mgr-oid", email="mgr@x.com", display_name="Dana Manager", status="ACTIVE")
        session.add(manager)
        await session.commit()
        await session.refresh(manager)
        return manager.id


@pytest.mark.asyncio
async def test_admin_can_set_and_clear_a_departments_manager(db_override):
    manager_id = await _seed_manager(db_override)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/policies/departments", json={"name": "Finance"})
        department_id = created.json()["id"]
        set_response = await client.patch(f"/api/v1/policies/departments/{department_id}/manager", json={"manager_id": str(manager_id)})
        listed = await client.get("/api/v1/policies/departments")
        clear_response = await client.patch(f"/api/v1/policies/departments/{department_id}/manager", json={"manager_id": None})
    assert set_response.status_code == 200
    assert set_response.json()["manager_id"] == str(manager_id)
    assert set_response.json()["manager_display_name"] == "Dana Manager"
    assert listed.json()[0]["manager_id"] == str(manager_id)
    assert clear_response.status_code == 200
    assert clear_response.json()["manager_id"] is None


@pytest.mark.asyncio
async def test_setting_an_unknown_manager_is_rejected(db_override):
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/policies/departments", json={"name": "Finance"})
        department_id = created.json()["id"]
        response = await client.patch(f"/api/v1/policies/departments/{department_id}/manager", json={"manager_id": "00000000-0000-0000-0000-000000000000"})
    assert response.status_code == 404
