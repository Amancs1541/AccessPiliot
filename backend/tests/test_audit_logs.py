import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import AccessAssignment, Group, IdentityProvider, User
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


def authenticate_as(role: str) -> None:
    async def dependency():
        return AuthenticatedUser("admin-oid", "Admin", "admin@example.com", "tenant", (role,), {})
    app.dependency_overrides[require_authenticated_user] = dependency


@pytest.mark.asyncio
async def test_reassigning_same_group_is_visible_in_audit_logs(db_override):
    async with db_override.factory() as session:
        provider = IdentityProvider(name="Directory", type="MOCK", status="CONNECTED", tenant_id="t")
        session.add(provider)
        await session.flush()
        target_user = User(provider_id=provider.id, external_id="target-user", email="target@x.com", display_name="Target User", status="ACTIVE")
        group = Group(provider_id=provider.id, external_id="g1", name="Security Team", status="ACTIVE", is_privileged=False)
        session.add_all([target_user, group])
        await session.commit()
        ids = {"user_id": target_user.id, "group_id": group.id}

    authenticate_as("AccessPilot.Admin")
    payload = {"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/api/v1/assignments", json=payload)
        await client.post(f"/api/v1/assignments/{first.json()['id']}/activate", json={"duration_hours": 2})
        second = await client.post("/api/v1/assignments", json=payload)
        await client.post(f"/api/v1/assignments/{second.json()['id']}/activate", json={"duration_hours": 2})
        logs = await client.get("/api/v1/audit-logs")

    assert logs.status_code == 200
    actions = [entry["action"] for entry in logs.json()]
    assert "ASSIGNMENT_REVOKED" in actions
    revoked_entry = next(entry for entry in logs.json() if entry["action"] == "ASSIGNMENT_REVOKED")
    assert revoked_entry["metadata"]["reason"] == "SUPERSEDED_BY_NEW_ASSIGNMENT"
    assert revoked_entry["result"] == "SUCCESS"


@pytest.mark.asyncio
async def test_assignment_audit_entries_include_target_user_detail(db_override):
    async with db_override.factory() as session:
        provider = IdentityProvider(name="Directory", type="MOCK", status="CONNECTED", tenant_id="t")
        session.add(provider)
        await session.flush()
        target_user = User(provider_id=provider.id, external_id="target-user", email="target@x.com", display_name="Target User", status="ACTIVE")
        group = Group(provider_id=provider.id, external_id="g1", name="Security Team", status="ACTIVE", is_privileged=False)
        session.add_all([target_user, group])
        await session.commit()
        ids = {"user_id": target_user.id, "group_id": group.id}

    authenticate_as("AccessPilot.Admin")
    payload = {"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/assignments", json=payload)
        logs = await client.get("/api/v1/audit-logs")

    assert logs.status_code == 200
    created_entry = next(entry for entry in logs.json() if entry["action"] == "ASSIGNMENT_CREATED")
    assert created_entry["target_user_display_name"] == "Target User"
    assert created_entry["target_user_email"] == "target@x.com"


@pytest.mark.asyncio
async def test_audit_logs_denied_for_normal_user(db_override):
    authenticate_as("AccessPilot.User")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/audit-logs")
    assert response.status_code == 403
