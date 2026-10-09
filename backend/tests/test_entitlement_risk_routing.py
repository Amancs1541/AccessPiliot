from uuid import UUID

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import Group, IdentityProvider, Role, User
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
        return AuthenticatedUser(subject, "Someone", "someone@example.com", "tenant", (role,), {})
    app.dependency_overrides[require_authenticated_user] = dependency


async def _seed(factory) -> dict:
    async with factory() as session:
        provider = IdentityProvider(name="Directory", type="MOCK", status="CONNECTED", tenant_id="t")
        session.add(provider)
        await session.flush()
        target = User(provider_id=provider.id, external_id="target-user", email="target@x.com", display_name="Target User", status="ACTIVE")
        approver = User(provider_id=provider.id, external_id="approver-oid", email="approver@x.com", display_name="Approver", status="ACTIVE")
        risky_group = Group(provider_id=provider.id, external_id="g-risky", name="Global Admins", status="ACTIVE", is_privileged=True)
        safe_role = Role(provider_id=provider.id, external_id="r-safe", name="Reports Reader", role_type="DIRECTORY_ROLE", status="ACTIVE", is_privileged=False)
        session.add_all([target, approver, risky_group, safe_role])
        await session.commit()
        return {"user_id": target.id, "approver_id": approver.id, "risky_group_id": risky_group.id, "safe_role_id": safe_role.id}


async def _set_risk_tier(client, resource_id: UUID, tier: str):
    authenticate_as("AccessPilot.Admin")
    catalog = (await client.get("/api/v1/entitlement-catalog")).json()
    entry = next(e for e in catalog if e["resource_id"] == str(resource_id))
    response = await client.patch(f"/api/v1/entitlement-catalog/{entry['id']}", json={"risk_tier": tier})
    assert response.status_code == 200
    return entry["id"]


def _workflow_payload(approver_id):
    return {"name": "High-Risk Safety Net", "stages": [{"name": "Approval", "approval_mode": "ANY_OF", "approver_user_ids": [str(approver_id)]}]}


async def _active_workflow(client, approver_id) -> str:
    authenticate_as("AccessPilot.Admin")
    created = (await client.post("/api/v1/workflows/definitions", json=_workflow_payload(approver_id))).json()
    await client.patch(f"/api/v1/workflows/definitions/{created['id']}", json={"status": "ACTIVE"})
    return created["id"]


@pytest.mark.asyncio
async def test_admin_assign_package_with_a_critical_item_is_blocked_without_a_workflow(db_override):
    ids = await _seed(db_override.factory)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await _set_risk_tier(client, ids["risky_group_id"], "CRITICAL")
        authenticate_as("AccessPilot.Admin")
        package = (await client.post("/api/v1/packages", json={"name": "Risky Kit", "items": [{"resource_type": "GROUP", "resource_id": str(ids["risky_group_id"])}]})).json()
        blocked = await client.post(f"/api/v1/packages/{package['id']}/assign", json={"user_id": str(ids["user_id"]), "assignment_type": "PERMANENT", "justification": "Need it."})
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "WORKFLOW_REQUIRED_FOR_HIGH_RISK_ITEM"


@pytest.mark.asyncio
async def test_admin_assign_package_with_a_critical_item_succeeds_once_a_workflow_is_attached(db_override):
    ids = await _seed(db_override.factory)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await _set_risk_tier(client, ids["risky_group_id"], "CRITICAL")
        definition_id = await _active_workflow(client, ids["approver_id"])
        authenticate_as("AccessPilot.Admin")
        package = (await client.post("/api/v1/packages", json={"name": "Risky Kit", "items": [{"resource_type": "GROUP", "resource_id": str(ids["risky_group_id"])}]})).json()
        allowed = await client.post(f"/api/v1/packages/{package['id']}/assign", json={"user_id": str(ids["user_id"]), "assignment_type": "PERMANENT", "justification": "Need it.", "workflow_definition_id": definition_id})
    assert allowed.status_code == 201
    assert allowed.json()["members"][0]["results"][0]["status"] == "CREATED"


@pytest.mark.asyncio
async def test_a_low_risk_package_item_is_never_blocked(db_override):
    """Unclassified/LOW items (the default for every entitlement until an admin rates it) never require a workflow."""
    ids = await _seed(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        package = (await client.post("/api/v1/packages", json={"name": "Safe Kit", "items": [{"resource_type": "ROLE", "resource_id": str(ids["safe_role_id"])}]})).json()
        allowed = await client.post(f"/api/v1/packages/{package['id']}/assign", json={"user_id": str(ids["user_id"]), "assignment_type": "PERMANENT", "justification": "Need it."})
    assert allowed.status_code == 201
    assert allowed.json()["members"][0]["results"][0]["status"] == "CREATED"


@pytest.mark.asyncio
async def test_self_service_package_request_with_a_high_risk_item_is_blocked_without_a_workflow(db_override):
    ids = await _seed(db_override.factory)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await _set_risk_tier(client, ids["risky_group_id"], "HIGH")
        authenticate_as("AccessPilot.Admin")
        package = (await client.post("/api/v1/packages", json={"name": "Risky Self-Service Kit", "items": [{"resource_type": "GROUP", "resource_id": str(ids["risky_group_id"])}]})).json()
        await client.put(f"/api/v1/packages/{package['id']}/eligibility", json={"principals": [{"principal_type": "USER", "principal_id": str(ids["user_id"])}]})

        authenticate_as("AccessPilot.User", subject="target-user")
        blocked = await client.post(f"/api/v1/packages/{package['id']}/request", json={"assignment_type": "PERMANENT", "justification": "Need it."})
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "WORKFLOW_REQUIRED_FOR_HIGH_RISK_ITEM"


@pytest.mark.asyncio
async def test_business_role_assign_with_a_critical_item_is_blocked_without_a_workflow(db_override):
    ids = await _seed(db_override.factory)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await _set_risk_tier(client, ids["risky_group_id"], "CRITICAL")
        authenticate_as("AccessPilot.Admin")
        role = (await client.post("/api/v1/business-roles", json={"name": "Risky Role", "role_type": "BUSINESS", "items": [{"resource_type": "GROUP", "resource_id": str(ids["risky_group_id"])}]})).json()
        await client.patch(f"/api/v1/business-roles/{role['id']}", json={"status": "ACTIVE"})
        blocked = await client.post(f"/api/v1/business-roles/{role['id']}/assign", json={"user_id": str(ids["user_id"]), "assignment_type": "PERMANENT", "justification": "Need it."})
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "WORKFLOW_REQUIRED_FOR_HIGH_RISK_ITEM"
