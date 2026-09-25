import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import IdentityProvider, User
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


async def _seed_manager_and_employee(factory) -> dict:
    async with factory() as session:
        provider = IdentityProvider(name="Directory", type="MOCK", status="CONNECTED", tenant_id="t")
        session.add(provider)
        await session.flush()
        manager = User(provider_id=provider.id, external_id="mgr-1", email="manager@x.com", display_name="Mo Manager", status="ACTIVE")
        employee = User(provider_id=provider.id, external_id="emp-1", email="employee@x.com", display_name="Ed Employee", status="ACTIVE")
        session.add_all([manager, employee])
        await session.commit()
        await session.refresh(manager)
        await session.refresh(employee)
        return {"manager_id": manager.id, "employee_id": employee.id}


@pytest.mark.asyncio
async def test_admin_can_tag_a_user_as_manager(db_override):
    seeded = await _seed_manager_and_employee(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch(f"/api/v1/users/{seeded['manager_id']}/hierarchy", json={"employee_category": "MANAGER"})
    assert response.status_code == 200
    assert response.json()["employee_category"] == "MANAGER"


@pytest.mark.asyncio
async def test_admin_can_assign_an_employee_under_a_manager(db_override):
    seeded = await _seed_manager_and_employee(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.patch(f"/api/v1/users/{seeded['manager_id']}/hierarchy", json={"employee_category": "MANAGER"})
        response = await client.patch(f"/api/v1/users/{seeded['employee_id']}/hierarchy", json={"employee_category": "EMPLOYEE", "manager_id": str(seeded["manager_id"])})
        tree = await client.get("/api/v1/users/hierarchy-tree")
    assert response.status_code == 200
    assert response.json()["manager_id"] == str(seeded["manager_id"])
    nodes = {n["id"]: n for n in tree.json()}
    assert nodes[str(seeded["employee_id"])]["manager_id"] == str(seeded["manager_id"])
    assert nodes[str(seeded["manager_id"])]["employee_category"] == "MANAGER"


@pytest.mark.asyncio
async def test_a_user_cannot_be_their_own_manager(db_override):
    seeded = await _seed_manager_and_employee(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch(f"/api/v1/users/{seeded['manager_id']}/hierarchy", json={"manager_id": str(seeded["manager_id"])})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "MANAGER_CYCLE_DETECTED"


@pytest.mark.asyncio
async def test_assigning_a_manager_cycle_is_rejected(db_override):
    """A reports to B; then trying to set B to report to A must be rejected — no partial write."""
    seeded = await _seed_manager_and_employee(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.patch(f"/api/v1/users/{seeded['employee_id']}/hierarchy", json={"manager_id": str(seeded["manager_id"])})
        assert first.status_code == 200
        second = await client.patch(f"/api/v1/users/{seeded['manager_id']}/hierarchy", json={"manager_id": str(seeded["employee_id"])})
    assert second.status_code == 400
    assert second.json()["error"]["code"] == "MANAGER_CYCLE_DETECTED"

    async with db_override.factory() as session:
        manager = await session.get(User, seeded["manager_id"])
    assert manager.manager_id is None  # rejected write never partially applied


@pytest.mark.asyncio
async def test_clearing_a_manager_assignment(db_override):
    seeded = await _seed_manager_and_employee(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.patch(f"/api/v1/users/{seeded['employee_id']}/hierarchy", json={"manager_id": str(seeded["manager_id"])})
        cleared = await client.patch(f"/api/v1/users/{seeded['employee_id']}/hierarchy", json={"clear_manager": True})
    assert cleared.json()["manager_id"] is None


@pytest.mark.asyncio
async def test_a_normal_user_cannot_edit_hierarchy(db_override):
    seeded = await _seed_manager_and_employee(db_override.factory)
    authenticate_as("AccessPilot.User", subject="regular-user-oid")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch(f"/api/v1/users/{seeded['employee_id']}/hierarchy", json={"employee_category": "EMPLOYEE"})
    assert response.status_code == 403
