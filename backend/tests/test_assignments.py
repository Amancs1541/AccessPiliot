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
async def test_create_assignment_without_approver_is_eligible_not_active(db_override):
    """Phase 5: no assignment ever grants real access at creation time — no-approver requests become ELIGIBLE
    and must be self-activated (or admin-activated) before any real Entra/Graph grant happens."""
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "justification": "Test justification."})
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "ELIGIBLE"
    assert body["activated_at"] is None
    assert body["user_display_name"] == "Target User"
    assert body["resource_display_name"] == "Security Team"


@pytest.mark.asyncio
async def test_create_assignment_with_future_start_time_is_eligible_with_future_start(db_override):
    """A future start_time on a no-approver request still lands ELIGIBLE — it just can't be activated until then."""
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    future_start = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "start_time": future_start, "justification": "Test justification."})
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "ELIGIBLE"
    assert body["activated_at"] is None

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        too_early = await client.post(f"/api/v1/assignments/{body['id']}/activate", json={"duration_hours": 2, "justification": "Test justification."})
    assert too_early.status_code == 409
    assert too_early.json()["error"]["code"] == "NOT_YET_ELIGIBLE"


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
async def test_approving_a_future_dated_assignment_is_eligible_not_active(db_override):
    """Approval only ever grants eligibility, never real access directly — a future start_time just means the
    resulting eligible assignment can't be self-activated until that time arrives (same NOT_YET_ELIGIBLE gate
    activate_assignment already enforces for the no-approver path)."""
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    future_start = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "approver_id": str(ids["approver_id"]), "start_time": future_start, "justification": "Test justification."})
        assignment_id = created.json()["id"]
        assert created.json()["status"] == "PENDING_APPROVAL"
        approved = await client.post(f"/api/v1/assignments/{assignment_id}/approve", json={"justification": "Test justification."})
        assert approved.status_code == 200
        assert approved.json()["status"] == "ELIGIBLE"
        assert approved.json()["activated_at"] is None

        too_early = await client.post(f"/api/v1/assignments/{assignment_id}/activate", json={"duration_hours": 2, "justification": "Test justification."})
    assert too_early.status_code == 409
    assert too_early.json()["error"]["code"] == "NOT_YET_ELIGIBLE"


@pytest.mark.asyncio
async def test_create_assignment_with_approver_is_pending(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "ROLE", "resource_id": str(ids["role_id"]), "assignment_type": "PERMANENT", "approver_id": str(ids["approver_id"]), "justification": "Test justification."})
    assert response.status_code == 201
    assert response.json()["status"] == "PENDING_APPROVAL"


@pytest.mark.asyncio
async def test_temporary_assignment_requires_expiration_time(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "TEMPORARY", "justification": "Test justification."})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_approve_pending_assignment_makes_it_eligible_then_activation_grants_real_access(db_override):
    """Phase 5: approval never grants real access directly anymore — it only lifts the request to ELIGIBLE. The
    target user still has to activate it themselves (or an Admin on their behalf) from My Access."""
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "approver_id": str(ids["approver_id"]), "justification": "Test justification."})
        assignment_id = created.json()["id"]
        approved = await client.post(f"/api/v1/assignments/{assignment_id}/approve", json={"justification": "Test justification."})
        assert approved.status_code == 200
        assert approved.json()["status"] == "ELIGIBLE"
        assert approved.json()["approved_by"] == str(ids["approver_id"])

        activated = await client.post(f"/api/v1/assignments/{assignment_id}/activate", json={"duration_hours": 2, "justification": "Test justification."})
    assert activated.status_code == 200
    assert activated.json()["status"] == "ACTIVE"


@pytest.mark.asyncio
async def test_reject_pending_assignment(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "approver_id": str(ids["approver_id"]), "justification": "Test justification."})
        assignment_id = created.json()["id"]
        rejected = await client.post(f"/api/v1/assignments/{assignment_id}/reject")
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "REJECTED"


@pytest.mark.asyncio
async def test_cannot_approve_already_decided_assignment(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "approver_id": str(ids["approver_id"]), "justification": "Test justification."})
        assignment_id = created.json()["id"]
        await client.post(f"/api/v1/assignments/{assignment_id}/approve", json={"justification": "Test justification."})
        second_attempt = await client.post(f"/api/v1/assignments/{assignment_id}/approve", json={"justification": "Test justification."})
    assert second_attempt.status_code == 409
    assert second_attempt.json()["error"]["code"] == "REQUEST_ALREADY_PROCESSED"


@pytest.mark.asyncio
async def test_designated_approver_who_is_a_normal_user_can_approve(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "approver_id": str(ids["approver_id"]), "justification": "Test justification."})
        assignment_id = created.json()["id"]

    # The approver is seeded with external_id "admin-oid" but is NOT an Admin here — a plain User.
    authenticate_as("AccessPilot.User", subject="admin-oid")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        approved = await client.post(f"/api/v1/assignments/{assignment_id}/approve", json={"justification": "Test justification."})
    assert approved.status_code == 200
    assert approved.json()["status"] == "ELIGIBLE"


@pytest.mark.asyncio
async def test_non_approver_normal_user_cannot_approve(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "approver_id": str(ids["approver_id"]), "justification": "Test justification."})
        assignment_id = created.json()["id"]

    # A different, unrelated user (not the designated approver, not an Admin) tries to approve.
    authenticate_as("AccessPilot.User", subject="someone-else-oid")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/api/v1/assignments/{assignment_id}/approve", json={"justification": "Test justification."})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ACCESS_DENIED"


@pytest.mark.asyncio
async def test_my_approvals_lists_only_assignments_where_caller_is_approver(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "approver_id": str(ids["approver_id"]), "justification": "Test justification."})
        await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "ROLE", "resource_id": str(ids["role_id"]), "assignment_type": "PERMANENT", "justification": "Test justification."})  # no approver

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
        created = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "approver_id": str(ids["approver_id"]), "justification": "Test justification."})
        assignment_id = created.json()["id"]

    # The approver's real Graph object id is "admin-oid" (matches the seeded user's external_id),
    # but their token's `sub` claim is a completely different, unrelated pairwise identifier.
    authenticate_with_pairwise_sub("AccessPilot.User", pairwise_sub="some-unrelated-pairwise-value", real_oid="admin-oid")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        mine = await client.get("/api/v1/assignments/pending-approval")
        approved = await client.post(f"/api/v1/assignments/{assignment_id}/approve", json={"justification": "Test justification."})
    assert len(mine.json()) == 1
    assert approved.status_code == 200
    assert approved.json()["status"] == "ELIGIBLE"


@pytest.mark.asyncio
async def test_create_assignment_denied_for_normal_user(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.User")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "justification": "Test justification."})
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_create_assignment_unknown_group_returns_404(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": "00000000-0000-0000-0000-000000000000", "assignment_type": "PERMANENT", "justification": "Test justification."})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "GROUP_NOT_FOUND"


@pytest.mark.asyncio
async def test_creating_two_eligible_assignments_to_same_target_does_not_supersede_until_activated(db_override):
    """Phase 5: superseding a same-target assignment is deferred to activation time (like the approval path already
    defers to approval time) — two merely-eligible assignments to the same target simply coexist."""
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    payload = {"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "justification": "Test justification."}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/api/v1/assignments", json=payload)
        first_id = first.json()["id"]
        second = await client.post("/api/v1/assignments", json=payload)
    assert first.json()["status"] == "ELIGIBLE"
    assert second.status_code == 201
    assert second.json()["status"] == "ELIGIBLE"
    assert second.json()["id"] != first_id

    async with db_override.factory() as session:
        old = await session.get(AccessAssignment, UUID(first_id))
        assert old.status == "ELIGIBLE"  # untouched — nothing real happened yet for either


@pytest.mark.asyncio
async def test_activating_an_eligible_assignment_supersedes_the_other_eligible_one_to_same_target(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    payload = {"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "justification": "Test justification."}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/api/v1/assignments", json=payload)
        first_id = first.json()["id"]
        second = await client.post("/api/v1/assignments", json=payload)
        second_id = second.json()["id"]
        activated = await client.post(f"/api/v1/assignments/{second_id}/activate", json={"duration_hours": 2, "justification": "Test justification."})
    assert activated.status_code == 200
    assert activated.json()["status"] == "ACTIVE"

    async with db_override.factory() as session:
        old = await session.get(AccessAssignment, UUID(first_id))
        assert old.status == "REVOKED"
        assert old.revoked_at is not None
        audit_actions = [row.action for row in (await session.execute(select(AuditLog))).scalars().all()]
        assert "ASSIGNMENT_REVOKED" in audit_actions


@pytest.mark.asyncio
async def test_pending_approval_request_does_not_touch_existing_access_until_approved(db_override):
    """Regression: creating a request that requires approval must NOT revoke the user's current REAL access —
    only an actual approval decision may replace it. A later rejection must leave the original untouched."""
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "justification": "Test justification."})
        first_id = first.json()["id"]
        await client.post(f"/api/v1/assignments/{first_id}/activate", json={"duration_hours": 2, "justification": "Test justification."})  # make it genuinely real, not just eligible
        pending = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "approver_id": str(ids["approver_id"]), "justification": "Test justification."})
        pending_id = pending.json()["id"]
    assert pending.json()["status"] == "PENDING_APPROVAL"

    async with db_override.factory() as session:
        still_active = await session.get(AccessAssignment, UUID(first_id))
        assert still_active.status == "ACTIVE"  # untouched while the new request is only pending

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        rejected = await client.post(f"/api/v1/assignments/{pending_id}/reject")
    assert rejected.json()["status"] == "REJECTED"

    async with db_override.factory() as session:
        still_active = await session.get(AccessAssignment, UUID(first_id))
        assert still_active.status == "ACTIVE"  # a rejection must never have touched the original


@pytest.mark.asyncio
async def test_approving_a_request_does_not_touch_existing_access_until_activated(db_override):
    """Approval alone must never touch the user's existing real access — only actually activating the newly
    approved (now eligible) request supersedes it, mirroring the no-approver activation path exactly."""
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "justification": "Test justification."})
        first_id = first.json()["id"]
        await client.post(f"/api/v1/assignments/{first_id}/activate", json={"duration_hours": 2, "justification": "Test justification."})
        pending = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "approver_id": str(ids["approver_id"]), "justification": "Test justification."})
        pending_id = pending.json()["id"]
        approved = await client.post(f"/api/v1/assignments/{pending_id}/approve", json={"justification": "Test justification."})
        assert approved.status_code == 200
        assert approved.json()["status"] == "ELIGIBLE"

        async with db_override.factory() as session:
            still_active = await session.get(AccessAssignment, UUID(first_id))
            assert still_active.status == "ACTIVE"  # untouched — the approved request is only eligible so far

        activated = await client.post(f"/api/v1/assignments/{pending_id}/activate", json={"duration_hours": 2, "justification": "Test justification."})
    assert activated.status_code == 200
    assert activated.json()["status"] == "ACTIVE"

    async with db_override.factory() as session:
        old = await session.get(AccessAssignment, UUID(first_id))
        assert old.status == "REVOKED"
        assert old.revoked_at is not None
        audit_actions = [row.action for row in (await session.execute(select(AuditLog))).scalars().all()]
        assert "ASSIGNMENT_REVOKED" in audit_actions


@pytest.mark.asyncio
async def test_assigning_a_different_resource_does_not_touch_unrelated_assignment(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        group_assignment = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "justification": "Test justification."})
        group_id = group_assignment.json()["id"]
        await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "ROLE", "resource_id": str(ids["role_id"]), "assignment_type": "PERMANENT", "justification": "Test justification."})

    async with db_override.factory() as session:
        still_eligible = await session.get(AccessAssignment, UUID(group_id))
        assert still_eligible.status == "ELIGIBLE"  # a different resource_type must not touch this one


@pytest.mark.asyncio
async def test_list_assignments_returns_created_ones(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "justification": "Test justification."})
        listed = await client.get("/api/v1/assignments")
    assert listed.status_code == 200
    assert len(listed.json()) == 1


@pytest.mark.asyncio
async def test_activate_eligible_assignment_grants_real_access_for_chosen_duration(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "justification": "Test justification."})
        assignment_id = created.json()["id"]
        activated = await client.post(f"/api/v1/assignments/{assignment_id}/activate", json={"duration_hours": 3, "justification": "Test justification."})
    assert activated.status_code == 200
    body = activated.json()
    assert body["status"] == "ACTIVE"
    assert body["activated_at"] is not None
    activated_at = datetime.fromisoformat(body["activated_at"].replace("Z", "+00:00"))
    expiration_time = datetime.fromisoformat(body["expiration_time"].replace("Z", "+00:00"))
    assert abs((expiration_time - activated_at) - timedelta(hours=3)) < timedelta(seconds=5)


@pytest.mark.asyncio
async def test_activate_rejects_duration_exceeding_provider_cap(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "justification": "Test justification."})
        assignment_id = created.json()["id"]
        rejected = await client.post(f"/api/v1/assignments/{assignment_id}/activate", json={"duration_hours": 9, "justification": "Test justification."})  # default cap is 8
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "DURATION_EXCEEDS_MAXIMUM"


@pytest.mark.asyncio
async def test_activate_denied_for_someone_other_than_the_assignment_user_or_admin(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "justification": "Test justification."})
        assignment_id = created.json()["id"]

    authenticate_as("AccessPilot.User", subject="someone-else-oid")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/api/v1/assignments/{assignment_id}/activate", json={"duration_hours": 2, "justification": "Test justification."})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ACCESS_DENIED"


@pytest.mark.asyncio
async def test_target_user_can_activate_their_own_eligible_assignment(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "justification": "Test justification."})
        assignment_id = created.json()["id"]

    authenticate_as("AccessPilot.User", subject="target-user")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        activated = await client.post(f"/api/v1/assignments/{assignment_id}/activate", json={"duration_hours": 2, "justification": "Test justification."})
    assert activated.status_code == 200
    assert activated.json()["status"] == "ACTIVE"


@pytest.mark.asyncio
async def test_cannot_activate_already_active_assignment(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "justification": "Test justification."})
        assignment_id = created.json()["id"]
        await client.post(f"/api/v1/assignments/{assignment_id}/activate", json={"duration_hours": 2, "justification": "Test justification."})
        second_attempt = await client.post(f"/api/v1/assignments/{assignment_id}/activate", json={"duration_hours": 2, "justification": "Test justification."})
    assert second_attempt.status_code == 409
    assert second_attempt.json()["error"]["code"] == "REQUEST_ALREADY_PROCESSED"


@pytest.mark.asyncio
async def test_list_my_assignments_returns_only_callers_own(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "justification": "Test justification."})

    authenticate_as("AccessPilot.User", subject="target-user")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        mine = await client.get("/api/v1/assignments/mine")
    assert mine.status_code == 200
    assert len(mine.json()) == 1
    assert mine.json()[0]["status"] == "ELIGIBLE"

    authenticate_as("AccessPilot.User", subject="admin-oid")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        theirs = await client.get("/api/v1/assignments/mine")
    assert theirs.json() == []  # the approver/admin user has no assignments of their own here


@pytest.mark.asyncio
async def test_activation_policy_reflects_admin_configured_cap(db_override):
    ids = await _seed_directory(db_override.factory)
    async with db_override.factory() as session:
        provider = await session.get(IdentityProvider, ids["provider_id"])
        provider.max_self_activation_hours = 24
        await session.commit()

    authenticate_as("AccessPilot.User", subject="target-user")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        policy = await client.get("/api/v1/assignments/activation-policy")
    assert policy.status_code == 200
    assert policy.json()["max_self_activation_hours"] == 24


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


@pytest.mark.asyncio
async def test_expiration_worker_expires_eligible_assignments_never_activated_by_deadline(db_override):
    """An ELIGIBLE (Temporary) assignment that's never activated by its expiration_time deadline must be swept to
    EXPIRED with no provider call — nothing was ever granted, so there's nothing to revoke."""
    from app.workers.expiration import expire_due_eligibility

    ids = await _seed_directory(db_override.factory)
    async with db_override.factory() as session:
        missed = AccessAssignment(provider_id=ids["provider_id"], user_id=ids["user_id"], resource_type="GROUP", resource_id=ids["group_id"], assignment_type="TEMPORARY", status="ELIGIBLE", expiration_time=datetime.now(timezone.utc) - timedelta(minutes=1))
        still_open = AccessAssignment(provider_id=ids["provider_id"], user_id=ids["user_id"], resource_type="ROLE", resource_id=ids["role_id"], assignment_type="TEMPORARY", status="ELIGIBLE", expiration_time=datetime.now(timezone.utc) + timedelta(hours=2))
        session.add_all([missed, still_open])
        await session.commit()
        missed_id, still_open_id = missed.id, still_open.id

    expired_count = await expire_due_eligibility(db_override.factory)
    assert expired_count == 1

    async with db_override.factory() as session:
        expired = await session.get(AccessAssignment, missed_id)
        open_one = await session.get(AccessAssignment, still_open_id)
        assert expired.status == "EXPIRED"
        assert open_one.status == "ELIGIBLE"


@pytest.mark.asyncio
async def test_target_user_can_deactivate_their_own_active_assignment(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "justification": "Test justification."})
        assignment_id = created.json()["id"]
        await client.post(f"/api/v1/assignments/{assignment_id}/activate", json={"duration_hours": 2, "justification": "Test justification."})

    authenticate_as("AccessPilot.User", subject="target-user")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        deactivated = await client.post(f"/api/v1/assignments/{assignment_id}/deactivate")
    assert deactivated.status_code == 200
    body = deactivated.json()
    assert body["status"] == "ELIGIBLE"
    assert body["expiration_time"] is None

    async with db_override.factory() as session:
        row = await session.get(AccessAssignment, UUID(assignment_id))
        assert row.activated_at is None
        audit_actions = {r.action for r in (await session.execute(select(AuditLog))).scalars().all()}
        assert "ASSIGNMENT_DEACTIVATED" in audit_actions


@pytest.mark.asyncio
async def test_deactivated_assignment_can_be_activated_again(db_override):
    """Reactivating a deactivated assignment needs no new request/approval — it just goes through /activate again."""
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "justification": "Test justification."})
        assignment_id = created.json()["id"]
        await client.post(f"/api/v1/assignments/{assignment_id}/activate", json={"duration_hours": 2, "justification": "Test justification."})
        await client.post(f"/api/v1/assignments/{assignment_id}/deactivate")
        reactivated = await client.post(f"/api/v1/assignments/{assignment_id}/activate", json={"duration_hours": 3, "justification": "Test justification."})
    assert reactivated.status_code == 200
    assert reactivated.json()["status"] == "ACTIVE"


@pytest.mark.asyncio
async def test_cannot_deactivate_an_assignment_that_is_not_active(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "justification": "Test justification."})
        assignment_id = created.json()["id"]
        response = await client.post(f"/api/v1/assignments/{assignment_id}/deactivate")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "REQUEST_ALREADY_PROCESSED"


@pytest.mark.asyncio
async def test_deactivate_is_denied_to_someone_other_than_the_target_user_or_admin(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "justification": "Test justification."})
        assignment_id = created.json()["id"]
        await client.post(f"/api/v1/assignments/{assignment_id}/activate", json={"duration_hours": 2, "justification": "Test justification."})

    authenticate_as("AccessPilot.User", subject="someone-else-oid")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/api/v1/assignments/{assignment_id}/deactivate")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ACCESS_DENIED"


@pytest.mark.asyncio
async def test_create_assignment_requires_a_real_justification(db_override):
    """A missing, empty, or whitespace-only justification must never let an assignment be created."""
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    base = {"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        missing = await client.post("/api/v1/assignments", json=base)
        blank = await client.post("/api/v1/assignments", json={**base, "justification": "   "})
        too_short = await client.post("/api/v1/assignments", json={**base, "justification": "ok"})
    assert missing.status_code == 422
    assert blank.status_code == 422
    assert too_short.status_code == 422

    async with db_override.factory() as session:
        from sqlalchemy import select
        remaining = (await session.execute(select(AccessAssignment))).scalars().all()
        assert len(remaining) == 0  # nothing was created for any of the rejected requests


@pytest.mark.asyncio
async def test_approve_requires_a_real_justification(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "approver_id": str(ids["approver_id"]), "justification": "Test justification."})
        assignment_id = created.json()["id"]
        missing = await client.post(f"/api/v1/assignments/{assignment_id}/approve")
        blank = await client.post(f"/api/v1/assignments/{assignment_id}/approve", json={"justification": "  "})
    assert missing.status_code == 422
    assert blank.status_code == 422

    async with db_override.factory() as session:
        still_pending = await session.get(AccessAssignment, UUID(assignment_id))
        assert still_pending.status == "PENDING_APPROVAL"


@pytest.mark.asyncio
async def test_activate_requires_a_real_justification(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "justification": "Test justification."})
        assignment_id = created.json()["id"]
        missing = await client.post(f"/api/v1/assignments/{assignment_id}/activate", json={"duration_hours": 2})
        blank = await client.post(f"/api/v1/assignments/{assignment_id}/activate", json={"duration_hours": 2, "justification": "   "})
    assert missing.status_code == 422
    assert blank.status_code == 422

    async with db_override.factory() as session:
        still_eligible = await session.get(AccessAssignment, UUID(assignment_id))
        assert still_eligible.status == "ELIGIBLE"


@pytest.mark.asyncio
async def test_bypass_activation_grants_real_access_immediately(db_override):
    """The Admin-only 'assign immediately' checkbox skips the eligible/activate step entirely."""
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Direct grant for onboarding."})
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "ACTIVE"
    assert body["bypass_activation"] is True
    assert body["activated_at"] is not None

    async with db_override.factory() as session:
        audit_actions = {row.action for row in (await session.execute(select(AuditLog))).scalars().all()}
        assert "ASSIGNMENT_ACTIVATED" in audit_actions  # so it still counts toward the privileged-role timeline


@pytest.mark.asyncio
async def test_bypass_activation_rejects_combination_with_approver(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "approver_id": str(ids["approver_id"]), "justification": "Conflicting request."})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_bypass_activation_cannot_be_deactivated_by_the_end_user(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Direct grant."})
        assignment_id = created.json()["id"]

    authenticate_as("AccessPilot.User", subject="target-user")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/api/v1/assignments/{assignment_id}/deactivate")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ACCESS_DENIED"

    async with db_override.factory() as session:
        still_active = await session.get(AccessAssignment, UUID(assignment_id))
        assert still_active.status == "ACTIVE"


@pytest.mark.asyncio
async def test_admin_can_still_deactivate_a_bypass_activation_assignment(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Direct grant."})
        assignment_id = created.json()["id"]
        deactivated = await client.post(f"/api/v1/assignments/{assignment_id}/deactivate")
    assert deactivated.status_code == 200
    assert deactivated.json()["status"] == "ELIGIBLE"
    assert deactivated.json()["bypass_activation"] is False


@pytest.mark.asyncio
async def test_bypass_activation_with_future_start_time_schedules_it(db_override):
    from app.workers.activation import activate_due_assignments

    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    future_start = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "start_time": future_start, "justification": "Direct grant, starts later."})
    assert created.status_code == 201
    assignment_id = created.json()["id"]
    assert created.json()["status"] == "SCHEDULED"

    async with db_override.factory() as session:
        assignment = await session.get(AccessAssignment, UUID(assignment_id))
        assignment.start_time = datetime.now(timezone.utc) - timedelta(minutes=1)
        await session.commit()

    activated_count = await activate_due_assignments(db_override.factory)
    assert activated_count == 1
    async with db_override.factory() as session:
        assignment = await session.get(AccessAssignment, UUID(assignment_id))
        assert assignment.status == "ACTIVE"


@pytest.mark.asyncio
async def test_bypass_activation_supersedes_existing_active_assignment_immediately(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    payload = {"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "justification": "Test justification."}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/api/v1/assignments", json=payload)
        first_id = first.json()["id"]
        await client.post(f"/api/v1/assignments/{first_id}/activate", json={"duration_hours": 2, "justification": "Test justification."})

        second = await client.post("/api/v1/assignments", json={**payload, "bypass_activation": True})
    assert second.status_code == 201
    assert second.json()["status"] == "ACTIVE"

    async with db_override.factory() as session:
        old = await session.get(AccessAssignment, UUID(first_id))
        assert old.status == "REVOKED"


@pytest.mark.asyncio
async def test_bypass_activation_does_not_persist_when_grant_fails(db_override, monkeypatch):
    async with db_override.factory() as session:
        provider = IdentityProvider(name="Entra", type="ENTRA", status="CONNECTED", tenant_id="t")
        session.add(provider)
        await session.flush()
        target_user = User(provider_id=provider.id, external_id="target-user", email="target@x.com", display_name="Target User", status="ACTIVE")
        group = Group(provider_id=provider.id, external_id="g1", name="Security Team", status="ACTIVE", is_privileged=False)
        session.add_all([target_user, group])
        await session.commit()
        ids = {"user_id": target_user.id, "group_id": group.id}
    monkeypatch.delenv("ENTRA_API_CLIENT_SECRET", raising=False)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Direct grant."})
    assert response.status_code in (502, 503)

    async with db_override.factory() as session:
        remaining = (await session.execute(select(AccessAssignment))).scalars().all()
        assert len(remaining) == 0


@pytest.mark.asyncio
async def test_admin_can_revoke_an_active_assignment(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "justification": "Need it."})
        assignment_id = created.json()["id"]
        await client.post(f"/api/v1/assignments/{assignment_id}/activate", json={"duration_hours": 2, "justification": "Activating."})
        revoked = await client.post(f"/api/v1/assignments/{assignment_id}/revoke", json={"justification": "No longer needed."})
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "REVOKED"

    async with db_override.factory() as session:
        row = await session.get(AccessAssignment, UUID(assignment_id))
        assert row.revoked_at is not None
        audit_actions = [r.action for r in (await session.execute(select(AuditLog))).scalars().all()]
        assert "ASSIGNMENT_REVOKED" in audit_actions


@pytest.mark.asyncio
async def test_admin_can_revoke_an_eligible_assignment_with_no_real_access_yet(db_override):
    """Revoking an ELIGIBLE assignment (never activated, nothing granted) needs no provider call — just marks it REVOKED."""
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "justification": "Need it."})
        assignment_id = created.json()["id"]
        assert created.json()["status"] == "ELIGIBLE"
        revoked = await client.post(f"/api/v1/assignments/{assignment_id}/revoke", json={"justification": "Not appropriate for this user."})
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "REVOKED"


@pytest.mark.asyncio
async def test_revoke_requires_a_real_justification(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "justification": "Need it."})
        assignment_id = created.json()["id"]
        missing = await client.post(f"/api/v1/assignments/{assignment_id}/revoke")
        blank = await client.post(f"/api/v1/assignments/{assignment_id}/revoke", json={"justification": "  "})
    assert missing.status_code == 422
    assert blank.status_code == 422

    async with db_override.factory() as session:
        still_eligible = await session.get(AccessAssignment, UUID(assignment_id))
        assert still_eligible.status == "ELIGIBLE"


@pytest.mark.asyncio
async def test_revoke_denied_to_a_normal_user(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "justification": "Need it."})
        assignment_id = created.json()["id"]

    authenticate_as("AccessPilot.User", subject="target-user")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/api/v1/assignments/{assignment_id}/revoke", json={"justification": "Trying anyway."})
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_cannot_revoke_an_assignment_already_in_a_final_state(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "justification": "Need it."})
        assignment_id = created.json()["id"]
        await client.post(f"/api/v1/assignments/{assignment_id}/revoke", json={"justification": "First revoke."})
        second_attempt = await client.post(f"/api/v1/assignments/{assignment_id}/revoke", json={"justification": "Second revoke."})
    assert second_attempt.status_code == 409
    assert second_attempt.json()["error"]["code"] == "REQUEST_ALREADY_PROCESSED"
