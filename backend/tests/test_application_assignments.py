from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import AccessAssignment, Application, AuditLog, IdentityProvider, User
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


def authenticate_as(role: str, subject: str = "admin-oid") -> None:
    async def dependency():
        return AuthenticatedUser(subject, "Admin", "admin@example.com", "tenant", (role,), {})
    app.dependency_overrides[require_authenticated_user] = dependency


async def _seed_directory(factory):
    async with factory() as session:
        provider = IdentityProvider(name="Directory", type="MOCK", status="CONNECTED", tenant_id="t")
        session.add(provider)
        await session.flush()
        target_user = User(provider_id=provider.id, external_id="target-user", email="target@x.com", display_name="Target User", status="ACTIVE")
        approver = User(provider_id=provider.id, external_id="admin-oid", email="admin@x.com", display_name="Admin User", status="ACTIVE")
        application = Application(
            provider_id=provider.id,
            external_id="app-001",
            name="Reporting Portal",
            status="ACTIVE",
            app_roles=[{"id": "approle-001", "name": "Viewer", "description": "Read-only"}, {"id": "approle-002", "name": "Editor", "description": "Read-write"}],
        )
        session.add_all([target_user, approver, application])
        await session.commit()
        return {"provider_id": provider.id, "user_id": target_user.id, "approver_id": approver.id, "application_id": application.id}


@pytest.mark.asyncio
async def test_create_application_assignment_is_eligible_then_activation_grants_real_app_role(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "APPLICATION", "resource_id": str(ids["application_id"]), "app_role_external_id": "approle-002", "assignment_type": "PERMANENT"})
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "ELIGIBLE"
        assert body["app_role_external_id"] == "approle-002"
        assert body["resource_display_name"] == "Reporting Portal — Editor"

        activated = await client.post(f"/api/v1/assignments/{body['id']}/activate", json={"duration_hours": 2})
    assert activated.status_code == 200
    assert activated.json()["status"] == "ACTIVE"

    async with db_override.factory() as session:
        audit_actions = {row.action for row in (await session.execute(select(AuditLog))).scalars().all()}
    assert "ASSIGNMENT_ACTIVATED" in audit_actions


@pytest.mark.asyncio
async def test_application_assignment_requires_app_role_external_id(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "APPLICATION", "resource_id": str(ids["application_id"]), "assignment_type": "PERMANENT"})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_application_assignment_unknown_role_returns_404(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "APPLICATION", "resource_id": str(ids["application_id"]), "app_role_external_id": "does-not-exist", "assignment_type": "PERMANENT"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "APPLICATION_ROLE_NOT_FOUND"


@pytest.mark.asyncio
async def test_application_assignment_unknown_application_returns_404(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "APPLICATION", "resource_id": "00000000-0000-0000-0000-000000000000", "app_role_external_id": "approle-001", "assignment_type": "PERMANENT"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "APPLICATION_NOT_FOUND"


@pytest.mark.asyncio
async def test_activating_same_application_and_role_supersedes_the_other_eligible_one(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    payload = {"user_id": str(ids["user_id"]), "resource_type": "APPLICATION", "resource_id": str(ids["application_id"]), "app_role_external_id": "approle-001", "assignment_type": "PERMANENT"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/api/v1/assignments", json=payload)
        first_id = first.json()["id"]
        second = await client.post("/api/v1/assignments", json=payload)
        second_id = second.json()["id"]
        assert second.status_code == 201
        assert second.json()["status"] == "ELIGIBLE"
        activated = await client.post(f"/api/v1/assignments/{second_id}/activate", json={"duration_hours": 2})
    assert activated.status_code == 200
    assert activated.json()["status"] == "ACTIVE"

    async with db_override.factory() as session:
        old = await session.get(AccessAssignment, UUID(first_id))
        assert old.status == "REVOKED"
        audit_actions = [row.action for row in (await session.execute(select(AuditLog))).scalars().all()]
        assert "ASSIGNMENT_REVOKED" in audit_actions


@pytest.mark.asyncio
async def test_different_role_on_same_application_does_not_supersede(db_override):
    """A different app role on the same application is a distinct grant, not a replacement of the existing one."""
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        viewer = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "APPLICATION", "resource_id": str(ids["application_id"]), "app_role_external_id": "approle-001", "assignment_type": "PERMANENT"})
        viewer_id = viewer.json()["id"]
        await client.post(f"/api/v1/assignments/{viewer_id}/activate", json={"duration_hours": 2})
        editor = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "APPLICATION", "resource_id": str(ids["application_id"]), "app_role_external_id": "approle-002", "assignment_type": "PERMANENT"})
        editor_id = editor.json()["id"]
        await client.post(f"/api/v1/assignments/{editor_id}/activate", json={"duration_hours": 2})
    assert editor.status_code == 201

    async with db_override.factory() as session:
        still_active = await session.get(AccessAssignment, UUID(viewer_id))
        assert still_active.status == "ACTIVE"  # unaffected by the second, different-role assignment


@pytest.mark.asyncio
async def test_pending_application_approval_does_not_touch_existing_role_until_approved(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "APPLICATION", "resource_id": str(ids["application_id"]), "app_role_external_id": "approle-001", "assignment_type": "PERMANENT"})
        first_id = first.json()["id"]
        await client.post(f"/api/v1/assignments/{first_id}/activate", json={"duration_hours": 2})  # make it genuinely real
        pending = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "APPLICATION", "resource_id": str(ids["application_id"]), "app_role_external_id": "approle-001", "assignment_type": "PERMANENT", "approver_id": str(ids["approver_id"])})
    assert pending.json()["status"] == "PENDING_APPROVAL"

    async with db_override.factory() as session:
        still_active = await session.get(AccessAssignment, UUID(first_id))
        assert still_active.status == "ACTIVE"  # untouched while the new request is only pending


@pytest.mark.asyncio
async def test_activating_an_approved_application_request_supersedes_the_existing_role_assignment(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "APPLICATION", "resource_id": str(ids["application_id"]), "app_role_external_id": "approle-001", "assignment_type": "PERMANENT"})
        first_id = first.json()["id"]
        await client.post(f"/api/v1/assignments/{first_id}/activate", json={"duration_hours": 2})
        pending = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "APPLICATION", "resource_id": str(ids["application_id"]), "app_role_external_id": "approle-001", "assignment_type": "PERMANENT", "approver_id": str(ids["approver_id"])})
        pending_id = pending.json()["id"]
        approved = await client.post(f"/api/v1/assignments/{pending_id}/approve")
        assert approved.status_code == 200
        assert approved.json()["status"] == "ELIGIBLE"
        activated = await client.post(f"/api/v1/assignments/{pending_id}/activate", json={"duration_hours": 2})
    assert activated.status_code == 200
    assert activated.json()["status"] == "ACTIVE"

    async with db_override.factory() as session:
        old = await session.get(AccessAssignment, UUID(first_id))
        assert old.status == "REVOKED"
        audit_actions = [row.action for row in (await session.execute(select(AuditLog))).scalars().all()]
        assert "ASSIGNMENT_REVOKED" in audit_actions


@pytest.mark.asyncio
async def test_expiring_a_temporary_application_assignment_revokes_real_app_role(db_override):
    ids = await _seed_directory(db_override.factory)
    async with db_override.factory() as session:
        assignment = AccessAssignment(
            provider_id=ids["provider_id"], user_id=ids["user_id"], resource_type="APPLICATION", resource_id=ids["application_id"], app_role_external_id="approle-001",
            assignment_type="TEMPORARY", status="ACTIVE",
            start_time=datetime.now(timezone.utc) - timedelta(hours=2), expiration_time=datetime.now(timezone.utc) - timedelta(minutes=1), activated_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )
        session.add(assignment)
        await session.commit()
        assignment_id = assignment.id

    expired_count = await expire_due_assignments(db_override.factory)
    assert expired_count == 1

    async with db_override.factory() as session:
        expired = await session.get(AccessAssignment, assignment_id)
        assert expired.status == "EXPIRED"
        audit_actions = {row.action for row in (await session.execute(select(AuditLog))).scalars().all()}
        assert "ASSIGNMENT_EXPIRED" in audit_actions
