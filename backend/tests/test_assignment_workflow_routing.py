from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import AccessAssignment, BusinessRole, BusinessRoleItem, Group, IdentityProvider, Role, User, UserGroup, WorkflowInstance
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
    """A target user whose real department is Finance (so a department-based condition can be proven to come
    from the directory record, never from anything a client could type), a group/role to grant, a Business Role
    wrapping the group, and two approvers."""
    provider = IdentityProvider(name="Directory", type="MOCK", status="CONNECTED", tenant_id="t")
    session.add(provider)
    await session.flush()
    target = User(provider_id=provider.id, external_id="target-oid", email="target@x.com", display_name="Target User", status="ACTIVE", department="Finance")
    other_dept_target = User(provider_id=provider.id, external_id="other-dept-oid", email="other@x.com", display_name="Other Dept User", status="ACTIVE", department="Engineering")
    approver = User(provider_id=provider.id, external_id="approver-oid", email="approver@x.com", display_name="Approver", status="ACTIVE")
    admin = User(provider_id=provider.id, external_id="admin-oid", email="admin@x.com", display_name="Admin", status="ACTIVE")
    group = Group(provider_id=provider.id, external_id="g1", name="Finance Group", status="ACTIVE", is_privileged=False)
    role = Role(provider_id=provider.id, external_id="r1", name="Finance Role", role_type="DIRECTORY_ROLE", status="ACTIVE")
    session.add_all([target, other_dept_target, approver, admin, group, role])
    await session.commit()
    for user in (target, other_dept_target, approver, admin, group, role):
        await session.refresh(user)
    return {"provider_id": provider.id, "target_id": target.id, "other_dept_target_id": other_dept_target.id, "approver_id": approver.id, "admin_id": admin.id, "group_id": group.id, "role_id": role.id}


def _definition_payload(ids, with_condition: bool = False):
    stage = {"name": "Manager approval", "approval_mode": "ANY_OF", "approver_user_ids": [str(ids["approver_id"])]}
    if with_condition:
        stage["condition_field"] = "department"
        stage["condition_operator"] = "EQUALS"
        stage["condition_value"] = "Finance"
    return {"name": "Assignment Workflow", "stages": [stage]}


async def _create_active_definition(client, ids, with_condition: bool = False) -> str:
    authenticate_as("AccessPilot.Admin")
    created = (await client.post("/api/v1/workflows/definitions", json=_definition_payload(ids, with_condition))).json()
    await client.patch(f"/api/v1/workflows/definitions/{created['id']}", json={"status": "ACTIVE"})
    return created["id"]


@pytest.mark.asyncio
async def test_workflow_routed_assignment_lands_pending_approval_with_no_single_approver(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        definition_id = await _create_active_definition(client, ids)
        authenticate_as("AccessPilot.Admin")
        response = await client.post("/api/v1/assignments", json={"user_id": str(ids["target_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "workflow_definition_id": definition_id, "justification": "Routed through a workflow."})
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "PENDING_APPROVAL"
    assert body["approved_by"] is None
    assert body["fallback_approver_id"] is None

    async with db_override.factory() as session:
        assignment = (await session.scalars(select(AccessAssignment).where(AccessAssignment.id == UUID(body["id"])))).first()
        assert assignment.workflow_instance_id is not None
        instance = await session.get(WorkflowInstance, assignment.workflow_instance_id)
    assert instance.subject_type == "ASSIGNMENT" and instance.subject_id == assignment.id

    # workflow_definition_name only shows up once re-fetched through a path that hydrates it (same pre-existing
    # behavior business_role_name/package_name already have — the immediate create response stays minimal).
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        authenticate_as("AccessPilot.Admin")
        listed = (await client.get("/api/v1/assignments")).json()
    refetched = next(a for a in listed if a["id"] == body["id"])
    assert refetched["workflow_definition_name"] == "Assignment Workflow"


@pytest.mark.asyncio
async def test_condition_payload_comes_from_the_real_target_users_department_column(db_override):
    """The core trust fix: the condition value is the TARGET user's real department column, never anything a
    requester could type — there is no payload field on AssignmentCreate at all, so there is no way to override it."""
    async with db_override.factory() as session:
        ids = await _seed(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        definition_id = await _create_active_definition(client, ids, with_condition=True)
        authenticate_as("AccessPilot.Admin")

        # Real Finance-department user -> the condition matches -> stage 1 is PENDING.
        matching = (await client.post("/api/v1/assignments", json={"user_id": str(ids["target_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "workflow_definition_id": definition_id, "justification": "Finance person."})).json()
        async with db_override.factory() as session:
            assignment = await session.get(AccessAssignment, UUID(matching["id"]))
            instance1 = await session.get(WorkflowInstance, assignment.workflow_instance_id)
        assert instance1.status == "PENDING"
        assert instance1.payload["department"] == "Finance"

        # Real Engineering-department user -> the condition doesn't match -> the lone stage is skipped -> the
        # whole workflow (and the assignment) auto-approves with zero human decisions.
        non_matching = (await client.post("/api/v1/assignments", json={"user_id": str(ids["other_dept_target_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "workflow_definition_id": definition_id, "justification": "Engineering person."})).json()
    assert non_matching["status"] == "ELIGIBLE"


@pytest.mark.asyncio
async def test_completing_the_workflow_approved_flips_assignment_to_eligible_never_active(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        definition_id = await _create_active_definition(client, ids)
        authenticate_as("AccessPilot.Admin")
        created = (await client.post("/api/v1/assignments", json={"user_id": str(ids["target_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "workflow_definition_id": definition_id, "justification": "Routed."})).json()

        async with db_override.factory() as session:
            assignment = await session.get(AccessAssignment, UUID(created["id"]))
            instance_id = assignment.workflow_instance_id
        authenticate_as("AccessPilot.User", subject="outsider-oid")
        pending = (await client.get("/api/v1/workflows/requests/pending-my-decision")).json()
        assert len(pending) == 0  # not authenticated as the approver yet

        authenticate_as("AccessPilot.User", subject="approver-oid")
        pending = (await client.get("/api/v1/workflows/requests/pending-my-decision")).json()
        assert len(pending) == 1
        stage_id = pending[0]["stages"][0]["id"]
        decided = (await client.post(f"/api/v1/workflows/requests/{instance_id}/stages/{stage_id}/decide", json={"decision": "APPROVED", "justification": "Looks fine."})).json()
    assert decided["instance_status"] == "APPROVED"

    async with db_override.factory() as session:
        assignment = await session.get(AccessAssignment, UUID(created["id"]))
    assert assignment.status == "ELIGIBLE"


@pytest.mark.asyncio
async def test_completing_the_workflow_rejected_flips_assignment_to_rejected(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        definition_id = await _create_active_definition(client, ids)
        authenticate_as("AccessPilot.Admin")
        created = (await client.post("/api/v1/assignments", json={"user_id": str(ids["target_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "workflow_definition_id": definition_id, "justification": "Routed."})).json()

        async with db_override.factory() as session:
            assignment = await session.get(AccessAssignment, UUID(created["id"]))
            instance_id = assignment.workflow_instance_id

        authenticate_as("AccessPilot.User", subject="approver-oid")
        pending = (await client.get("/api/v1/workflows/requests/pending-my-decision")).json()
        stage_id = pending[0]["stages"][0]["id"]
        decided = (await client.post(f"/api/v1/workflows/requests/{instance_id}/stages/{stage_id}/decide", json={"decision": "REJECTED", "justification": "Not needed."})).json()
    assert decided["instance_status"] == "REJECTED"

    async with db_override.factory() as session:
        assignment = await session.get(AccessAssignment, UUID(created["id"]))
    assert assignment.status == "REJECTED"


@pytest.mark.asyncio
async def test_cancelling_an_assignment_routed_workflow_rejects_the_assignment(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        definition_id = await _create_active_definition(client, ids)
        authenticate_as("AccessPilot.Admin")
        created = (await client.post("/api/v1/assignments", json={"user_id": str(ids["target_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "workflow_definition_id": definition_id, "justification": "Routed."})).json()
        async with db_override.factory() as session:
            assignment = await session.get(AccessAssignment, UUID(created["id"]))
            instance_id = assignment.workflow_instance_id
        cancelled = (await client.post(f"/api/v1/workflows/requests/{instance_id}/cancel")).json()
    assert cancelled["instance_status"] == "CANCELLED"
    async with db_override.factory() as session:
        assignment = await session.get(AccessAssignment, UUID(created["id"]))
    assert assignment.status == "REJECTED"


@pytest.mark.asyncio
async def test_workflow_definition_id_is_mutually_exclusive_with_approver_and_bypass(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        definition_id = await _create_active_definition(client, ids)
        authenticate_as("AccessPilot.Admin")
        with_approver = await client.post("/api/v1/assignments", json={"user_id": str(ids["target_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "workflow_definition_id": definition_id, "approver_id": str(ids["approver_id"]), "justification": "Both set."})
        with_bypass = await client.post("/api/v1/assignments", json={"user_id": str(ids["target_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "workflow_definition_id": definition_id, "bypass_activation": True, "justification": "Both set."})
    assert with_approver.status_code == 422
    assert with_bypass.status_code == 422


@pytest.mark.asyncio
async def test_a_disabled_or_unknown_workflow_is_rejected(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        authenticate_as("AccessPilot.Admin")
        created = (await client.post("/api/v1/workflows/definitions", json=_definition_payload(ids))).json()  # left DRAFT
        still_draft = await client.post("/api/v1/assignments", json={"user_id": str(ids["target_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "workflow_definition_id": created["id"], "justification": "Not active yet."})
        unknown = await client.post("/api/v1/assignments", json={"user_id": str(ids["target_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "workflow_definition_id": str(uuid4()), "justification": "Does not exist."})
    assert still_draft.status_code == 409
    assert unknown.status_code == 404


@pytest.mark.asyncio
async def test_business_role_assign_can_be_routed_through_a_workflow(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
        role = BusinessRole(name="Finance Analyst", status="ACTIVE")
        session.add(role)
        await session.flush()
        session.add(BusinessRoleItem(role_id=role.id, resource_type="GROUP", resource_id=ids["group_id"]))
        await session.commit()
        role_id = role.id
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        definition_id = await _create_active_definition(client, ids)
        authenticate_as("AccessPilot.Admin")
        assigned = (await client.post(f"/api/v1/business-roles/{role_id}/assign", json={"user_id": str(ids["target_id"]), "assignment_type": "PERMANENT", "workflow_definition_id": definition_id, "justification": "Routed via workflow."})).json()
    assert assigned["results"][0]["status"] == "CREATED"
    assert assigned["results"][0]["assignment"]["status"] == "PENDING_APPROVAL"


@pytest.mark.asyncio
async def test_package_assign_to_one_user_can_be_routed_through_a_workflow(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        definition_id = await _create_active_definition(client, ids)
        authenticate_as("AccessPilot.Admin")
        package = (await client.post("/api/v1/packages", json={"name": "Starter Kit", "items": [{"resource_type": "ROLE", "resource_id": str(ids["role_id"])}]})).json()

        assigned = (await client.post(f"/api/v1/packages/{package['id']}/assign", json={"user_id": str(ids["target_id"]), "assignment_type": "PERMANENT", "workflow_definition_id": definition_id, "justification": "Routed via workflow."})).json()
        assert assigned["members"][0]["results"][0]["assignment"]["status"] == "PENDING_APPROVAL"


@pytest.mark.asyncio
async def test_package_group_fanout_can_also_be_routed_through_a_workflow_independently_per_member(db_override):
    """Confirms group fan-out + workflow is no longer rejected — each fanned-out member gets their own
    independent WorkflowInstance, exactly the way each already gets their own independent PENDING_APPROVAL
    AccessAssignment when a single approver_id is used instead."""
    async with db_override.factory() as session:
        ids = await _seed(session)
        session.add_all([
            UserGroup(user_id=ids["target_id"], group_id=ids["group_id"], source="SYNC"),
            UserGroup(user_id=ids["other_dept_target_id"], group_id=ids["group_id"], source="SYNC"),
        ])
        await session.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        definition_id = await _create_active_definition(client, ids)
        authenticate_as("AccessPilot.Admin")
        package = (await client.post("/api/v1/packages", json={"name": "Starter Kit", "items": [{"resource_type": "ROLE", "resource_id": str(ids["role_id"])}]})).json()

        group_fanout = await client.post(f"/api/v1/packages/{package['id']}/assign", json={"group_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "workflow_definition_id": definition_id, "justification": "Routed via workflow."})
    assert group_fanout.status_code == 201
    body = group_fanout.json()
    assert len(body["members"]) == 2
    instance_ids = set()
    for member in body["members"]:
        assignment = member["results"][0]["assignment"]
        assert assignment["status"] == "PENDING_APPROVAL"
        async with db_override.factory() as session:
            row = await session.get(AccessAssignment, UUID(assignment["id"]))
            assert row.workflow_instance_id is not None
            instance_ids.add(row.workflow_instance_id)
    assert len(instance_ids) == 2  # each member's own independent instance, not one shared instance
