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
from app.models import AccessAssignment, AccessPackage, AccessPackageAssignment, AccessPackageItem, AccessReviewCampaign, AccessReviewItem, BusinessRole, BusinessRoleItem, BusinessRoleOwner, Group, IdentityProvider, Notification, Role, User
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


def future(hours: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def past(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


async def _seed(factory) -> dict:
    async with factory() as session:
        provider = IdentityProvider(name="Directory", type="MOCK", status="CONNECTED", tenant_id="t")
        session.add(provider)
        await session.flush()
        group = Group(provider_id=provider.id, external_id="g1", name="Finance Team", status="ACTIVE", is_privileged=False)
        member = User(provider_id=provider.id, external_id="member-oid", email="member@x.com", display_name="Existing Member", status="ACTIVE")
        reviewer = User(provider_id=provider.id, external_id="reviewer-oid", email="reviewer@x.com", display_name="Reviewer Amy", status="ACTIVE")
        fallback = User(provider_id=provider.id, external_id="fallback-oid", email="fallback@x.com", display_name="Fallback Fred", status="ACTIVE")
        session.add_all([group, member, reviewer, fallback])
        await session.flush()
        assignment = AccessAssignment(provider_id=provider.id, user_id=member.id, resource_type="GROUP", resource_id=group.id, assignment_type="PERMANENT", status="ACTIVE", justification="Existing access.")
        session.add(assignment)
        await session.commit()
        await session.refresh(assignment)
        return {"provider_id": provider.id, "group_id": group.id, "member_id": member.id, "reviewer_id": reviewer.id, "fallback_id": fallback.id, "assignment_id": assignment.id}


@pytest.mark.asyncio
async def test_admin_can_create_a_campaign_and_it_snapshots_matching_assignments(db_override):
    seeded = await _seed(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/access-reviews", json={
            "name": "Finance Team Q1 Review", "scope_type": "SPECIFIC_RESOURCE", "scope_resource_type": "GROUP", "scope_resource_id": str(seeded["group_id"]),
            "reviewer_id": str(seeded["reviewer_id"]), "due_at": future(24),
        })
    assert response.status_code == 201
    body = response.json()
    assert body["item_count"] == 1
    assert body["decided_count"] == 0
    assert body["status"] == "ACTIVE"

    async with db_override.factory() as session:
        items = (await session.execute(select(AccessReviewItem).where(AccessReviewItem.campaign_id == UUID(body["id"])))).scalars().all()
    assert len(items) == 1
    assert items[0].user_id == seeded["member_id"]
    assert items[0].assignment_status_at_snapshot == "ACTIVE"
    assert items[0].decision == "PENDING"


@pytest.mark.asyncio
async def test_deciding_approved_never_touches_the_underlying_assignment(db_override):
    seeded = await _seed(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/access-reviews", json={
            "name": "Approve Test", "scope_type": "SPECIFIC_RESOURCE", "scope_resource_type": "GROUP", "scope_resource_id": str(seeded["group_id"]),
            "reviewer_id": str(seeded["reviewer_id"]), "due_at": future(24),
        })
        items = await client.get(f"/api/v1/access-reviews/{created.json()['id']}/items")
        item_id = items.json()[0]["id"]

        authenticate_as("AccessPilot.User", subject="reviewer-oid")
        decided = await client.post(f"/api/v1/access-reviews/items/{item_id}/decide", json={"decision": "APPROVED", "justification": "Still needed for their role."})
    assert decided.status_code == 200
    assert decided.json()["decision"] == "APPROVED"

    async with db_override.factory() as session:
        assignment = await session.get(AccessAssignment, seeded["assignment_id"])
    assert assignment.status == "ACTIVE"  # completely untouched


@pytest.mark.asyncio
async def test_deciding_revoked_calls_the_real_revoke_path_and_completes_the_campaign(db_override):
    seeded = await _seed(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/access-reviews", json={
            "name": "Revoke Test", "scope_type": "SPECIFIC_RESOURCE", "scope_resource_type": "GROUP", "scope_resource_id": str(seeded["group_id"]),
            "reviewer_id": str(seeded["reviewer_id"]), "due_at": future(24),
        })
        campaign_id = created.json()["id"]
        items = await client.get(f"/api/v1/access-reviews/{campaign_id}/items")
        item_id = items.json()[0]["id"]

        authenticate_as("AccessPilot.User", subject="reviewer-oid")
        decided = await client.post(f"/api/v1/access-reviews/items/{item_id}/decide", json={"decision": "REVOKED", "justification": "No longer needed."})
        assert decided.status_code == 200

        authenticate_as("AccessPilot.Admin")
        campaign_after = await client.get(f"/api/v1/access-reviews/{campaign_id}")
    assert campaign_after.json()["status"] == "COMPLETED"
    assert campaign_after.json()["decided_count"] == 1

    async with db_override.factory() as session:
        assignment = await session.get(AccessAssignment, seeded["assignment_id"])
    assert assignment.status == "REVOKED"


@pytest.mark.asyncio
async def test_a_non_reviewer_cannot_decide_an_item(db_override):
    seeded = await _seed(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/access-reviews", json={
            "name": "Access Control Test", "scope_type": "SPECIFIC_RESOURCE", "scope_resource_type": "GROUP", "scope_resource_id": str(seeded["group_id"]),
            "reviewer_id": str(seeded["reviewer_id"]), "due_at": future(24),
        })
        items = await client.get(f"/api/v1/access-reviews/{created.json()['id']}/items")
        item_id = items.json()[0]["id"]

        authenticate_as("AccessPilot.User", subject="someone-else-oid")
        response = await client.post(f"/api/v1/access-reviews/items/{item_id}/decide", json={"decision": "APPROVED", "justification": "Trying to sneak a decision in."})
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_fallback_reviewer_blocked_before_unlock_hours_elapse(db_override):
    seeded = await _seed(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/access-reviews", json={
            "name": "Fallback Test", "scope_type": "SPECIFIC_RESOURCE", "scope_resource_type": "GROUP", "scope_resource_id": str(seeded["group_id"]),
            "reviewer_id": str(seeded["reviewer_id"]), "fallback_reviewer_id": str(seeded["fallback_id"]), "fallback_unlock_hours": 48, "due_at": future(72),
        })
        items = await client.get(f"/api/v1/access-reviews/{created.json()['id']}/items")
        item_id = items.json()[0]["id"]

        authenticate_as("AccessPilot.User", subject="fallback-oid")
        response = await client.post(f"/api/v1/access-reviews/items/{item_id}/decide", json={"decision": "APPROVED", "justification": "Acting too early."})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FALLBACK_NOT_YET_AVAILABLE"


@pytest.mark.asyncio
async def test_manual_complete_auto_revokes_pending_items(db_override):
    seeded = await _seed(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/access-reviews", json={
            "name": "Manual Complete Test", "scope_type": "SPECIFIC_RESOURCE", "scope_resource_type": "GROUP", "scope_resource_id": str(seeded["group_id"]),
            "reviewer_id": str(seeded["reviewer_id"]), "due_at": future(24),
        })
        completed = await client.post(f"/api/v1/access-reviews/{created.json()['id']}/complete")
    assert completed.status_code == 200
    assert completed.json()["status"] == "COMPLETED"

    async with db_override.factory() as session:
        assignment = await session.get(AccessAssignment, seeded["assignment_id"])
        item = (await session.execute(select(AccessReviewItem).where(AccessReviewItem.campaign_id == UUID(created.json()["id"])))).scalars().one()
    assert assignment.status == "REVOKED"
    assert item.decision == "AUTO_REVOKED"


@pytest.mark.asyncio
async def test_overdue_campaign_is_auto_swept_by_the_worker_function(db_override):
    from app.services.access_reviews import sweep_overdue_campaigns

    seeded = await _seed(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/access-reviews", json={
            "name": "Overdue Test", "scope_type": "SPECIFIC_RESOURCE", "scope_resource_type": "GROUP", "scope_resource_id": str(seeded["group_id"]),
            "reviewer_id": str(seeded["reviewer_id"]), "due_at": future(24),
        })
        campaign_id = UUID(created.json()["id"])

    async with db_override.factory() as session:
        campaign = await session.get(AccessReviewCampaign, campaign_id)
        campaign.due_at = datetime.now(timezone.utc) - timedelta(hours=1)
        await session.commit()

        swept = await sweep_overdue_campaigns(session)
        assert swept == 1

        assignment = await session.get(AccessAssignment, seeded["assignment_id"])
        campaign_after = await session.get(AccessReviewCampaign, campaign_id)
    assert assignment.status == "REVOKED"
    assert campaign_after.status == "COMPLETED"


@pytest.mark.asyncio
async def test_access_review_admin_role_can_manage_campaigns_without_the_admin_role(db_override):
    """AccessPilot.AccessReviewAdmin's permission set is a full Admin superset by design — confirms a user
    holding ONLY that role (never the literal AccessPilot.Admin string) can still create/manage campaigns."""
    seeded = await _seed(db_override.factory)
    authenticate_as("AccessPilot.AccessReviewAdmin", subject="review-admin-oid")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/access-reviews", json={
            "name": "Review Admin Test", "scope_type": "SPECIFIC_RESOURCE", "scope_resource_type": "GROUP", "scope_resource_id": str(seeded["group_id"]),
            "reviewer_id": str(seeded["reviewer_id"]), "due_at": future(24),
        })
    assert response.status_code == 201


@pytest.mark.asyncio
async def test_a_normal_user_cannot_create_a_campaign(db_override):
    seeded = await _seed(db_override.factory)
    authenticate_as("AccessPilot.User", subject="regular-user-oid")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/access-reviews", json={
            "name": "Denied Test", "scope_type": "SPECIFIC_RESOURCE", "scope_resource_type": "GROUP", "scope_resource_id": str(seeded["group_id"]),
            "reviewer_id": str(seeded["reviewer_id"]), "due_at": future(24),
        })
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_my_review_items_lists_pending_items_across_campaigns(db_override):
    seeded = await _seed(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/access-reviews", json={
            "name": "My Items Test", "scope_type": "SPECIFIC_RESOURCE", "scope_resource_type": "GROUP", "scope_resource_id": str(seeded["group_id"]),
            "reviewer_id": str(seeded["reviewer_id"]), "due_at": future(24),
        })
        authenticate_as("AccessPilot.User", subject="reviewer-oid")
        mine = await client.get("/api/v1/access-reviews/items/mine")
    assert mine.status_code == 200
    assert len(mine.json()) == 1
    assert mine.json()[0]["decision"] == "PENDING"


async def _seed_multi_scope(factory) -> dict:
    """A Group, a Role, and a Package (with its own item) — three DIFFERENT resource kinds, each with a real
    non-final assignment, so one MULTIPLE_RESOURCES campaign can mix all three in a single test."""
    async with factory() as session:
        provider = IdentityProvider(name="Directory", type="MOCK", status="CONNECTED", tenant_id="t")
        session.add(provider)
        await session.flush()
        group = Group(provider_id=provider.id, external_id="g-multi", name="IT Group", status="ACTIVE", is_privileged=False)
        role = Role(provider_id=provider.id, external_id="r-multi", name="Reports Reader", role_type="DIRECTORY_ROLE", status="ACTIVE")
        member_a = User(provider_id=provider.id, external_id="member-a", email="membera@x.com", display_name="Member A", status="ACTIVE")
        member_b = User(provider_id=provider.id, external_id="member-b", email="memberb@x.com", display_name="Member B", status="ACTIVE")
        reviewer = User(provider_id=provider.id, external_id="multi-reviewer", email="multireviewer@x.com", display_name="Multi Reviewer", status="ACTIVE")
        session.add_all([group, role, member_a, member_b, reviewer])
        await session.flush()
        package = AccessPackage(name="Starter Kit (multi)", status="ACTIVE")
        session.add(package)
        await session.flush()
        session.add(AccessPackageItem(package_id=package.id, resource_type="ROLE", resource_id=role.id))
        group_assignment = AccessAssignment(provider_id=provider.id, user_id=member_a.id, resource_type="GROUP", resource_id=group.id, assignment_type="PERMANENT", status="ACTIVE", justification="Group access.")
        # A package-sourced ROLE assignment for member_b, tracked via AccessPackageAssignment — the case
        # _matching_assignments_for_target's PACKAGE branch specifically has to reach via the join table.
        package_role_assignment = AccessAssignment(provider_id=provider.id, user_id=member_b.id, resource_type="ROLE", resource_id=role.id, assignment_type="PERMANENT", status="ELIGIBLE", justification="Package: Starter Kit (multi)")
        session.add_all([group_assignment, package_role_assignment])
        await session.flush()
        session.add(AccessPackageAssignment(package_id=package.id, package_assignment_id=package_role_assignment.id, assignment_id=package_role_assignment.id, user_id=member_b.id))
        await session.commit()
        await session.refresh(group_assignment)
        await session.refresh(package_role_assignment)
        return {"provider_id": provider.id, "group_id": group.id, "role_id": role.id, "package_id": package.id, "reviewer_id": reviewer.id, "group_assignment_id": group_assignment.id, "package_role_assignment_id": package_role_assignment.id}


@pytest.mark.asyncio
async def test_multiple_resources_scope_mixes_a_group_and_a_package_in_one_campaign(db_override):
    seeded = await _seed_multi_scope(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/access-reviews", json={
            "name": "Mixed Scope Review", "scope_type": "MULTIPLE_RESOURCES",
            "scope_targets": [{"resource_type": "GROUP", "resource_id": str(seeded["group_id"])}, {"resource_type": "PACKAGE", "resource_id": str(seeded["package_id"])}],
            "reviewer_id": str(seeded["reviewer_id"]), "due_at": future(24),
        })
    assert response.status_code == 201
    body = response.json()
    assert body["item_count"] == 2  # the GROUP assignment + the package-sourced ROLE assignment
    assert len(body["scope_targets"]) == 2
    resolved_names = {t["resource_display_name"] for t in body["scope_targets"]}
    assert resolved_names == {"IT Group", "Starter Kit (multi)"}

    async with db_override.factory() as session:
        items = (await session.execute(select(AccessReviewItem).where(AccessReviewItem.campaign_id == UUID(body["id"])))).scalars().all()
    assignment_ids = {item.assignment_id for item in items}
    assert assignment_ids == {seeded["group_assignment_id"], seeded["package_role_assignment_id"]}


@pytest.mark.asyncio
async def test_multiple_resources_scope_rejects_a_nonexistent_target(db_override):
    seeded = await _seed_multi_scope(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/access-reviews", json={
            "name": "Bad Target Review", "scope_type": "MULTIPLE_RESOURCES",
            "scope_targets": [{"resource_type": "GROUP", "resource_id": "00000000-0000-0000-0000-000000000000"}],
            "reviewer_id": str(seeded["reviewer_id"]), "due_at": future(24),
        })
    assert response.status_code == 404

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        listed = await client.get("/api/v1/access-reviews")
    assert listed.json() == []  # nothing was created


@pytest.mark.asyncio
async def test_admin_can_edit_a_campaigns_metadata(db_override):
    seeded = await _seed(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/access-reviews", json={
            "name": "Original Name", "scope_type": "SPECIFIC_RESOURCE", "scope_resource_type": "GROUP", "scope_resource_id": str(seeded["group_id"]),
            "reviewer_id": str(seeded["reviewer_id"]), "due_at": future(24),
        })
        campaign_id = created.json()["id"]
        edited = await client.patch(f"/api/v1/access-reviews/{campaign_id}", json={"name": "Renamed Campaign", "reviewer_id": str(seeded["fallback_id"]), "due_at": future(48)})
    assert edited.status_code == 200
    body = edited.json()
    assert body["name"] == "Renamed Campaign"
    assert body["reviewer_id"] == str(seeded["fallback_id"])
    # Scope is completely untouched by the edit.
    assert body["scope_type"] == "SPECIFIC_RESOURCE"
    assert body["scope_resource_id"] == str(seeded["group_id"])
    assert body["item_count"] == 1


@pytest.mark.asyncio
async def test_reassigning_the_reviewer_lets_the_new_reviewer_decide_items(db_override):
    seeded = await _seed(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/access-reviews", json={
            "name": "Reassign Test", "scope_type": "SPECIFIC_RESOURCE", "scope_resource_type": "GROUP", "scope_resource_id": str(seeded["group_id"]),
            "reviewer_id": str(seeded["reviewer_id"]), "due_at": future(24),
        })
        campaign_id = created.json()["id"]
        await client.patch(f"/api/v1/access-reviews/{campaign_id}", json={"reviewer_id": str(seeded["fallback_id"])})
        items = await client.get(f"/api/v1/access-reviews/{campaign_id}/items")
        item_id = items.json()[0]["id"]

        # The ORIGINAL reviewer can no longer decide — they're no longer the campaign's reviewer.
        authenticate_as("AccessPilot.User", subject="reviewer-oid")
        denied = await client.post(f"/api/v1/access-reviews/items/{item_id}/decide", json={"decision": "APPROVED", "justification": "Old reviewer trying to act."})
        assert denied.status_code == 403

        # The NEWLY assigned reviewer can.
        authenticate_as("AccessPilot.User", subject="fallback-oid")
        allowed = await client.post(f"/api/v1/access-reviews/items/{item_id}/decide", json={"decision": "APPROVED", "justification": "New reviewer confirms access is still needed."})
    assert allowed.status_code == 200


@pytest.mark.asyncio
async def test_a_completed_campaign_cannot_be_edited(db_override):
    seeded = await _seed(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/access-reviews", json={
            "name": "Closed Campaign", "scope_type": "SPECIFIC_RESOURCE", "scope_resource_type": "GROUP", "scope_resource_id": str(seeded["group_id"]),
            "reviewer_id": str(seeded["reviewer_id"]), "due_at": future(24),
        })
        campaign_id = created.json()["id"]
        await client.post(f"/api/v1/access-reviews/{campaign_id}/complete")
        response = await client.patch(f"/api/v1/access-reviews/{campaign_id}", json={"name": "Trying to rename after close"})
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_a_normal_user_cannot_edit_a_campaign(db_override):
    seeded = await _seed(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/access-reviews", json={
            "name": "Permission Test", "scope_type": "SPECIFIC_RESOURCE", "scope_resource_type": "GROUP", "scope_resource_id": str(seeded["group_id"]),
            "reviewer_id": str(seeded["reviewer_id"]), "due_at": future(24),
        })
        campaign_id = created.json()["id"]
        authenticate_as("AccessPilot.User", subject="regular-user-oid")
        response = await client.patch(f"/api/v1/access-reviews/{campaign_id}", json={"name": "Sneaky rename"})
    assert response.status_code == 403


async def _seed_with_creator(factory) -> dict:
    """Same shape as _seed, plus a real User row whose external_id matches authenticate_as's default admin
    subject ("admin-oid") — _seed's own campaigns always resolve created_by to None because no such row exists
    there, which is fine for those tests but hides the creator-notification behavior this file also needs to
    cover."""
    async with factory() as session:
        provider = IdentityProvider(name="Directory", type="MOCK", status="CONNECTED", tenant_id="t")
        session.add(provider)
        await session.flush()
        group = Group(provider_id=provider.id, external_id="g1-creator", name="Finance Team", status="ACTIVE", is_privileged=False)
        member = User(provider_id=provider.id, external_id="member-oid-creator", email="memberx@x.com", display_name="Existing Member", status="ACTIVE")
        reviewer = User(provider_id=provider.id, external_id="reviewer-oid-creator", email="reviewerx@x.com", display_name="Reviewer Amy", status="ACTIVE")
        creator = User(provider_id=provider.id, external_id="admin-oid", email="adminx@x.com", display_name="Admin Creator", status="ACTIVE")
        session.add_all([group, member, reviewer, creator])
        await session.flush()
        assignment = AccessAssignment(provider_id=provider.id, user_id=member.id, resource_type="GROUP", resource_id=group.id, assignment_type="PERMANENT", status="ACTIVE", justification="Existing access.")
        session.add(assignment)
        await session.commit()
        await session.refresh(assignment)
        return {"group_id": group.id, "member_id": member.id, "reviewer_id": reviewer.id, "creator_id": creator.id, "assignment_id": assignment.id}


@pytest.mark.asyncio
async def test_campaign_creator_is_notified_when_a_reviewer_decides_an_item(db_override):
    seeded = await _seed_with_creator(db_override.factory)
    authenticate_as("AccessPilot.Admin")  # default subject "admin-oid" matches the seeded creator
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/access-reviews", json={
            "name": "Creator Notify Test", "scope_type": "SPECIFIC_RESOURCE", "scope_resource_type": "GROUP", "scope_resource_id": str(seeded["group_id"]),
            "reviewer_id": str(seeded["reviewer_id"]), "due_at": future(24),
        })
        campaign_id = created.json()["id"]
        items = await client.get(f"/api/v1/access-reviews/{campaign_id}/items")
        item_id = items.json()[0]["id"]

        authenticate_as("AccessPilot.User", subject="reviewer-oid-creator")
        decided = await client.post(f"/api/v1/access-reviews/items/{item_id}/decide", json={"decision": "APPROVED", "justification": "Still needed for their role."})
    assert decided.status_code == 200

    async with db_override.factory() as session:
        notifications = (await session.execute(select(Notification).where(Notification.user_id == seeded["creator_id"], Notification.notification_type == "ACCESS_REVIEW_ITEM_DECIDED"))).scalars().all()
    assert len(notifications) == 1
    assert "approved" in notifications[0].message


@pytest.mark.asyncio
async def test_manual_complete_of_a_recurring_campaign_spawns_the_next_one(db_override):
    seeded = await _seed(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/access-reviews", json={
            "name": "Quarterly Group Review", "scope_type": "SPECIFIC_RESOURCE", "scope_resource_type": "GROUP", "scope_resource_id": str(seeded["group_id"]),
            "reviewer_id": str(seeded["reviewer_id"]), "due_at": future(24), "frequency_days": 90,
        })
        campaign_id = created.json()["id"]
        completed = await client.post(f"/api/v1/access-reviews/{campaign_id}/complete")
        assert completed.status_code == 200
        listed = await client.get("/api/v1/access-reviews")
    campaigns = listed.json()
    assert len(campaigns) == 2
    original = next(c for c in campaigns if c["id"] == campaign_id)
    spawned = next(c for c in campaigns if c["id"] != campaign_id)
    assert original["status"] == "COMPLETED"
    assert spawned["status"] == "ACTIVE"
    assert spawned["parent_campaign_id"] == campaign_id
    assert spawned["frequency_days"] == 90
    assert spawned["reviewer_id"] == str(seeded["reviewer_id"])
    assert spawned["scope_resource_id"] == str(seeded["group_id"])
    # The only assignment in scope was just auto-revoked by the manual completion above, so the freshly-spawned
    # campaign correctly finds nothing left to review yet — not a bug, a faithful re-snapshot of current reality.
    assert spawned["item_count"] == 0


@pytest.mark.asyncio
async def test_deciding_the_last_item_of_a_recurring_campaign_spawns_the_next_one(db_override):
    seeded = await _seed(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/access-reviews", json={
            "name": "Recurring Approve Test", "scope_type": "SPECIFIC_RESOURCE", "scope_resource_type": "GROUP", "scope_resource_id": str(seeded["group_id"]),
            "reviewer_id": str(seeded["reviewer_id"]), "due_at": future(24), "frequency_days": 30,
        })
        campaign_id = created.json()["id"]
        items = await client.get(f"/api/v1/access-reviews/{campaign_id}/items")
        item_id = items.json()[0]["id"]

        authenticate_as("AccessPilot.User", subject="reviewer-oid")
        decided = await client.post(f"/api/v1/access-reviews/items/{item_id}/decide", json={"decision": "APPROVED", "justification": "Still needed."})
        assert decided.status_code == 200

        authenticate_as("AccessPilot.Admin")
        listed = await client.get("/api/v1/access-reviews")
    campaigns = listed.json()
    assert len(campaigns) == 2
    spawned = next(c for c in campaigns if c["id"] != campaign_id)
    assert spawned["parent_campaign_id"] == campaign_id
    # APPROVED never touches the underlying assignment, so it's still ACTIVE and correctly re-snapshotted.
    assert spawned["item_count"] == 1


@pytest.mark.asyncio
async def test_dashboard_summary_reflects_real_counts(db_override):
    seeded = await _seed(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/access-reviews", json={
            "name": "Dashboard Test", "scope_type": "SPECIFIC_RESOURCE", "scope_resource_type": "GROUP", "scope_resource_id": str(seeded["group_id"]),
            "reviewer_id": str(seeded["reviewer_id"]), "due_at": future(24),
        })
        campaign_id = created.json()["id"]
        items = await client.get(f"/api/v1/access-reviews/{campaign_id}/items")
        item_id = items.json()[0]["id"]
        authenticate_as("AccessPilot.User", subject="reviewer-oid")
        await client.post(f"/api/v1/access-reviews/items/{item_id}/decide", json={"decision": "REVOKED", "justification": "No longer needed."})

        authenticate_as("AccessPilot.Admin")
        dashboard = await client.get("/api/v1/access-reviews/dashboard")
    assert dashboard.status_code == 200
    body = dashboard.json()
    assert body["total_campaigns"] == 1
    assert body["completed_campaigns"] == 1  # auto-completed once its only item was decided
    assert body["total_items"] == 1
    assert body["revoked_items"] == 1
    assert body["pending_items"] == 0
    assert any(g["name"] == "Finance Team" and g["count"] == 1 for g in body["top_groups"])


@pytest.mark.asyncio
async def test_inactive_users_scope_fails_clearly_without_a_real_entra_connector(db_override):
    """_seed's IdentityProvider is type MOCK, not a real Entra connection — confirms this scope fails FAST with a
    clear, actionable error rather than silently creating a campaign that reviews nobody, and that nothing is
    left half-created."""
    seeded = await _seed(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/access-reviews", json={
            "name": "Inactive Users Test", "scope_type": "INACTIVE_USERS", "scope_inactive_days": 90,
            "reviewer_id": str(seeded["reviewer_id"]), "due_at": future(24),
        })
    assert response.status_code == 424
    assert response.json()["error"]["code"] == "INACTIVE_USER_CHECK_UNAVAILABLE"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        listed = await client.get("/api/v1/access-reviews")
    assert listed.json() == []


@pytest.mark.asyncio
async def test_reviewer_is_notified_when_a_recurring_cycle_completes_and_spawns_the_next_one(db_override):
    seeded = await _seed(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/access-reviews", json={
            "name": "Notify Recurrence Test", "scope_type": "SPECIFIC_RESOURCE", "scope_resource_type": "GROUP", "scope_resource_id": str(seeded["group_id"]),
            "reviewer_id": str(seeded["reviewer_id"]), "due_at": future(24), "frequency_days": 30,
        })
        campaign_id = created.json()["id"]
        completed = await client.post(f"/api/v1/access-reviews/{campaign_id}/complete")
        assert completed.status_code == 200

    async with db_override.factory() as session:
        notifications = (await session.execute(select(Notification).where(Notification.user_id == seeded["reviewer_id"], Notification.notification_type == "ACCESS_REVIEW_RECURRENCE_CREATED"))).scalars().all()
    assert len(notifications) == 1
    assert "next one is already scheduled" in notifications[0].message


@pytest.mark.asyncio
async def test_item_response_includes_the_users_email(db_override):
    seeded = await _seed(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/access-reviews", json={
            "name": "Email Field Test", "scope_type": "SPECIFIC_RESOURCE", "scope_resource_type": "GROUP", "scope_resource_id": str(seeded["group_id"]),
            "reviewer_id": str(seeded["reviewer_id"]), "due_at": future(24),
        })
        items = await client.get(f"/api/v1/access-reviews/{created.json()['id']}/items")
    assert items.json()[0]["user_email"] == "member@x.com"


@pytest.mark.asyncio
async def test_items_report_where_the_entitlement_came_from_and_package_scope_excludes_other_holders(db_override):
    """A PACKAGE-scoped campaign must contain ONLY package-sourced grants — a user holding the same underlying
    resource directly (not via the package) must not appear — and each item states its source."""
    seeded = await _seed_multi_scope(db_override.factory)
    async with db_override.factory() as session:
        outsider = User(provider_id=seeded["provider_id"], external_id="outsider", email="out@x.com", display_name="Direct Holder", status="ACTIVE")
        session.add(outsider)
        await session.flush()
        session.add(AccessAssignment(provider_id=seeded["provider_id"], user_id=outsider.id, resource_type="ROLE", resource_id=seeded["role_id"], assignment_type="PERMANENT", status="ACTIVE", justification="Direct grant."))
        await session.commit()
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/access-reviews", json={"name": "Pkg only", "scope_type": "SPECIFIC_RESOURCE", "scope_resource_type": "PACKAGE", "scope_resource_id": str(seeded["package_id"]), "reviewer_id": str(seeded["reviewer_id"]), "due_at": future(24)})
        items = (await client.get(f"/api/v1/access-reviews/{created.json()['id']}/items")).json()
    assert len(items) == 1
    assert items[0]["user_display_name"] == "Member B"
    assert items[0]["granted_via"] == "Package: Starter Kit (multi)"


async def _seed_business_role_scope(factory) -> dict:
    """A Business Role with one mapped Group item, granted to one person through assign_business_role (so
    business_role_id is actually set), plus a second person holding the same raw group DIRECTLY — the BUSINESS_ROLE
    scope's analog of _seed_multi_scope's package-vs-direct-holder setup."""
    async with factory() as session:
        provider = IdentityProvider(name="Directory", type="MOCK", status="CONNECTED", tenant_id="t")
        session.add(provider)
        await session.flush()
        group = Group(provider_id=provider.id, external_id="g-br", name="Finance Group", status="ACTIVE", is_privileged=False)
        owner = User(provider_id=provider.id, external_id="br-owner", email="owner@x.com", display_name="Role Owner", status="ACTIVE")
        holder = User(provider_id=provider.id, external_id="br-holder", email="holder@x.com", display_name="Role Holder", status="ACTIVE")
        direct_holder = User(provider_id=provider.id, external_id="br-direct", email="direct@x.com", display_name="Direct Holder", status="ACTIVE")
        reviewer = User(provider_id=provider.id, external_id="br-reviewer", email="brreviewer@x.com", display_name="BR Reviewer", status="ACTIVE")
        session.add_all([group, owner, holder, direct_holder, reviewer])
        await session.flush()
        role = BusinessRole(name="Finance Analyst (AR test)", status="ACTIVE")
        session.add(role)
        await session.flush()
        session.add(BusinessRoleItem(role_id=role.id, resource_type="GROUP", resource_id=group.id))
        session.add(BusinessRoleOwner(role_id=role.id, user_id=owner.id))
        batch_id = holder.id  # any UUID works as the batch id for this seed
        role_assignment = AccessAssignment(provider_id=provider.id, user_id=holder.id, resource_type="GROUP", resource_id=group.id, assignment_type="PERMANENT", status="ELIGIBLE", justification="Business Role: Finance Analyst (AR test)", business_role_id=role.id, role_assignment_id=batch_id)
        direct_assignment = AccessAssignment(provider_id=provider.id, user_id=direct_holder.id, resource_type="GROUP", resource_id=group.id, assignment_type="PERMANENT", status="ACTIVE", justification="Direct grant.")
        session.add_all([role_assignment, direct_assignment])
        await session.commit()
        await session.refresh(role_assignment)
        await session.refresh(direct_assignment)
        return {"provider_id": provider.id, "group_id": group.id, "role_id": role.id, "owner_id": owner.id, "reviewer_id": reviewer.id, "role_assignment_id": role_assignment.id, "direct_assignment_id": direct_assignment.id}


@pytest.mark.asyncio
async def test_business_role_scope_includes_only_role_sourced_grants_and_reports_its_source(db_override):
    """Completing the loop from this session's Business Role work: a BUSINESS_ROLE-scoped campaign must contain
    ONLY grants tagged with that role's business_role_id — a direct holder of the same raw group must not appear —
    and the item states its source, mirroring the existing PACKAGE-scope test above."""
    seeded = await _seed_business_role_scope(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/access-reviews", json={"name": "Business role only", "scope_type": "SPECIFIC_RESOURCE", "scope_resource_type": "BUSINESS_ROLE", "scope_resource_id": str(seeded["role_id"]), "reviewer_id": str(seeded["reviewer_id"]), "due_at": future(24)})
        assert created.status_code == 201
        assert created.json()["item_count"] == 1
        items = (await client.get(f"/api/v1/access-reviews/{created.json()['id']}/items")).json()
    assert len(items) == 1
    assert items[0]["assignment_id"] == str(seeded["role_assignment_id"])
    assert items[0]["user_display_name"] == "Role Holder"
    assert items[0]["granted_via"] == "Business Role: Finance Analyst (AR test)"
    assert items[0]["business_role_id"] == str(seeded["role_id"])
    assert items[0]["business_role_name"] == "Finance Analyst (AR test)"


@pytest.mark.asyncio
async def test_business_role_scope_rejects_an_unknown_role_and_suggests_its_owner(db_override):
    seeded = await _seed_business_role_scope(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        from uuid import uuid4
        rejected = await client.post("/api/v1/access-reviews", json={"name": "Bad role", "scope_type": "SPECIFIC_RESOURCE", "scope_resource_type": "BUSINESS_ROLE", "scope_resource_id": str(uuid4()), "reviewer_id": str(seeded["reviewer_id"]), "due_at": future(24)})
        assert rejected.status_code == 404
        suggestions = (await client.get(f"/api/v1/access-reviews/owner-suggestions?resource_type=BUSINESS_ROLE&resource_id={seeded['role_id']}")).json()
    assert len(suggestions) == 1 and suggestions[0]["display_name"] == "Role Owner" and suggestions[0]["source"] == "Business Role owner"


def test_compute_next_run_handles_day_time_timezone_and_short_months():
    from app.services.access_reviews import compute_next_run

    utc = timezone.utc
    # Berlin is UTC+1 in January: 09:00 local == 08:00 UTC. Before the slot -> this month's slot.
    assert compute_next_run(datetime(2027, 1, 10, tzinfo=utc), 15, "09:00", 1, "Europe/Berlin") == datetime(2027, 1, 15, 8, 0, tzinfo=utc)
    # After this month's slot -> next month's.
    assert compute_next_run(datetime(2027, 1, 15, 10, 0, tzinfo=utc), 15, "09:00", 1, "Europe/Berlin") == datetime(2027, 2, 15, 8, 0, tzinfo=utc)
    # Day 31 clamps to the month's last day; summer time (UTC+2) shifts the UTC hour.
    assert compute_next_run(datetime(2027, 2, 1, tzinfo=utc), 31, "09:00", 1, "Europe/Berlin") == datetime(2027, 2, 28, 8, 0, tzinfo=utc)
    assert compute_next_run(datetime(2027, 6, 1, tzinfo=utc), 15, "09:00", 1, "Europe/Berlin") == datetime(2027, 6, 15, 7, 0, tzinfo=utc)
    # Every 3 months steps by quarter, and crosses the year boundary.
    assert compute_next_run(datetime(2027, 11, 20, tzinfo=utc), 5, "00:30", 3, "UTC") == datetime(2028, 2, 5, 0, 30, tzinfo=utc)


@pytest.mark.asyncio
async def test_fixed_schedule_sets_next_run_and_is_exclusive_with_repeat_after_completion(db_override):
    seeded = await _seed(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    base = {"name": "Monthly on the 15th", "scope_type": "SPECIFIC_RESOURCE", "scope_resource_type": "GROUP", "scope_resource_id": str(seeded["group_id"]), "reviewer_id": str(seeded["reviewer_id"]), "due_at": future(24 * 7)}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        ok = await client.post("/api/v1/access-reviews", json={**base, "schedule_day_of_month": 15, "schedule_time": "09:00", "schedule_every_months": 1, "schedule_due_days": 7})
        both = await client.post("/api/v1/access-reviews", json={**base, "schedule_day_of_month": 15, "schedule_time": "09:00", "frequency_days": 30})
        half = await client.post("/api/v1/access-reviews", json={**base, "schedule_day_of_month": 15})
    assert ok.status_code == 201
    body = ok.json()
    assert body["schedule_day_of_month"] == 15 and body["schedule_time"] == "09:00" and body["next_run_at"] is not None
    assert body["frequency_days"] is None
    assert both.status_code == 422
    assert half.status_code == 422


@pytest.mark.asyncio
async def test_worker_starts_the_next_scheduled_campaign_only_when_the_previous_one_is_closed(db_override):
    from app.services.access_reviews import sweep_scheduled_campaigns

    seeded = await _seed(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/access-reviews", json={"name": "Scheduled", "scope_type": "SPECIFIC_RESOURCE", "scope_resource_type": "GROUP", "scope_resource_id": str(seeded["group_id"]), "reviewer_id": str(seeded["reviewer_id"]), "due_at": future(24 * 7), "schedule_day_of_month": 15, "schedule_time": "09:00", "schedule_due_days": 7})
    holder_id = UUID(created.json()["id"])

    async with db_override.factory() as session:
        holder = await session.get(AccessReviewCampaign, holder_id)
        holder.next_run_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        await session.commit()

        # Previous cycle still ACTIVE -> this occurrence is skipped, nothing new is created, the slot advances.
        assert await sweep_scheduled_campaigns(session) == 0
        assert len((await session.scalars(select(AccessReviewCampaign))).all()) == 1
        await session.refresh(holder)
        assert holder.next_run_at is not None and holder.next_run_at.replace(tzinfo=timezone.utc) > datetime.now(timezone.utc)

        # Close it, make the slot due again -> the next campaign starts and the schedule moves onto it.
        holder.status = "COMPLETED"
        holder.next_run_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        await session.commit()
        assert await sweep_scheduled_campaigns(session) == 1
        campaigns = (await session.scalars(select(AccessReviewCampaign))).all()
        assert len(campaigns) == 2
        new = next(c for c in campaigns if c.id != holder_id)
        await session.refresh(holder)
    assert new.parent_campaign_id == holder_id and new.status == "ACTIVE"
    assert new.schedule_day_of_month == 15 and new.next_run_at is not None
    assert holder.next_run_at is None


@pytest.mark.asyncio
async def test_editing_to_a_fixed_schedule_replaces_repeat_after_completion(db_override):
    seeded = await _seed(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/access-reviews", json={"name": "Switch", "scope_type": "SPECIFIC_RESOURCE", "scope_resource_type": "GROUP", "scope_resource_id": str(seeded["group_id"]), "reviewer_id": str(seeded["reviewer_id"]), "due_at": future(24), "frequency_days": 30})
        edited = await client.patch(f"/api/v1/access-reviews/{created.json()['id']}", json={"schedule_day_of_month": 1, "schedule_time": "06:00", "schedule_every_months": 3, "schedule_due_days": 14})
        cleared = await client.patch(f"/api/v1/access-reviews/{created.json()['id']}", json={"clear_schedule": True})
    assert edited.status_code == 200
    assert edited.json()["frequency_days"] is None and edited.json()["schedule_every_months"] == 3 and edited.json()["next_run_at"]
    assert cleared.json()["schedule_day_of_month"] is None and cleared.json()["next_run_at"] is None


@pytest.mark.asyncio
async def test_group_owners_can_be_set_and_are_suggested_as_reviewers(db_override):
    seeded = await _seed(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        put = await client.put(f"/api/v1/groups/{seeded['group_id']}/owners", json={"user_ids": [str(seeded["reviewer_id"])]})
        listed = await client.get(f"/api/v1/groups/{seeded['group_id']}/owners")
        suggestions = await client.get(f"/api/v1/access-reviews/owner-suggestions?resource_type=GROUP&resource_id={seeded['group_id']}")
        authenticate_as("AccessPilot.User", subject="regular-user-oid")
        denied = await client.put(f"/api/v1/groups/{seeded['group_id']}/owners", json={"user_ids": []})
    assert put.status_code == 200 and [o["display_name"] for o in listed.json()] == ["Reviewer Amy"]
    assert suggestions.json()[0]["display_name"] == "Reviewer Amy" and suggestions.json()[0]["source"] == "Group owner"
    assert denied.status_code == 403


@pytest.mark.asyncio
async def test_outcomes_separate_approved_revoked_and_auto_revoked(db_override):
    """Regression: 'Access removed' used to lump reviewer revocations together with auto-revocations, so a campaign
    where items were approved still looked like everything was removed."""
    seeded = await _seed_multi_scope(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = (await client.post("/api/v1/access-reviews", json={"name": "Outcomes", "scope_type": "MULTIPLE_RESOURCES", "scope_targets": [{"resource_type": "GROUP", "resource_id": str(seeded["group_id"])}, {"resource_type": "PACKAGE", "resource_id": str(seeded["package_id"])}], "reviewer_id": str(seeded["reviewer_id"]), "due_at": future(24)})).json()
        items = (await client.get(f"/api/v1/access-reviews/{created['id']}/items")).json()
        assert len(items) == 2
        await client.post(f"/api/v1/access-reviews/items/{items[0]['id']}/decide", json={"decision": "APPROVED", "justification": "Still needed."})
        await client.post(f"/api/v1/access-reviews/{created['id']}/complete")  # the other item is auto-revoked
        campaign = (await client.get(f"/api/v1/access-reviews/{created['id']}")).json()
        dashboard = (await client.get("/api/v1/access-reviews/dashboard")).json()
    assert (campaign["approved_count"], campaign["revoked_count"], campaign["auto_revoked_count"]) == (1, 0, 1)
    assert (dashboard["approved_items"], dashboard["revoked_items"], dashboard["auto_revoked_items"], dashboard["pending_items"]) == (1, 0, 1, 0)


@pytest.mark.asyncio
async def test_keep_on_no_response_auto_approves_instead_of_revoking(db_override):
    seeded = await _seed(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/access-reviews", json={
            "name": "Keep On Silence", "scope_type": "SPECIFIC_RESOURCE", "scope_resource_type": "GROUP", "scope_resource_id": str(seeded["group_id"]),
            "reviewer_id": str(seeded["reviewer_id"]), "due_at": future(24), "on_no_response": "KEEP",
        })
        completed = await client.post(f"/api/v1/access-reviews/{created.json()['id']}/complete")
        bad = await client.post("/api/v1/access-reviews", json={"name": "Bad", "scope_type": "ALL", "reviewer_id": str(seeded["reviewer_id"]), "due_at": future(24), "on_no_response": "DELETE"})
    assert created.json()["on_no_response"] == "KEEP" and completed.status_code == 200
    async with db_override.factory() as session:
        assignment = await session.get(AccessAssignment, seeded["assignment_id"])
        item = (await session.execute(select(AccessReviewItem).where(AccessReviewItem.campaign_id == UUID(created.json()["id"])))).scalars().one()
    assert assignment.status != "REVOKED" and item.decision == "APPROVED"
    assert bad.status_code == 422
