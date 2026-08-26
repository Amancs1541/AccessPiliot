from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import AccessAssignment, Group, IdentityProvider, Role, User
from app.security.auth import AuthenticatedUser, require_authenticated_user
from app.services.dashboard import admin_dashboard, get_privileged_role_activation_timeline, get_user_access_segment_members, get_user_access_segments


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
        admin_user = User(provider_id=provider.id, external_id="admin-oid", email="admin@x.com", display_name="Admin User", status="ACTIVE")
        group = Group(provider_id=provider.id, external_id="g1", name="Security Team", status="ACTIVE", is_privileged=False)
        privileged_role = Role(provider_id=provider.id, external_id="r1", name="Global Admin", role_type="DIRECTORY_ROLE", status="ACTIVE", is_privileged=True)
        plain_role = Role(provider_id=provider.id, external_id="r2", name="Reports Reader", role_type="DIRECTORY_ROLE", status="ACTIVE", is_privileged=False)
        session.add_all([target_user, admin_user, group, privileged_role, plain_role])
        await session.commit()
        return {"provider_id": provider.id, "user_id": target_user.id, "admin_id": admin_user.id, "group_id": group.id, "privileged_role_id": privileged_role.id, "plain_role_id": plain_role.id}


@pytest.mark.asyncio
async def test_admin_dashboard_counts_active_pending_and_expiring_access(db_override):
    ids = await _seed_directory(db_override.factory)
    now = datetime.now(timezone.utc)
    async with db_override.factory() as session:
        session.add_all([
            AccessAssignment(provider_id=ids["provider_id"], user_id=ids["user_id"], resource_type="GROUP", resource_id=ids["group_id"], assignment_type="PERMANENT", status="ACTIVE", justification="j"),
            AccessAssignment(provider_id=ids["provider_id"], user_id=ids["user_id"], resource_type="ROLE", resource_id=ids["privileged_role_id"], assignment_type="TEMPORARY", status="ACTIVE", expiration_time=now + timedelta(hours=2), justification="j"),
            AccessAssignment(provider_id=ids["provider_id"], user_id=ids["user_id"], resource_type="ROLE", resource_id=ids["plain_role_id"], assignment_type="TEMPORARY", status="ACTIVE", expiration_time=now + timedelta(days=10), justification="j"),
            AccessAssignment(provider_id=ids["provider_id"], user_id=ids["user_id"], resource_type="GROUP", resource_id=ids["group_id"], assignment_type="PERMANENT", status="PENDING_APPROVAL", justification="j"),
            AccessAssignment(provider_id=ids["provider_id"], user_id=ids["user_id"], resource_type="GROUP", resource_id=ids["group_id"], assignment_type="PERMANENT", status="ELIGIBLE", justification="j"),
        ])
        await session.commit()

    async with db_override.factory() as session:
        result = await admin_dashboard(session)
    assert result["activeSessions"] == 3
    assert result["pendingRequests"] == 1
    assert result["expiringAccess"] == 1  # only the one expiring within 24 hours


@pytest.mark.asyncio
async def test_privileged_role_activation_timeline_counts_privileged_roles_only(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        privileged = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "ROLE", "resource_id": str(ids["privileged_role_id"]), "assignment_type": "PERMANENT", "justification": "Need it."})
        plain = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "ROLE", "resource_id": str(ids["plain_role_id"]), "assignment_type": "PERMANENT", "justification": "Need it."})
        group_item = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "justification": "Need it."})
        for created in (privileged, plain, group_item):
            await client.post(f"/api/v1/assignments/{created.json()['id']}/activate", json={"duration_hours": 2, "justification": "Activating."})

    async with db_override.factory() as session:
        timeline = await get_privileged_role_activation_timeline(session, days=7)
    assert timeline["days"] == 7
    today = datetime.now(timezone.utc).date().isoformat()
    todays_entry = next(entry for entry in timeline["series"] if entry["date"] == today)
    assert todays_entry["count"] == 1  # only the privileged ROLE activation counts, not the plain role or the group


@pytest.mark.asyncio
async def test_privileged_role_activation_timeline_credits_assignment_owner_not_admin_actor(db_override):
    """An Admin activating on someone else's behalf must credit the timeline to that OTHER user, not the admin."""
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "ROLE", "resource_id": str(ids["privileged_role_id"]), "assignment_type": "PERMANENT", "justification": "Need it."})
        assignment_id = created.json()["id"]
        activated = await client.post(f"/api/v1/assignments/{assignment_id}/activate", json={"duration_hours": 2, "justification": "Activating on their behalf."})
    assert activated.status_code == 200

    async with db_override.factory() as session:
        timeline = await get_privileged_role_activation_timeline(session, days=7)
    today = datetime.now(timezone.utc).date().isoformat()
    todays_entry = next(entry for entry in timeline["series"] if entry["date"] == today)
    assert todays_entry["count"] == 1

    async with db_override.factory() as session:
        assignment = await session.get(AccessAssignment, UUID(assignment_id))
        assert assignment.user_id == ids["user_id"]  # the credited identity is the target user, not "admin-oid"


@pytest.mark.asyncio
async def test_user_access_segments_splits_permanent_active_from_eligible_without_double_counting(db_override):
    ids = await _seed_directory(db_override.factory)
    async with db_override.factory() as session:
        session.add_all([
            # target_user: both a Permanent+Active grant AND a separate Eligible one — must count under
            # permanentActive only, not also under eligible.
            AccessAssignment(provider_id=ids["provider_id"], user_id=ids["user_id"], resource_type="GROUP", resource_id=ids["group_id"], assignment_type="PERMANENT", status="ACTIVE", justification="j"),
            AccessAssignment(provider_id=ids["provider_id"], user_id=ids["user_id"], resource_type="ROLE", resource_id=ids["plain_role_id"], assignment_type="PERMANENT", status="ELIGIBLE", justification="j"),
            # admin_user: only an Eligible assignment, and a Temporary+Active one that must NOT count as permanentActive.
            AccessAssignment(provider_id=ids["provider_id"], user_id=ids["admin_id"], resource_type="ROLE", resource_id=ids["privileged_role_id"], assignment_type="PERMANENT", status="ELIGIBLE", justification="j"),
            AccessAssignment(provider_id=ids["provider_id"], user_id=ids["admin_id"], resource_type="GROUP", resource_id=ids["group_id"], assignment_type="TEMPORARY", status="ACTIVE", expiration_time=datetime.now(timezone.utc) + timedelta(hours=2), justification="j"),
        ])
        await session.commit()

    async with db_override.factory() as session:
        segments = await get_user_access_segments(session)
    assert segments["permanentActive"] == 1  # only target_user
    assert segments["eligible"] == 1  # only admin_user — target_user's eligible row doesn't also count them here

    async with db_override.factory() as session:
        permanent_active_members = await get_user_access_segment_members(session, "permanent-active")
        eligible_members = await get_user_access_segment_members(session, "eligible")
    assert [m["id"] for m in permanent_active_members] == [str(ids["user_id"])]
    assert [m["id"] for m in eligible_members] == [str(ids["admin_id"])]
    assert permanent_active_members[0]["display_name"] == "Target User"


@pytest.mark.asyncio
async def test_user_access_segment_members_returns_empty_list_for_unknown_segment(db_override):
    await _seed_directory(db_override.factory)
    async with db_override.factory() as session:
        members = await get_user_access_segment_members(session, "not-a-real-segment")
    assert members == []
