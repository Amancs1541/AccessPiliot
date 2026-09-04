from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import AccessAssignment, AccessPackage, AccessPackageItem, Group, IdentityProvider, User
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
        return AuthenticatedUser(subject, "Test User", "user@example.com", "tenant", (role,), {})
    app.dependency_overrides[require_authenticated_user] = dependency


async def _seed_directory(factory):
    async with factory() as session:
        provider = IdentityProvider(name="Directory", type="MOCK", status="CONNECTED", tenant_id="t")
        session.add(provider)
        await session.flush()
        target_user = User(provider_id=provider.id, external_id="target-user", email="target@x.com", display_name="Target User", status="ACTIVE")
        admin_user = User(provider_id=provider.id, external_id="admin-oid", email="admin@x.com", display_name="Admin User", status="ACTIVE")
        approver = User(provider_id=provider.id, external_id="approver-oid", email="approver@x.com", display_name="Approver User", status="ACTIVE")
        group_a = Group(provider_id=provider.id, external_id="ga", name="Security Team", status="ACTIVE", is_privileged=False)
        group_b = Group(provider_id=provider.id, external_id="gb", name="Finance Team", status="ACTIVE", is_privileged=False)
        session.add_all([target_user, admin_user, approver, group_a, group_b])
        await session.commit()
        return {"provider_id": provider.id, "user_id": target_user.id, "admin_id": admin_user.id, "approver_id": approver.id, "group_a_id": group_a.id, "group_b_id": group_b.id}


@pytest.mark.asyncio
async def test_admin_direct_assignment_notifies_the_target_user(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_a_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Direct grant."})
        assert created.status_code == 201

        authenticate_as("AccessPilot.User", subject="target-user")
        notifications = await client.get("/api/v1/notifications")
    assert notifications.status_code == 200
    matching = [n for n in notifications.json() if n["notification_type"] == "ASSIGNMENT_CREATED"]
    assert len(matching) == 1
    assert "Security Team" in matching[0]["message"]
    assert matching[0]["read_at"] is None


@pytest.mark.asyncio
async def test_self_service_package_request_does_not_notify_the_requester_of_their_own_action(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        package = await client.post("/api/v1/packages", json={"name": "Security Bundle", "items": [{"resource_type": "GROUP", "resource_id": str(ids["group_a_id"])}]})
        package_id = package.json()["id"]
        await client.put(f"/api/v1/packages/{package_id}/eligibility", json={"principals": [{"principal_type": "USER", "principal_id": str(ids["user_id"])}]})

        authenticate_as("AccessPilot.User", subject="target-user")
        requested = await client.post(f"/api/v1/packages/{package_id}/request", json={"assignment_type": "PERMANENT", "justification": "Requesting for myself."})
        assert requested.status_code == 201
        notifications = await client.get("/api/v1/notifications")
    assert notifications.status_code == 200
    assert all(n["notification_type"] != "ASSIGNMENT_CREATED" for n in notifications.json())


@pytest.mark.asyncio
async def test_pending_approval_notifies_the_approver(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_a_id"]), "assignment_type": "PERMANENT", "approver_id": str(ids["approver_id"]), "justification": "Needs approval."})
        assert created.status_code == 201
        assert created.json()["status"] == "PENDING_APPROVAL"

        authenticate_as("AccessPilot.User", subject="approver-oid")
        notifications = await client.get("/api/v1/notifications")
    assert notifications.status_code == 200
    matching = [n for n in notifications.json() if n["notification_type"] == "ASSIGNMENT_PENDING_APPROVAL"]
    assert len(matching) == 1
    assert "Target User" in matching[0]["message"]


@pytest.mark.asyncio
async def test_approving_notifies_the_target_user(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_a_id"]), "assignment_type": "PERMANENT", "approver_id": str(ids["approver_id"]), "justification": "Needs approval."})
        assignment_id = created.json()["id"]

        authenticate_as("AccessPilot.User", subject="approver-oid")
        approved = await client.post(f"/api/v1/assignments/{assignment_id}/approve", json={"justification": "Looks fine."})
        assert approved.status_code == 200

        authenticate_as("AccessPilot.User", subject="target-user")
        notifications = await client.get("/api/v1/notifications")
    assert notifications.status_code == 200
    matching = [n for n in notifications.json() if n["notification_type"] == "ASSIGNMENT_APPROVED"]
    assert len(matching) == 1


@pytest.mark.asyncio
async def test_rejecting_notifies_the_target_user(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_a_id"]), "assignment_type": "PERMANENT", "approver_id": str(ids["approver_id"]), "justification": "Needs approval."})
        assignment_id = created.json()["id"]

        authenticate_as("AccessPilot.User", subject="approver-oid")
        rejected = await client.post(f"/api/v1/assignments/{assignment_id}/reject")
        assert rejected.status_code == 200

        authenticate_as("AccessPilot.User", subject="target-user")
        notifications = await client.get("/api/v1/notifications")
    assert notifications.status_code == 200
    matching = [n for n in notifications.json() if n["notification_type"] == "ASSIGNMENT_REJECTED"]
    assert len(matching) == 1


@pytest.mark.asyncio
async def test_admin_activating_on_behalf_notifies_but_self_activation_does_not(db_override):
    ids = await _seed_directory(db_override.factory)
    async with db_override.factory() as session:
        eligible = AccessAssignment(provider_id=ids["provider_id"], user_id=ids["user_id"], resource_type="GROUP", resource_id=ids["group_a_id"], assignment_type="PERMANENT", status="ELIGIBLE")
        session.add(eligible)
        await session.commit()
        eligible_id = str(eligible.id)

    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        activated = await client.post(f"/api/v1/assignments/{eligible_id}/activate", json={"duration_hours": 1, "justification": "Activating on their behalf."})
        assert activated.status_code == 200

        authenticate_as("AccessPilot.User", subject="target-user")
        notifications = await client.get("/api/v1/notifications")
    assert notifications.status_code == 200
    matching = [n for n in notifications.json() if n["notification_type"] == "ASSIGNMENT_ACTIVATED"]
    assert len(matching) == 1


@pytest.mark.asyncio
async def test_self_activation_does_not_notify_yourself(db_override):
    ids = await _seed_directory(db_override.factory)
    async with db_override.factory() as session:
        eligible = AccessAssignment(provider_id=ids["provider_id"], user_id=ids["user_id"], resource_type="GROUP", resource_id=ids["group_a_id"], assignment_type="PERMANENT", status="ELIGIBLE")
        session.add(eligible)
        await session.commit()
        eligible_id = str(eligible.id)

    authenticate_as("AccessPilot.User", subject="target-user")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        activated = await client.post(f"/api/v1/assignments/{eligible_id}/activate", json={"duration_hours": 1, "justification": "Activating myself."})
        assert activated.status_code == 200
        notifications = await client.get("/api/v1/notifications")
    assert notifications.status_code == 200
    assert all(n["notification_type"] != "ASSIGNMENT_ACTIVATED" for n in notifications.json())


@pytest.mark.asyncio
async def test_admin_revoke_notifies_the_target_user(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_a_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Direct grant."})
        assignment_id = created.json()["id"]
        revoked = await client.post(f"/api/v1/assignments/{assignment_id}/revoke", json={"justification": "No longer needed."})
        assert revoked.status_code == 200

        authenticate_as("AccessPilot.User", subject="target-user")
        notifications = await client.get("/api/v1/notifications")
    assert notifications.status_code == 200
    matching = [n for n in notifications.json() if n["notification_type"] == "ASSIGNMENT_REVOKED"]
    assert len(matching) == 1


@pytest.mark.asyncio
async def test_mark_read_and_mark_all_read(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_a_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "First."})
        await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_b_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Second."})

        authenticate_as("AccessPilot.User", subject="target-user")
        notifications = await client.get("/api/v1/notifications")
        assert len(notifications.json()) == 2
        first_id = notifications.json()[0]["id"]

        marked = await client.post(f"/api/v1/notifications/{first_id}/read")
        assert marked.status_code == 204
        after_one = await client.get("/api/v1/notifications")
        unread_after_one = [n for n in after_one.json() if n["read_at"] is None]
        assert len(unread_after_one) == 1

        mark_all = await client.post("/api/v1/notifications/read-all")
        assert mark_all.status_code == 204
        after_all = await client.get("/api/v1/notifications")
    assert all(n["read_at"] is not None for n in after_all.json())


@pytest.mark.asyncio
async def test_notifications_are_scoped_to_the_caller(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_a_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "For target only."})

        authenticate_as("AccessPilot.User", subject="approver-oid")
        approver_view = await client.get("/api/v1/notifications")
        authenticate_as("AccessPilot.User", subject="target-user")
        target_view = await client.get("/api/v1/notifications")
    assert approver_view.json() == []
    assert len(target_view.json()) == 1
