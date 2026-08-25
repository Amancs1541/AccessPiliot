from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import AccessAssignment, Group, IdentityProvider, User
from app.security.auth import AuthenticatedUser, require_authenticated_user
from app.workers.expiration import expire_due_assignments


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


async def _seed(factory, provider_type: str):
    async with factory() as session:
        provider = IdentityProvider(name="Entra", type=provider_type, status="CONNECTED", tenant_id="t")
        session.add(provider)
        await session.flush()
        user = User(provider_id=provider.id, external_id="target-user", email="target@x.com", display_name="Target User", status="ACTIVE")
        group = Group(provider_id=provider.id, external_id="g1", name="Security Team", status="ACTIVE", is_privileged=False)
        session.add_all([user, group])
        await session.commit()
        return {"provider_id": provider.id, "user_id": user.id, "group_id": group.id}


@pytest.mark.asyncio
async def test_create_assignment_never_calls_the_provider_and_always_succeeds(db_override, monkeypatch):
    """Phase 5: create_assignment never grants real access (and so never calls the provider) — even against a
    misconfigured ENTRA provider, creation itself always succeeds, landing ELIGIBLE."""
    ids = await _seed(db_override.factory, "ENTRA")
    monkeypatch.delenv("ENTRA_API_CLIENT_SECRET", raising=False)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "justification": "Test justification."})
    assert response.status_code == 201
    assert response.json()["status"] == "ELIGIBLE"


@pytest.mark.asyncio
async def test_activate_does_not_activate_when_graph_grant_fails(db_override, monkeypatch):
    # ENTRA type with no configured secret -> the real Graph call fails safely.
    ids = await _seed(db_override.factory, "ENTRA")
    monkeypatch.delenv("ENTRA_API_CLIENT_SECRET", raising=False)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "justification": "Test justification."})
        assignment_id = created.json()["id"]
        response = await client.post(f"/api/v1/assignments/{assignment_id}/activate", json={"duration_hours": 2, "justification": "Test justification."})
    assert response.status_code in (502, 503)

    async with db_override.factory() as session:
        still_eligible = await session.get(AccessAssignment, UUID(assignment_id))
        assert still_eligible.status == "ELIGIBLE"  # never falsely marked ACTIVE


@pytest.mark.asyncio
async def test_approve_never_calls_the_provider_and_always_succeeds(db_override, monkeypatch):
    """Phase 5: approve_assignment only lifts a request to ELIGIBLE — it never grants real access (and so never
    calls the provider) itself, even against a provider whose grant call is broken."""
    ids = await _seed(db_override.factory, "MOCK")
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "approver_id": str(ids["user_id"]), "justification": "Test justification."})
        assignment_id = created.json()["id"]

    async def failing_activate(self, request):
        from app.providers.graph_client import GraphError
        raise GraphError("PROVIDER_UNAVAILABLE", "boom", 503)
    monkeypatch.setattr("app.providers.mock.MockProvider.activate_assignment", failing_activate)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        approved = await client.post(f"/api/v1/assignments/{assignment_id}/approve", json={"justification": "Test justification."})
    assert approved.status_code == 200
    assert approved.json()["status"] == "ELIGIBLE"


@pytest.mark.asyncio
async def test_activating_an_approved_assignment_does_not_activate_when_graph_grant_fails(db_override, monkeypatch):
    ids = await _seed(db_override.factory, "MOCK")
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "approver_id": str(ids["user_id"]), "justification": "Test justification."})
        assignment_id = created.json()["id"]
        await client.post(f"/api/v1/assignments/{assignment_id}/approve", json={"justification": "Test justification."})

    async def failing_activate(self, request):
        from app.providers.graph_client import GraphError
        raise GraphError("PROVIDER_UNAVAILABLE", "boom", 503)
    monkeypatch.setattr("app.providers.mock.MockProvider.activate_assignment", failing_activate)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        activated = await client.post(f"/api/v1/assignments/{assignment_id}/activate", json={"duration_hours": 2, "justification": "Test justification."})
    assert activated.status_code in (502, 503)

    async with db_override.factory() as session:
        assignment = await session.get(AccessAssignment, UUID(assignment_id))
        assert assignment.status == "ELIGIBLE"  # unchanged — a failed grant must not silently mark it active


@pytest.mark.asyncio
async def test_expiration_worker_does_not_expire_when_graph_revoke_fails(db_override, monkeypatch):
    ids = await _seed(db_override.factory, "MOCK")
    async with db_override.factory() as session:
        assignment = AccessAssignment(provider_id=ids["provider_id"], user_id=ids["user_id"], resource_type="GROUP", resource_id=ids["group_id"], assignment_type="TEMPORARY", status="ACTIVE", start_time=datetime.now(timezone.utc) - timedelta(hours=1), expiration_time=datetime.now(timezone.utc) - timedelta(minutes=1), activated_at=datetime.now(timezone.utc) - timedelta(hours=1))
        session.add(assignment)
        await session.commit()
        assignment_id = assignment.id

    async def failing_revoke(self, request):
        from app.providers.graph_client import GraphError
        raise GraphError("PROVIDER_UNAVAILABLE", "boom", 503)
    monkeypatch.setattr("app.providers.mock.MockProvider.revoke_assignment", failing_revoke)

    expired_count = await expire_due_assignments(db_override.factory)
    assert expired_count == 0

    async with db_override.factory() as session:
        still_active = await session.get(AccessAssignment, assignment_id)
        assert still_active.status == "ACTIVE"  # left alone to retry on the next poll, never falsely marked EXPIRED
