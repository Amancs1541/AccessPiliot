from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import AccessAssignment, Group, IdentityProvider, Role, User
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
    provider = IdentityProvider(name="Directory", type="MOCK", status="CONNECTED", tenant_id="t")
    session.add(provider)
    await session.flush()
    target = User(provider_id=provider.id, external_id="target-user", email="target@x.com", display_name="Target User", status="ACTIVE")
    approver = User(provider_id=provider.id, external_id="approver-oid", email="approver@x.com", display_name="Approver", status="ACTIVE")
    admin = User(provider_id=provider.id, external_id="admin-oid", email="admin@x.com", display_name="Admin", status="ACTIVE")
    role = Role(provider_id=provider.id, external_id="r1", name="Reports Reader", role_type="DIRECTORY_ROLE", status="ACTIVE", is_privileged=False)
    role2 = Role(provider_id=provider.id, external_id="r2", name="Billing Viewer", role_type="DIRECTORY_ROLE", status="ACTIVE", is_privileged=False)
    session.add_all([target, approver, admin, role, role2])
    await session.commit()
    for row in (target, approver, admin, role, role2):
        await session.refresh(row)
    return {"user_id": target.id, "approver_id": approver.id, "admin_id": admin.id, "role_id": role.id, "role2_id": role2.id}


def _definition_payload(ids):
    return {"name": "Package Self-Service Workflow", "stages": [{"name": "Manager approval", "approval_mode": "ANY_OF", "approver_user_ids": [str(ids["approver_id"])]}]}


async def _create_active_definition(client, ids) -> str:
    authenticate_as("AccessPilot.Admin")
    created = (await client.post("/api/v1/workflows/definitions", json=_definition_payload(ids))).json()
    await client.patch(f"/api/v1/workflows/definitions/{created['id']}", json={"status": "ACTIVE"})
    return created["id"]


@pytest.mark.asyncio
async def test_package_with_a_default_workflow_routes_a_self_service_request_through_it(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        definition_id = await _create_active_definition(client, ids)
        authenticate_as("AccessPilot.Admin")
        package = (await client.post("/api/v1/packages", json={"name": "Starter Kit", "items": [{"resource_type": "ROLE", "resource_id": str(ids["role_id"])}], "workflow_definition_id": definition_id})).json()
        assert package["workflow_definition_name"] == "Package Self-Service Workflow"
        await client.put(f"/api/v1/packages/{package['id']}/eligibility", json={"principals": [{"principal_type": "USER", "principal_id": str(ids["user_id"])}], "workflow_definition_id": definition_id})

        authenticate_as("AccessPilot.User", subject="target-user")
        requested = (await client.post(f"/api/v1/packages/{package['id']}/request", json={"assignment_type": "PERMANENT", "justification": "Need it."})).json()
    assert requested["results"][0]["assignment"]["status"] == "PENDING_APPROVAL"
    assert requested["results"][0]["assignment"]["approved_by"] is None

    async with db_override.factory() as session:
        assignment = await session.get(AccessAssignment, UUID(requested["results"][0]["assignment"]["id"]))
        assert assignment.workflow_instance_id is not None


@pytest.mark.asyncio
async def test_a_multi_item_package_requests_share_a_batch_id_on_workflow_requests(db_override):
    """Each item of a multi-item package gets its own independent WorkflowInstance (so they can progress at their
    own pace), but the Workflow Requests page needs to bundle them — confirms they share a batch_id/batch_label
    the frontend can group on."""
    async with db_override.factory() as session:
        ids = await _seed(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        definition_id = await _create_active_definition(client, ids)
        authenticate_as("AccessPilot.Admin")
        package = (await client.post("/api/v1/packages", json={"name": "Two Item Kit", "items": [{"resource_type": "ROLE", "resource_id": str(ids["role_id"])}, {"resource_type": "ROLE", "resource_id": str(ids["role2_id"])}], "workflow_definition_id": definition_id})).json()
        await client.put(f"/api/v1/packages/{package['id']}/eligibility", json={"principals": [{"principal_type": "USER", "principal_id": str(ids["user_id"])}], "workflow_definition_id": definition_id})

        authenticate_as("AccessPilot.User", subject="target-user")
        await client.post(f"/api/v1/packages/{package['id']}/request", json={"assignment_type": "PERMANENT", "justification": "Need both."})

        authenticate_as("AccessPilot.User", subject="approver-oid")
        pending = (await client.get("/api/v1/workflows/requests/pending-my-decision")).json()
    assert len(pending) == 2
    batch_ids = {p["batch_id"] for p in pending}
    assert len(batch_ids) == 1 and None not in batch_ids
    assert all(p["batch_label"] == "📦 Two Item Kit" for p in pending)


@pytest.mark.asyncio
async def test_package_level_workflow_and_default_approver_are_mutually_exclusive(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        definition_id = await _create_active_definition(client, ids)
        authenticate_as("AccessPilot.Admin")
        rejected = await client.post("/api/v1/packages", json={"name": "Starter Kit", "items": [{"resource_type": "ROLE", "resource_id": str(ids["role_id"])}], "workflow_definition_id": definition_id, "default_approver_id": str(ids["approver_id"])})
    assert rejected.status_code == 422


@pytest.mark.asyncio
async def test_group_fanout_assign_with_workflow_is_no_longer_rejected_and_grants_independent_instances(db_override):
    from app.models import UserGroup
    async with db_override.factory() as session:
        ids = await _seed(session)
        group = Group(provider_id=(await session.get(User, ids["user_id"])).provider_id, external_id="g1", name="Fanout Group", status="ACTIVE", is_privileged=False)
        session.add(group)
        await session.flush()
        session.add(UserGroup(user_id=ids["user_id"], group_id=group.id, source="SYNC"))
        session.add(UserGroup(user_id=ids["approver_id"], group_id=group.id, source="SYNC"))
        await session.commit()
        ids["group_id"] = group.id
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        definition_id = await _create_active_definition(client, ids)
        authenticate_as("AccessPilot.Admin")
        package = (await client.post("/api/v1/packages", json={"name": "Starter Kit", "items": [{"resource_type": "ROLE", "resource_id": str(ids["role_id"])}]})).json()
        assigned = await client.post(f"/api/v1/packages/{package['id']}/assign", json={"group_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "workflow_definition_id": definition_id, "justification": "Routed via workflow."})
    assert assigned.status_code == 201
    body = assigned.json()
    assert len(body["members"]) == 2
    assert all(member["results"][0]["assignment"]["status"] == "PENDING_APPROVAL" for member in body["members"])


@pytest.mark.asyncio
async def test_a_disabled_or_unknown_package_workflow_is_rejected(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        authenticate_as("AccessPilot.Admin")
        created = (await client.post("/api/v1/workflows/definitions", json=_definition_payload(ids))).json()  # left DRAFT
        still_draft = await client.post("/api/v1/packages", json={"name": "Starter Kit", "items": [{"resource_type": "ROLE", "resource_id": str(ids["role_id"])}], "workflow_definition_id": created["id"]})
        unknown = await client.post("/api/v1/packages", json={"name": "Starter Kit 2", "items": [{"resource_type": "ROLE", "resource_id": str(ids["role_id"])}], "workflow_definition_id": str(uuid4())})
    assert still_draft.status_code == 409
    assert unknown.status_code == 404
