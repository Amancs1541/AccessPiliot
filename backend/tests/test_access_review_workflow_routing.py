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
from app.models import AccessAssignment, AccessReviewCampaign, AccessReviewItem, Group, IdentityProvider, User, WorkflowInstance
from app.security.auth import AuthenticatedUser, require_authenticated_user


class TestSession:
    __test__ = False

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
        return AuthenticatedUser(subject, "Someone", "someone@example.com", "tenant", (role,), {})
    app.dependency_overrides[require_authenticated_user] = dependency


def future(hours: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


async def _seed(session) -> dict:
    provider = IdentityProvider(name="Directory", type="MOCK", status="CONNECTED", tenant_id="t")
    session.add(provider)
    await session.flush()
    group = Group(provider_id=provider.id, external_id="g1", name="Finance Team", status="ACTIVE", is_privileged=False)
    member_one = User(provider_id=provider.id, external_id="member-1-oid", email="m1@x.com", display_name="Member One", status="ACTIVE", department="Finance")
    member_two = User(provider_id=provider.id, external_id="member-2-oid", email="m2@x.com", display_name="Member Two", status="ACTIVE", department="Finance")
    approver = User(provider_id=provider.id, external_id="approver-oid", email="approver@x.com", display_name="Approver", status="ACTIVE")
    admin = User(provider_id=provider.id, external_id="admin-oid", email="admin@x.com", display_name="Admin", status="ACTIVE")
    session.add_all([group, member_one, member_two, approver, admin])
    await session.flush()
    assignment_one = AccessAssignment(provider_id=provider.id, user_id=member_one.id, resource_type="GROUP", resource_id=group.id, assignment_type="PERMANENT", status="ACTIVE", justification="Existing access.")
    assignment_two = AccessAssignment(provider_id=provider.id, user_id=member_two.id, resource_type="GROUP", resource_id=group.id, assignment_type="PERMANENT", status="ACTIVE", justification="Existing access.")
    session.add_all([assignment_one, assignment_two])
    await session.commit()
    for row in (group, member_one, member_two, approver, admin, assignment_one, assignment_two):
        await session.refresh(row)
    return {"group_id": group.id, "member_one_id": member_one.id, "member_two_id": member_two.id, "approver_id": approver.id, "assignment_one_id": assignment_one.id, "assignment_two_id": assignment_two.id}


def _definition_payload(ids):
    return {"name": "Review Workflow", "stages": [{"name": "Manager approval", "approval_mode": "ANY_OF", "approver_user_ids": [str(ids["approver_id"])]}]}


async def _create_active_definition(client, ids) -> str:
    authenticate_as("AccessPilot.Admin")
    created = (await client.post("/api/v1/workflows/definitions", json=_definition_payload(ids))).json()
    await client.patch(f"/api/v1/workflows/definitions/{created['id']}", json={"status": "ACTIVE"})
    return created["id"]


async def _create_workflow_campaign(client, ids, definition_id, on_no_response="REVOKE"):
    authenticate_as("AccessPilot.Admin")
    response = await client.post("/api/v1/access-reviews", json={
        "name": "Workflow-routed Review", "scope_type": "SPECIFIC_RESOURCE", "scope_resource_type": "GROUP", "scope_resource_id": str(ids["group_id"]),
        "workflow_definition_id": definition_id, "due_at": future(24), "on_no_response": on_no_response,
    })
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_workflow_routed_campaign_creates_one_independent_instance_per_item(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        definition_id = await _create_active_definition(client, ids)
        campaign = await _create_workflow_campaign(client, ids, definition_id)
    assert campaign["item_count"] == 2
    assert campaign["reviewer_id"] is None
    assert campaign["workflow_definition_name"] == "Review Workflow"

    async with db_override.factory() as session:
        items = list((await session.scalars(select(AccessReviewItem).where(AccessReviewItem.campaign_id == UUID(campaign["id"])))).all())
        assert len(items) == 2
        instance_ids = {item.workflow_instance_id for item in items}
        assert len(instance_ids) == 2 and None not in instance_ids
        for item in items:
            instance = await session.get(WorkflowInstance, item.workflow_instance_id)
            assert instance.subject_type == "ACCESS_REVIEW_ITEM" and instance.subject_id == item.id
            assert instance.payload["department"] == "Finance"


@pytest.mark.asyncio
async def test_deciding_an_items_workflow_approved_certifies_without_touching_the_assignment(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        definition_id = await _create_active_definition(client, ids)
        campaign = await _create_workflow_campaign(client, ids, definition_id)
        async with db_override.factory() as session:
            item = (await session.scalars(select(AccessReviewItem).where(AccessReviewItem.campaign_id == UUID(campaign["id"]), AccessReviewItem.assignment_id == ids["assignment_one_id"]))).first()
            instance_id = item.workflow_instance_id

        authenticate_as("AccessPilot.User", subject="approver-oid")
        pending = (await client.get("/api/v1/workflows/requests/pending-my-decision")).json()
        stage_id = next(p for p in pending if p["id"] == str(instance_id))["stages"][0]["id"]
        decided = (await client.post(f"/api/v1/workflows/requests/{instance_id}/stages/{stage_id}/decide", json={"decision": "APPROVED", "justification": "Still needed."})).json()
    assert decided["instance_status"] == "APPROVED"

    async with db_override.factory() as session:
        item = await session.get(AccessReviewItem, item.id)
        assignment = await session.get(AccessAssignment, ids["assignment_one_id"])
    assert item.decision == "APPROVED"
    assert assignment.status == "ACTIVE"  # certifying existing access never touches the grant


@pytest.mark.asyncio
async def test_deciding_an_items_workflow_rejected_revokes_the_real_assignment(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        definition_id = await _create_active_definition(client, ids)
        campaign = await _create_workflow_campaign(client, ids, definition_id)
        async with db_override.factory() as session:
            item = (await session.scalars(select(AccessReviewItem).where(AccessReviewItem.campaign_id == UUID(campaign["id"]), AccessReviewItem.assignment_id == ids["assignment_one_id"]))).first()
            instance_id = item.workflow_instance_id

        authenticate_as("AccessPilot.User", subject="approver-oid")
        pending = (await client.get("/api/v1/workflows/requests/pending-my-decision")).json()
        stage_id = next(p for p in pending if p["id"] == str(instance_id))["stages"][0]["id"]
        decided = (await client.post(f"/api/v1/workflows/requests/{instance_id}/stages/{stage_id}/decide", json={"decision": "REJECTED", "justification": "No longer needed."})).json()
    assert decided["instance_status"] == "REJECTED"

    async with db_override.factory() as session:
        item = await session.get(AccessReviewItem, item.id)
        assignment = await session.get(AccessAssignment, ids["assignment_one_id"])
    assert item.decision == "REVOKED"
    assert assignment.status == "REVOKED"


@pytest.mark.asyncio
async def test_the_old_decide_endpoint_rejects_a_workflow_routed_item(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        definition_id = await _create_active_definition(client, ids)
        campaign = await _create_workflow_campaign(client, ids, definition_id)
        items = (await client.get(f"/api/v1/access-reviews/{campaign['id']}/items")).json()
        authenticate_as("AccessPilot.Admin")
        rejected = await client.post(f"/api/v1/access-reviews/items/{items[0]['id']}/decide", json={"decision": "APPROVED", "justification": "Should not work."})
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "ACCESS_REVIEW_ITEM_ROUTED_THROUGH_WORKFLOW"


@pytest.mark.asyncio
async def test_reviewer_and_workflow_are_mutually_exclusive_and_one_is_required(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        definition_id = await _create_active_definition(client, ids)
        authenticate_as("AccessPilot.Admin")
        both = await client.post("/api/v1/access-reviews", json={"name": "Both", "scope_type": "SPECIFIC_RESOURCE", "scope_resource_type": "GROUP", "scope_resource_id": str(ids["group_id"]), "reviewer_id": str(ids["approver_id"]), "workflow_definition_id": definition_id, "due_at": future(24)})
        neither = await client.post("/api/v1/access-reviews", json={"name": "Neither", "scope_type": "SPECIFIC_RESOURCE", "scope_resource_type": "GROUP", "scope_resource_id": str(ids["group_id"]), "due_at": future(24)})
        workflow_with_fallback = await client.post("/api/v1/access-reviews", json={"name": "WF+Fallback", "scope_type": "SPECIFIC_RESOURCE", "scope_resource_type": "GROUP", "scope_resource_id": str(ids["group_id"]), "workflow_definition_id": definition_id, "fallback_reviewer_id": str(ids["approver_id"]), "due_at": future(24)})
    assert both.status_code == 422
    assert neither.status_code == 422
    assert workflow_with_fallback.status_code == 422


@pytest.mark.asyncio
async def test_complete_campaign_resolves_open_item_workflows_per_on_no_response(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        definition_id = await _create_active_definition(client, ids)
        campaign = await _create_workflow_campaign(client, ids, definition_id, on_no_response="REVOKE")
        authenticate_as("AccessPilot.Admin")
        completed = await client.post(f"/api/v1/access-reviews/{campaign['id']}/complete")
    assert completed.status_code == 200
    assert completed.json()["status"] == "COMPLETED"

    async with db_override.factory() as session:
        items = list((await session.scalars(select(AccessReviewItem).where(AccessReviewItem.campaign_id == UUID(campaign["id"])))).all())
        assignment_one = await session.get(AccessAssignment, ids["assignment_one_id"])
        assignment_two = await session.get(AccessAssignment, ids["assignment_two_id"])
    assert all(item.decision == "AUTO_REVOKED" for item in items)
    assert assignment_one.status == "REVOKED" and assignment_two.status == "REVOKED"


@pytest.mark.asyncio
async def test_complete_campaign_keeps_access_when_on_no_response_is_keep(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        definition_id = await _create_active_definition(client, ids)
        campaign = await _create_workflow_campaign(client, ids, definition_id, on_no_response="KEEP")
        authenticate_as("AccessPilot.Admin")
        completed = await client.post(f"/api/v1/access-reviews/{campaign['id']}/complete")
    assert completed.status_code == 200

    async with db_override.factory() as session:
        items = list((await session.scalars(select(AccessReviewItem).where(AccessReviewItem.campaign_id == UUID(campaign["id"])))).all())
        assignment_one = await session.get(AccessAssignment, ids["assignment_one_id"])
        assignment_two = await session.get(AccessAssignment, ids["assignment_two_id"])
    assert all(item.decision == "APPROVED" for item in items)
    assert assignment_one.status == "ACTIVE" and assignment_two.status == "ACTIVE"


@pytest.mark.asyncio
async def test_updating_reviewer_fields_is_rejected_on_a_workflow_routed_campaign(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        definition_id = await _create_active_definition(client, ids)
        campaign = await _create_workflow_campaign(client, ids, definition_id)
        authenticate_as("AccessPilot.Admin")
        rejected = await client.patch(f"/api/v1/access-reviews/{campaign['id']}", json={"reviewer_id": str(ids["approver_id"])})
    assert rejected.status_code == 422
