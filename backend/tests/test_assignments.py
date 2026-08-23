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
from app.models import AccessAssignment, AuditLog, Group, IdentityProvider, Role, User
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


def as_role(role: str, subject: str = "admin-oid"):
    async def dependency():
        return AuthenticatedUser(subject, "Admin", "admin@example.com", "tenant", (role,), {})
    return dependency


def authenticate_as(role: str, subject: str = "admin-oid") -> None:
    app.dependency_overrides[require_authenticated_user] = as_role(role, subject)


def authenticate_with_pairwise_sub(role: str, *, pairwise_sub: str, real_oid: str) -> None:
    """Simulates a real Entra token: `sub` is a per-app pairwise ID, distinct from the `oid` claim used in directory sync."""
    async def dependency():
        return AuthenticatedUser(pairwise_sub, "Real User", "real@example.com", "tenant", (role,), {"oid": real_oid})
    app.dependency_overrides[require_authenticated_user] = dependency


async def _seed_directory(factory):
    async with factory() as session:
        provider = IdentityProvider(name="Directory", type="MOCK", status="CONNECTED", tenant_id="t")
        session.add(provider)
        await session.flush()
        target_user = User(provider_id=provider.id, external_id="target-user", email="target@x.com", display_name="Target User", status="ACTIVE")
        approver = User(provider_id=provider.id, external_id="admin-oid", email="admin@x.com", display_name="Admin User", status="ACTIVE")
        group = Group(provider_id=provider.id, external_id="g1", name="Security Team", status="ACTIVE", is_privileged=False)
        role = Role(provider_id=provider.id, external_id="r1", name="Reports Reader", role_type="DIRECTORY_ROLE", status="ACTIVE", is_privileged=False)
        session.add_all([target_user, approver, group, role])
        await session.commit()
        return {"provider_id": provider.id, "user_id": target_user.id, "approver_id": approver.id, "group_id": group.id, "role_id": role.id}


@pytest.mark.asyncio
async def test_create_assignment_without_approver_is_immediately_active(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT"})
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "ACTIVE"
    assert body["user_display_name"] == "Target User"
    assert body["resource_display_name"] == "Security Team"


@pytest.mark.asyncio
async def test_create_assignment_with_future_start_time_is_scheduled_not_active(db_override):
    """A future start_time must NOT grant access immediately — it should wait for the activation worker."""
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    future_start = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "start_time": future_start})
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "SCHEDULED"
    assert body["activated_at"] is None


@pytest.mark.asyncio
async def test_activation_worker_grants_access_once_start_time_arrives(db_override, monkeypatch):
    from app.workers.activation import activate_due_assignments

    ids = await _seed_directory(db_override.factory)
    async with db_override.factory() as session:
        due = AccessAssignment(provider_id=ids["provider_id"], user_id=ids["user_id"], resource_type="GROUP", resource_id=ids["group_id"], assignment_type="PERMANENT", status="SCHEDULED", start_time=datetime.now(timezone.utc) - timedelta(minutes=1))
        not_due = AccessAssignment(provider_id=ids["provider_id"], user_id=ids["user_id"], resource_type="ROLE", resource_id=ids["role_id"], assignment_type="PERMANENT", status="SCHEDULED", start_time=datetime.now(timezone.utc) + timedelta(hours=1))
        session.add_all([due, not_due])
        await session.commit()
        due_id, not_due_id = due.id, not_due.id

    activated_count = await activate_due_assignments(db_override.factory)
    assert activated_count == 1

    async with db_override.factory() as session:
        activated = await session.get(AccessAssignment, due_id)
        still_scheduled = await session.get(AccessAssignment, not_due_id)
        assert activated.status == "ACTIVE" and activated.activated_at is not None
        assert still_scheduled.status == "SCHEDULED"
        audit_actions = {row.action for row in (await session.execute(select(AuditLog))).scalars().all()}
        assert "ASSIGNMENT_ACTIVATED" in audit_actions


@pytest.mark.asyncio
async def test_activation_worker_does_not_activate_when_graph_grant_fails(db_override, monkeypatch):
    from app.workers.activation import activate_due_assignments

    ids = await _seed_directory(db_override.factory)
    async with db_override.factory() as session:
        assignment = AccessAssignment(provider_id=ids["provider_id"], user_id=ids["user_id"], resource_type="GROUP", resource_id=ids["group_id"], assignment_type="PERMANENT", status="SCHEDULED", start_time=datetime.now(timezone.utc) - timedelta(minutes=1))
        session.add(assignment)
        await session.commit()
        assignment_id = assignment.id

    async def failing_activate(self, request):
        from app.providers.graph_client import GraphError
        raise GraphError("PROVIDER_UNAVAILABLE", "boom", 503)
    monkeypatch.setattr("app.providers.mock.MockProvider.activate_assignment", failing_activate)

    activated_count = await activate_due_assignments(db_override.factory)
    assert activated_count == 0

    async with db_override.factory() as session:
        still_scheduled = await session.get(AccessAssignment, assignment_id)
        assert still_scheduled.status == "SCHEDULED"  # left alone to retry, never falsely marked ACTIVE


@pytest.mark.asyncio
async def test_approving_a_future_dated_assignment_schedules_it_instead_of_activating(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    future_start = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "approver_id": str(ids["approver_id"]), "start_time": future_start})
        assignment_id = created.json()["id"]
        assert created.json()["status"] == "PENDING_APPROVAL"
        approved = await client.post(f"/api/v1/assignments/{assignment_id}/approve")
    assert approved.status_code == 200
    assert approved.json()["status"] == "SCHEDULED"
    assert approved.json()["activated_at"] is None


@pytest.mark.asyncio
async def test_create_assignment_with_approver_is_pending(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "ROLE", "resource_id": str(ids["role_id"]), "assignment_type": "PERMANENT", "approver_id": str(ids["approver_id"])})
    assert response.status_code == 201
    assert response.json()["status"] == "PENDING_APPROVAL"


@pytest.mark.asyncio
async def test_temporary_assignment_requires_expiration_time(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "TEMPORARY"})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_approve_pending_assignment_activates_it(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "approver_id": str(ids["approver_id"])})
        assignment_id = created.json()["id"]
        approved = await client.post(f"/api/v1/assignments/{assignment_id}/approve")
    assert approved.status_code == 200
    assert approved.json()["status"] == "ACTIVE"
    assert approved.json()["approved_by"] == str(ids["approver_id"])


@pytest.mark.asyncio
async def test_reject_pending_assignment(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "approver_id": str(ids["approver_id"])})
        assignment_id = created.json()["id"]
        rejected = await client.post(f"/api/v1/assignments/{assignment_id}/reject")
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "REJECTED"


@pytest.mark.asyncio
async def test_cannot_approve_already_decided_assignment(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "approver_id": str(ids["approver_id"])})
        assignment_id = created.json()["id"]
        await client.post(f"/api/v1/assignments/{assignment_id}/approve")
        second_attempt = await client.post(f"/api/v1/assignments/{assignment_id}/approve")
    assert second_attempt.status_code == 409
    assert second_attempt.json()["error"]["code"] == "REQUEST_ALREADY_PROCESSED"


@pytest.mark.asyncio
async def test_designated_approver_who_is_a_normal_user_can_approve(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "approver_id": str(ids["approver_id"])})
        assignment_id = created.json()["id"]

    # The approver is seeded with external_id "admin-oid" but is NOT an Admin here — a plain User.
    authenticate_as("AccessPilot.User", subject="admin-oid")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        approved = await client.post(f"/api/v1/assignments/{assignment_id}/approve")
    assert approved.status_code == 200
    assert approved.json()["status"] == "ACTIVE"


@pytest.mark.asyncio
async def test_non_approver_normal_user_cannot_approve(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "approver_id": str(ids["approver_id"])})
        assignment_id = created.json()["id"]

    # A different, unrelated user (not the designated approver, not an Admin) tries to approve.
    authenticate_as("AccessPilot.User", subject="someone-else-oid")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/api/v1/assignments/{assignment_id}/approve")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ACCESS_DENIED"


@pytest.mark.asyncio
async def test_my_approvals_lists_only_assignments_where_caller_is_approver(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "approver_id": str(ids["approver_id"])})
        await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "ROLE", "resource_id": str(ids["role_id"]), "assignment_type": "PERMANENT"})  # no approver

    authenticate_as("AccessPilot.User", subject="admin-oid")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        mine = await client.get("/api/v1/assignments/pending-approval")
    assert mine.status_code == 200
    assert len(mine.json()) == 1
    assert mine.json()[0]["resource_type"] == "GROUP"


@pytest.mark.asyncio
async def test_approver_is_recognized_via_oid_not_pairwise_sub(db_override):
    """Regression test: Entra's `sub` claim is a per-app pairwise ID, NOT the Graph object ID stored as users.external_id.
    The approver must be matched via the `oid` claim, or a real user's own approvals would never be found."""
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "approver_id": str(ids["approver_id"])})
        assignment_id = created.json()["id"]

    # The approver's real Graph object id is "admin-oid" (matches the seeded user's external_id),
    # but their token's `sub` claim is a completely different, unrelated pairwise identifier.
    authenticate_with_pairwise_sub("AccessPilot.User", pairwise_sub="some-unrelated-pairwise-value", real_oid="admin-oid")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        mine = await client.get("/api/v1/assignments/pending-approval")
        approved = await client.post(f"/api/v1/assignments/{assignment_id}/approve")
    assert len(mine.json()) == 1
    assert approved.status_code == 200
    assert approved.json()["status"] == "ACTIVE"


@pytest.mark.asyncio
async def test_create_assignment_denied_for_normal_user(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.User")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT"})
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_create_assignment_unknown_group_returns_404(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": "00000000-0000-0000-0000-000000000000", "assignment_type": "PERMANENT"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "GROUP_NOT_FOUND"


@pytest.mark.asyncio
async def test_reassigning_same_group_revokes_the_old_active_one_first(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    payload = {"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/api/v1/assignments", json=payload)
        first_id = first.json()["id"]
        second = await client.post("/api/v1/assignments", json=payload)
    assert first.json()["status"] == "ACTIVE"
    assert second.status_code == 201
    assert second.json()["status"] == "ACTIVE"
    assert second.json()["id"] != first_id

    async with db_override.factory() as session:
        old = await session.get(AccessAssignment, UUID(first_id))
        assert old.status == "REVOKED"
        assert old.revoked_at is not None
        audit_actions = [row.action for row in (await session.execute(select(AuditLog))).scalars().all()]
        assert "ASSIGNMENT_REVOKED" in audit_actions


@pytest.mark.asyncio
async def test_reassigning_same_group_removes_stale_scheduled_one_without_graph_call(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    future_start = (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        scheduled = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "start_time": future_start})
        scheduled_id = scheduled.json()["id"]
        assert scheduled.json()["status"] == "SCHEDULED"
        replacement = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT"})
    assert replacement.status_code == 201
    assert replacement.json()["status"] == "ACTIVE"

    async with db_override.factory() as session:
        old = await session.get(AccessAssignment, UUID(scheduled_id))
        assert old.status == "REVOKED"


@pytest.mark.asyncio
async def test_assigning_a_different_resource_does_not_touch_unrelated_assignment(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        group_assignment = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT"})
        group_id = group_assignment.json()["id"]
        await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "ROLE", "resource_id": str(ids["role_id"]), "assignment_type": "PERMANENT"})

    async with db_override.factory() as session:
        still_active = await session.get(AccessAssignment, UUID(group_id))
        assert still_active.status == "ACTIVE"  # a different resource_type must not revoke this one


@pytest.mark.asyncio
async def test_list_assignments_returns_created_ones(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT"})
        listed = await client.get("/api/v1/assignments")
    assert listed.status_code == 200
    assert len(listed.json()) == 1


@pytest.mark.asyncio
async def test_expiration_worker_expires_due_temporary_assignments(db_override):
    ids = await _seed_directory(db_override.factory)
    async with db_override.factory() as session:
        assignment = AccessAssignment(provider_id=ids["provider_id"], user_id=ids["user_id"], resource_type="GROUP", resource_id=ids["group_id"], assignment_type="TEMPORARY", status="ACTIVE", start_time=datetime.now(timezone.utc) - timedelta(hours=2), expiration_time=datetime.now(timezone.utc) - timedelta(minutes=1), activated_at=datetime.now(timezone.utc) - timedelta(hours=2))
        not_due = AccessAssignment(provider_id=ids["provider_id"], user_id=ids["user_id"], resource_type="ROLE", resource_id=ids["role_id"], assignment_type="TEMPORARY", status="ACTIVE", start_time=datetime.now(timezone.utc), expiration_time=datetime.now(timezone.utc) + timedelta(hours=2))
        session.add_all([assignment, not_due])
        await session.commit()
        expired_id, active_id = assignment.id, not_due.id

    expired_count = await expire_due_assignments(db_override.factory)
    assert expired_count == 1

    async with db_override.factory() as session:
        expired = await session.get(AccessAssignment, expired_id)
        still_active = await session.get(AccessAssignment, active_id)
        assert expired.status == "EXPIRED"
        assert still_active.status == "ACTIVE"
        audit_actions = {row.action for row in (await session.execute(select(AuditLog))).scalars().all()}
        assert "ASSIGNMENT_EXPIRED" in audit_actions
