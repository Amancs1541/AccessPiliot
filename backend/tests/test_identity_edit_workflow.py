from uuid import UUID

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import AccessAssignment, Group, IdentityProvider, User, UserAttributeChangeRequest, WorkflowInstance
from app.providers.base import NormalizedUser
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


async def _seed(session) -> dict:
    """A real Entra-backed target user in Engineering, an approver, and two groups so a department-change's
    birthright reconciliation can be proven once the workflow is actually approved."""
    provider = IdentityProvider(name="Entra", type="ENTRA", status="CONNECTED", tenant_id="tenant-1")
    session.add(provider)
    await session.flush()
    target = User(provider_id=provider.id, external_id="target-oid", email="mover@x.com", display_name="Mover", status="ACTIVE", department="Engineering")
    approver = User(provider_id=provider.id, external_id="approver-oid", email="approver@x.com", display_name="Approver", status="ACTIVE")
    admin = User(provider_id=provider.id, external_id="admin-oid", email="admin@x.com", display_name="Admin", status="ACTIVE")
    engineering_group = Group(provider_id=provider.id, external_id="g-eng", name="Engineering Team", status="ACTIVE", is_privileged=False)
    sales_group = Group(provider_id=provider.id, external_id="g-sales", name="Sales Team", status="ACTIVE", is_privileged=False)
    session.add_all([target, approver, admin, engineering_group, sales_group])
    await session.commit()
    for row in (target, approver, admin, engineering_group, sales_group):
        await session.refresh(row)
    return {"target_id": target.id, "approver_id": approver.id, "engineering_group_id": engineering_group.id, "sales_group_id": sales_group.id}


def _definition_payload(ids):
    return {"name": "Attribute Change Workflow", "stages": [{"name": "Manager approval", "approval_mode": "ANY_OF", "approver_user_ids": [str(ids["approver_id"])]}]}


async def _create_active_definition(client, ids) -> str:
    authenticate_as("AccessPilot.Admin")
    created = (await client.post("/api/v1/workflows/definitions", json=_definition_payload(ids))).json()
    await client.patch(f"/api/v1/workflows/definitions/{created['id']}", json={"status": "ACTIVE"})
    return created["id"]


def _patch_entra_update_user(monkeypatch, seen: dict):
    async def fake_update_user(self, external_id, *, department, job_title):
        seen["called"] = True
        seen["external_id"], seen["department"], seen["job_title"] = external_id, department, job_title
        return NormalizedUser(external_id=external_id, email="mover@x.com", display_name="Mover", department=department, job_title=job_title)
    monkeypatch.setattr("app.providers.entra.EntraProvider.update_user", fake_update_user)


@pytest.mark.asyncio
async def test_workflow_routed_attribute_edit_defers_the_real_write_until_approved(db_override, monkeypatch):
    async with db_override.factory() as session:
        ids = await _seed(session)
    seen: dict = {}
    _patch_entra_update_user(monkeypatch, seen)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        definition_id = await _create_active_definition(client, ids)
        authenticate_as("AccessPilot.Admin")
        response = await client.patch(f"/api/v1/users/{ids['target_id']}/attributes", json={"department": "Sales", "workflow_definition_id": definition_id})
    assert response.status_code == 200
    body = response.json()
    assert body["pending_attribute_change"] is True
    assert body["department"] == "Engineering"  # unchanged — the edit has not applied yet
    assert "called" not in seen  # no real write to the provider yet

    async with db_override.factory() as session:
        target = await session.get(User, ids["target_id"])
        assert target.department == "Engineering"
        change_request = (await session.scalars(select(UserAttributeChangeRequest).where(UserAttributeChangeRequest.user_id == ids["target_id"]))).first()
        assert change_request is not None
        assert change_request.status == "PENDING"
        assert change_request.previous_department == "Engineering"
        assert change_request.requested_department == "Sales"
        assert change_request.workflow_instance_id is not None
        instance = await session.get(WorkflowInstance, change_request.workflow_instance_id)
        assert instance.subject_type == "USER_ATTRIBUTES" and instance.subject_id == change_request.id
        assert instance.payload["department"] == "Engineering"  # built from the CURRENT real record, not the request


@pytest.mark.asyncio
async def test_approving_the_attribute_change_applies_the_write_and_reconciles_birthright(db_override, monkeypatch):
    async with db_override.factory() as session:
        ids = await _seed(session)
    seen: dict = {}
    _patch_entra_update_user(monkeypatch, seen)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        definition_id = await _create_active_definition(client, ids)
        authenticate_as("AccessPilot.Admin")
        await client.post("/api/v1/policies/birthright", json={"name": "Engineering -> Engineering Team", "match_field": "department", "match_value": "Engineering", "resource_type": "GROUP", "resource_id": str(ids["engineering_group_id"])})
        await client.post("/api/v1/policies/birthright", json={"name": "Sales -> Sales Team", "match_field": "department", "match_value": "Sales", "resource_type": "GROUP", "resource_id": str(ids["sales_group_id"])})
        await client.post(f"/api/v1/policies/birthright/evaluate/{ids['target_id']}")

        await client.patch(f"/api/v1/users/{ids['target_id']}/attributes", json={"department": "Sales", "workflow_definition_id": definition_id})
        async with db_override.factory() as session:
            change_request = (await session.scalars(select(UserAttributeChangeRequest).where(UserAttributeChangeRequest.user_id == ids["target_id"]))).first()
            instance_id = change_request.workflow_instance_id

        authenticate_as("AccessPilot.User", subject="approver-oid")
        pending = (await client.get("/api/v1/workflows/requests/pending-my-decision")).json()
        stage_id = pending[0]["stages"][0]["id"]
        decided = (await client.post(f"/api/v1/workflows/requests/{instance_id}/stages/{stage_id}/decide", json={"decision": "APPROVED", "justification": "Looks fine."})).json()
    assert decided["instance_status"] == "APPROVED"
    assert seen["called"] and seen["department"] == "Sales"

    async with db_override.factory() as session:
        target = await session.get(User, ids["target_id"])
        assert target.department == "Sales"
        change_request = await session.get(UserAttributeChangeRequest, change_request.id)
        assert change_request.status == "APPLIED"
        assignments = (await session.execute(select(AccessAssignment).where(AccessAssignment.user_id == ids["target_id"]))).scalars().all()
        by_resource = {a.resource_id: a.status for a in assignments}
    assert by_resource[ids["engineering_group_id"]] == "REVOKED"
    assert by_resource[ids["sales_group_id"]] == "ELIGIBLE"


@pytest.mark.asyncio
async def test_rejecting_the_attribute_change_leaves_the_user_untouched(db_override, monkeypatch):
    async with db_override.factory() as session:
        ids = await _seed(session)
    seen: dict = {}
    _patch_entra_update_user(monkeypatch, seen)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        definition_id = await _create_active_definition(client, ids)
        authenticate_as("AccessPilot.Admin")
        await client.patch(f"/api/v1/users/{ids['target_id']}/attributes", json={"department": "Sales", "workflow_definition_id": definition_id})
        async with db_override.factory() as session:
            change_request = (await session.scalars(select(UserAttributeChangeRequest).where(UserAttributeChangeRequest.user_id == ids["target_id"]))).first()
            instance_id = change_request.workflow_instance_id

        authenticate_as("AccessPilot.User", subject="approver-oid")
        pending = (await client.get("/api/v1/workflows/requests/pending-my-decision")).json()
        stage_id = pending[0]["stages"][0]["id"]
        decided = (await client.post(f"/api/v1/workflows/requests/{instance_id}/stages/{stage_id}/decide", json={"decision": "REJECTED", "justification": "Not approved."})).json()
    assert decided["instance_status"] == "REJECTED"
    assert "called" not in seen  # never pushed to the provider

    async with db_override.factory() as session:
        target = await session.get(User, ids["target_id"])
        assert target.department == "Engineering"
        change_request = await session.get(UserAttributeChangeRequest, change_request.id)
        assert change_request.status == "REJECTED"


@pytest.mark.asyncio
async def test_a_disabled_or_unknown_workflow_is_rejected(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        authenticate_as("AccessPilot.Admin")
        created = (await client.post("/api/v1/workflows/definitions", json=_definition_payload(ids))).json()  # left DRAFT
        still_draft = await client.patch(f"/api/v1/users/{ids['target_id']}/attributes", json={"department": "Sales", "workflow_definition_id": created["id"]})
        unknown = await client.patch(f"/api/v1/users/{ids['target_id']}/attributes", json={"department": "Sales", "workflow_definition_id": "00000000-0000-0000-0000-000000000000"})
    assert still_draft.status_code == 409
    assert unknown.status_code == 404
