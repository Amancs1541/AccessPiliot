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
from app.models import Application, ApplicationOwner, IdentityProvider, NhiReviewItem, User, WorkflowInstance
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
    app_one = Application(provider_id=provider.id, external_id="sp-1", name="Reporting Service", status="ACTIVE", app_roles=[], nhi_type="SERVICE_PRINCIPAL")
    app_two = Application(provider_id=provider.id, external_id="sp-2", name="Billing Bot", status="ACTIVE", app_roles=[], nhi_type="BOT")
    owner = User(provider_id=provider.id, external_id="owner-oid", email="owner@x.com", display_name="Owner", status="ACTIVE")
    approver = User(provider_id=provider.id, external_id="approver-oid", email="approver@x.com", display_name="Approver", status="ACTIVE")
    admin = User(provider_id=provider.id, external_id="admin-oid", email="admin@x.com", display_name="Admin", status="ACTIVE")
    session.add_all([app_one, app_two, owner, approver, admin])
    await session.flush()
    session.add_all([ApplicationOwner(application_id=app_one.id, user_id=owner.id), ApplicationOwner(application_id=app_two.id, user_id=owner.id)])
    await session.commit()
    for row in (app_one, app_two, owner, approver, admin):
        await session.refresh(row)
    return {"app_one_id": app_one.id, "app_two_id": app_two.id, "owner_id": owner.id, "approver_id": approver.id}


def _definition_payload(ids):
    return {"name": "NHI Ownership Workflow", "stages": [{"name": "Manager approval", "approval_mode": "ANY_OF", "approver_user_ids": [str(ids["approver_id"])]}]}


async def _create_active_definition(client, ids) -> str:
    # WORKFLOW_MANAGE is an Admin-wide permission, not NHIAdmin-specific — matches how every other
    # workflow-routing test in this suite creates definitions (see test_access_review_workflow_routing.py).
    authenticate_as("AccessPilot.Admin")
    created = (await client.post("/api/v1/workflows/definitions", json=_definition_payload(ids))).json()
    await client.patch(f"/api/v1/workflows/definitions/{created['id']}", json={"status": "ACTIVE"})
    return created["id"]


async def _create_workflow_campaign(client, definition_id):
    authenticate_as("AccessPilot.NHIAdmin")
    response = await client.post("/api/v1/nhi/reviews", json={"name": "Workflow-routed NHI Review", "workflow_definition_id": definition_id, "due_at": future(24)})
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_workflow_routed_campaign_creates_one_independent_instance_per_item(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        definition_id = await _create_active_definition(client, ids)
        campaign = await _create_workflow_campaign(client, definition_id)
    assert campaign["item_count"] == 2
    assert campaign["reviewer_id"] is None
    assert campaign["workflow_definition_name"] == "NHI Ownership Workflow"

    async with db_override.factory() as session:
        items = list((await session.scalars(select(NhiReviewItem).where(NhiReviewItem.campaign_id == UUID(campaign["id"])))).all())
        assert len(items) == 2
        instance_ids = {item.workflow_instance_id for item in items}
        assert len(instance_ids) == 2 and None not in instance_ids
        for item in items:
            instance = await session.get(WorkflowInstance, item.workflow_instance_id)
            assert instance.subject_type == "NHI_REVIEW_ITEM" and instance.subject_id == item.id


@pytest.mark.asyncio
async def test_deciding_an_items_workflow_approved_confirms_without_touching_ownership(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        definition_id = await _create_active_definition(client, ids)
        campaign = await _create_workflow_campaign(client, definition_id)
        async with db_override.factory() as session:
            item = (await session.scalars(select(NhiReviewItem).where(NhiReviewItem.campaign_id == UUID(campaign["id"]), NhiReviewItem.application_id == ids["app_one_id"]))).first()
            instance_id, item_id = item.workflow_instance_id, item.id

        authenticate_as("AccessPilot.User", subject="approver-oid")
        pending = (await client.get("/api/v1/workflows/requests/pending-my-decision")).json()
        stage_id = next(p for p in pending if p["id"] == str(instance_id))["stages"][0]["id"]
        decided = (await client.post(f"/api/v1/workflows/requests/{instance_id}/stages/{stage_id}/decide", json={"decision": "APPROVED", "justification": "Still needed."})).json()
    assert decided["instance_status"] == "APPROVED"

    async with db_override.factory() as session:
        item = await session.get(NhiReviewItem, item_id)
        owners = (await session.scalars(select(ApplicationOwner).where(ApplicationOwner.application_id == ids["app_one_id"]))).all()
    assert item.decision == "CONFIRMED"
    assert len(owners) == 1  # certifying existing ownership never touches it


@pytest.mark.asyncio
async def test_deciding_an_items_workflow_rejected_removes_the_real_ownership(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        definition_id = await _create_active_definition(client, ids)
        campaign = await _create_workflow_campaign(client, definition_id)
        async with db_override.factory() as session:
            item = (await session.scalars(select(NhiReviewItem).where(NhiReviewItem.campaign_id == UUID(campaign["id"]), NhiReviewItem.application_id == ids["app_one_id"]))).first()
            instance_id, item_id = item.workflow_instance_id, item.id

        authenticate_as("AccessPilot.User", subject="approver-oid")
        pending = (await client.get("/api/v1/workflows/requests/pending-my-decision")).json()
        stage_id = next(p for p in pending if p["id"] == str(instance_id))["stages"][0]["id"]
        decided = (await client.post(f"/api/v1/workflows/requests/{instance_id}/stages/{stage_id}/decide", json={"decision": "REJECTED", "justification": "No longer needed."})).json()
    assert decided["instance_status"] == "REJECTED"

    async with db_override.factory() as session:
        item = await session.get(NhiReviewItem, item_id)
        owners = (await session.scalars(select(ApplicationOwner).where(ApplicationOwner.application_id == ids["app_one_id"]))).all()
    assert item.decision == "OWNER_REMOVED"
    assert owners == []


@pytest.mark.asyncio
async def test_the_direct_decide_endpoint_rejects_a_workflow_routed_item(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        definition_id = await _create_active_definition(client, ids)
        campaign = await _create_workflow_campaign(client, definition_id)
        items = (await client.get(f"/api/v1/nhi/reviews/{campaign['id']}/items")).json()
        authenticate_as("AccessPilot.NHIAdmin")
        rejected = await client.post(f"/api/v1/nhi/reviews/items/{items[0]['id']}/decide", json={"decision": "CONFIRMED"})
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "NHI_REVIEW_ITEM_ROUTED_THROUGH_WORKFLOW"


@pytest.mark.asyncio
async def test_reviewer_and_workflow_are_mutually_exclusive_and_one_is_required(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        definition_id = await _create_active_definition(client, ids)
        authenticate_as("AccessPilot.NHIAdmin")
        both = await client.post("/api/v1/nhi/reviews", json={"name": "Both", "reviewer_id": str(ids["approver_id"]), "workflow_definition_id": definition_id, "due_at": future(24)})
        neither = await client.post("/api/v1/nhi/reviews", json={"name": "Neither", "due_at": future(24)})
        workflow_with_fallback = await client.post("/api/v1/nhi/reviews", json={"name": "WF+Fallback", "workflow_definition_id": definition_id, "fallback_reviewer_id": str(ids["approver_id"]), "due_at": future(24)})
    assert both.status_code == 422
    assert neither.status_code == 422
    assert workflow_with_fallback.status_code == 422


@pytest.mark.asyncio
async def test_a_reviewer_routed_campaign_still_works_unaffected(db_override):
    """Regression guard: adding workflow routing must not break the original, simpler reviewer/fallback path."""
    async with db_override.factory() as session:
        ids = await _seed(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        authenticate_as("AccessPilot.NHIAdmin")
        created = await client.post("/api/v1/nhi/reviews", json={"name": "Plain reviewer review", "reviewer_id": str(ids["approver_id"]), "due_at": future(24)})
        assert created.status_code == 201
        campaign = created.json()
        assert campaign["workflow_definition_id"] is None
        items = (await client.get(f"/api/v1/nhi/reviews/{campaign['id']}/items")).json()
        authenticate_as("AccessPilot.User", subject="approver-oid")
        decided = await client.post(f"/api/v1/nhi/reviews/items/{items[0]['id']}/decide", json={"decision": "CONFIRMED"})
    assert decided.status_code == 200
    assert decided.json()["decision"] == "CONFIRMED"
