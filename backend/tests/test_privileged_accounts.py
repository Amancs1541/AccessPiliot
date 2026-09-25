from uuid import UUID

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import AccessAssignment, IdentityProvider, PrivilegedAccountRequest, Role, User
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


async def _seed(factory):
    async with factory() as session:
        provider = IdentityProvider(name="Directory", type="MOCK", status="CONNECTED", tenant_id="t", provisioning_domain="example.com")
        session.add(provider)
        await session.flush()
        requester = User(provider_id=provider.id, external_id="requester-oid", email="jane.doe@example.com", display_name="Jane Doe", given_name="Jane", surname="Doe", status="ACTIVE")
        approver = User(provider_id=provider.id, external_id="approver-oid", email="approver@example.com", display_name="Approver Amy", status="ACTIVE")
        role = Role(provider_id=provider.id, external_id="role-001", name="Production Administrator", role_type="DIRECTORY_ROLE", status="ACTIVE")
        session.add_all([requester, approver, role])
        await session.commit()
        await session.refresh(requester)
        await session.refresh(approver)
        await session.refresh(role)
        return {"provider_id": provider.id, "requester_id": requester.id, "approver_id": approver.id, "role_id": role.id}


@pytest.mark.asyncio
async def test_request_with_no_approver_configured_auto_provisions_immediately(db_override):
    seeded = await _seed(db_override.factory)
    authenticate_as("AccessPilot.User", subject="requester-oid")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/privileged-accounts/requests", json={"account_type": "PU", "justification": "Need elevated access for infra work."})
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "PROVISIONED"
    assert body["provisioned_user_id"] is not None

    async with db_override.factory() as session:
        provisioned = await session.get(User, UUID(body["provisioned_user_id"]))
    assert provisioned.account_type == "PU"
    assert provisioned.linked_user_id == seeded["requester_id"]
    assert provisioned.email.lower().startswith("pu_")


@pytest.mark.asyncio
async def test_request_with_approver_configured_stays_pending_until_approved(db_override):
    seeded = await _seed(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        policy = await client.patch("/api/v1/privileged-accounts/policy/TU", json={"default_approver_id": str(seeded["approver_id"])})
        assert policy.json()["approval_required"] is True

        authenticate_as("AccessPilot.User", subject="requester-oid")
        created = await client.post("/api/v1/privileged-accounts/requests", json={"account_type": "TU", "justification": "Need a test account for UAT."})
        assert created.json()["status"] == "PENDING_APPROVAL"
        request_id = created.json()["id"]

        authenticate_as("AccessPilot.User", subject="approver-oid")
        approved = await client.post(f"/api/v1/privileged-accounts/requests/{request_id}/approve")
    assert approved.status_code == 200
    assert approved.json()["status"] == "PROVISIONED"


@pytest.mark.asyncio
async def test_a_non_approver_non_admin_cannot_approve(db_override):
    seeded = await _seed(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.patch("/api/v1/privileged-accounts/policy/TU", json={"default_approver_id": str(seeded["approver_id"])})
        authenticate_as("AccessPilot.User", subject="requester-oid")
        created = await client.post("/api/v1/privileged-accounts/requests", json={"account_type": "TU", "justification": "Need a test account."})
        request_id = created.json()["id"]

        authenticate_as("AccessPilot.User", subject="someone-else-oid")
        response = await client.post(f"/api/v1/privileged-accounts/requests/{request_id}/approve")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_rejecting_a_request_never_provisions_anything(db_override):
    seeded = await _seed(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.patch("/api/v1/privileged-accounts/policy/PU", json={"default_approver_id": str(seeded["approver_id"])})
        authenticate_as("AccessPilot.User", subject="requester-oid")
        created = await client.post("/api/v1/privileged-accounts/requests", json={"account_type": "PU", "justification": "Need elevated access."})
        request_id = created.json()["id"]

        authenticate_as("AccessPilot.User", subject="approver-oid")
        rejected = await client.post(f"/api/v1/privileged-accounts/requests/{request_id}/reject", json={"justification": "Not justified."})
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "REJECTED"
    assert rejected.json()["provisioned_user_id"] is None


@pytest.mark.asyncio
async def test_cannot_grant_access_to_an_unassociated_pu_account(db_override):
    """The association gate: an Admin-created PU/TU row with no linked_user_id must be refused any assignment."""
    seeded = await _seed(db_override.factory)
    async with db_override.factory() as session:
        orphan = User(provider_id=seeded["provider_id"], external_id="orphan-pu", email="pu_orphan@example.com", display_name="Orphan PU", status="ACTIVE", account_type="PU")
        session.add(orphan)
        await session.commit()
        await session.refresh(orphan)
        orphan_id = orphan.id

    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/assignments", json={"user_id": str(orphan_id), "resource_type": "ROLE", "resource_id": str(seeded["provider_id"]), "assignment_type": "PERMANENT", "justification": "Trying to grant an unassociated account.", "bypass_activation": True})
    assert response.status_code in (404, 409)


@pytest.mark.asyncio
async def test_birthright_policies_skip_pu_tu_accounts(db_override):
    from app.services.birthright import evaluate_birthright_policies

    seeded = await _seed(db_override.factory)
    async with db_override.factory() as session:
        pu_account = User(provider_id=seeded["provider_id"], external_id="pu-1", email="pu_jane@example.com", display_name="Jane Doe PU", department="IT", status="ACTIVE", account_type="PU", linked_user_id=seeded["requester_id"])
        session.add(pu_account)
        await session.commit()
        await session.refresh(pu_account)
        pu_id = pu_account.id

        created = await evaluate_birthright_policies(session, pu_id, "system:test", "req-1")
    assert created == []


@pytest.mark.asyncio
async def test_set_account_enabled_flips_status_and_rejects_normal_accounts(db_override):
    seeded = await _seed(db_override.factory)
    async with db_override.factory() as session:
        pu_account = User(provider_id=seeded["provider_id"], external_id="user-001", email="pu_jane2@example.com", display_name="Jane Doe PU", status="ACTIVE", account_type="PU", linked_user_id=seeded["requester_id"])
        session.add(pu_account)
        await session.commit()
        await session.refresh(pu_account)
        pu_id = pu_account.id
        requester_id = seeded["requester_id"]

    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        disabled = await client.post(f"/api/v1/users/{pu_id}/enabled", json={"enabled": False})
        assert disabled.status_code == 200
        assert disabled.json()["status"] == "DISABLED"

        rejected = await client.post(f"/api/v1/users/{requester_id}/enabled", json={"enabled": False})
    assert rejected.status_code == 400


@pytest.mark.asyncio
async def test_provisioning_uses_pu_tu_prefixed_display_name(db_override):
    """The naming convention the diagram asked for: PU_<name>/TU_<name>, matching the UPN's local part — not the
    older '<name> — Privileged Account' phrasing."""
    seeded = await _seed(db_override.factory)
    authenticate_as("AccessPilot.User", subject="requester-oid")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/privileged-accounts/requests", json={"account_type": "TU", "justification": "Need a test account."})
    body = response.json()
    async with db_override.factory() as session:
        provisioned = await session.get(User, UUID(body["provisioned_user_id"]))
    assert provisioned.display_name == "TU_Jane Doe"


@pytest.mark.asyncio
async def test_activity_summary_lists_pu_tu_accounts_with_event_counts_and_unknown_sign_in(db_override):
    """MOCK isn't EntraProvider, so sign-in data must come back as genuinely unknown (sign_in_data_available
    False, not a false 'never signed in'), while the AccessPilot-recorded event count/last-activity are real."""
    seeded = await _seed(db_override.factory)
    authenticate_as("AccessPilot.User", subject="requester-oid")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/privileged-accounts/requests", json={"account_type": "PU", "justification": "Need elevated access."})
        account_id = created.json()["provisioned_user_id"]

        authenticate_as("AccessPilot.Admin")
        await client.post(f"/api/v1/users/{account_id}/enabled", json={"enabled": False})
        response = await client.get("/api/v1/privileged-accounts/activity")
    assert response.status_code == 200
    row = next(r for r in response.json() if r["id"] == account_id)
    assert row["account_type"] == "PU"
    assert row["display_name"] == "PU_Jane Doe"
    assert row["linked_user_display_name"] == "Jane Doe"
    assert row["sign_in_data_available"] is False
    assert row["last_sign_in_at"] is None
    assert row["event_count"] >= 2  # PRIVILEGED_ACCOUNT_CREATED + PRIVILEGED_ACCOUNT_DISABLED
    assert row["last_activity_at"] is not None


@pytest.mark.asyncio
async def test_timeline_merges_creation_enable_disable_and_assignment_events(db_override):
    seeded = await _seed(db_override.factory)
    authenticate_as("AccessPilot.User", subject="requester-oid")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/privileged-accounts/requests", json={"account_type": "PU", "justification": "Need elevated access."})
        account_id = created.json()["provisioned_user_id"]

        # MockProvider is a fresh, unpersisted instance per request (see _connector) — it only ever recognizes
        # its two built-in seed users across requests, not one it "created" during a prior, now-discarded
        # instance. Re-pointing the freshly-provisioned account at one of those built-ins lets the *rest* of this
        # request's real code path (set_user_enabled's provider call, the audit trail, this timeline) run for
        # real, without this pre-existing MockProvider limitation getting in the way of testing this feature.
        async with db_override.factory() as session:
            account = await session.get(User, UUID(account_id))
            account.external_id = "user-001"
            await session.commit()

        authenticate_as("AccessPilot.Admin")
        await client.post(f"/api/v1/users/{account_id}/enabled", json={"enabled": False})
        await client.post(f"/api/v1/users/{account_id}/enabled", json={"enabled": True})
        # A manual grant to the now-associated PU account should show up too — proves the timeline isn't limited
        # to the privileged-account-specific actions alone.
        assignment = await client.post("/api/v1/assignments", json={"user_id": account_id, "resource_type": "ROLE", "resource_id": str(seeded["role_id"]), "assignment_type": "PERMANENT", "justification": "Manual grant.", "bypass_activation": True})
        assert assignment.status_code == 201

        response = await client.get(f"/api/v1/privileged-accounts/{account_id}/timeline")
    assert response.status_code == 200
    actions = [entry["action"] for entry in response.json()]
    assert "PRIVILEGED_ACCOUNT_CREATED" in actions
    assert actions.count("PRIVILEGED_ACCOUNT_ENABLED") + actions.count("PRIVILEGED_ACCOUNT_DISABLED") == 2
    assert "ASSIGNMENT_CREATED" in actions
    # Newest first.
    timestamps = [entry["timestamp"] for entry in response.json()]
    assert timestamps == sorted(timestamps, reverse=True)


@pytest.mark.asyncio
async def test_activity_and_timeline_require_policy_read(db_override):
    seeded = await _seed(db_override.factory)
    authenticate_as("AccessPilot.User", subject="requester-oid")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        activity = await client.get("/api/v1/privileged-accounts/activity")
        timeline = await client.get(f"/api/v1/privileged-accounts/{seeded['requester_id']}/timeline")
    assert activity.status_code == 403
    assert timeline.status_code == 403


@pytest.mark.asyncio
async def test_linked_accounts_endpoint_lists_pu_and_tu_rows(db_override):
    seeded = await _seed(db_override.factory)
    async with db_override.factory() as session:
        session.add_all([
            User(provider_id=seeded["provider_id"], external_id="pu-3", email="pu_j@example.com", display_name="Jane PU", status="ACTIVE", account_type="PU", linked_user_id=seeded["requester_id"]),
            User(provider_id=seeded["provider_id"], external_id="tu-3", email="tu_j@example.com", display_name="Jane TU", status="ACTIVE", account_type="TU", linked_user_id=seeded["requester_id"]),
        ])
        await session.commit()

    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/v1/users/{seeded['requester_id']}/linked-accounts")
    assert response.status_code == 200
    account_types = sorted(row["account_type"] for row in response.json())
    assert account_types == ["PU", "TU"]
