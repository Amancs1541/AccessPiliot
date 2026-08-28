import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import AccessAssignment, Group, IdentityProvider, User
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


async def _seed_group(factory) -> str:
    async with factory() as session:
        provider = IdentityProvider(name="Directory", type="MOCK", status="CONNECTED", tenant_id="t")
        session.add(provider)
        await session.flush()
        group = Group(provider_id=provider.id, external_id="g1", name="Finance Team", status="ACTIVE", is_privileged=False)
        session.add(group)
        await session.commit()
        return str(group.id)


async def _real_provisioned_user(factory, email: str) -> User:
    """Onboarding now provisions a REAL account (via the MOCK connector in tests) for every CSV joiner/mover, so
    birthright grants land on that real account, not the CSV bookkeeping row — look it up by email, under
    whichever non-CSV provider it landed on."""
    async with factory() as session:
        provider = (await session.execute(select(IdentityProvider).where(IdentityProvider.type != "CSV"))).scalar_one()
        return (await session.execute(select(User).where(User.email == email, User.provider_id == provider.id))).scalar_one()


@pytest.mark.asyncio
async def test_admin_can_create_a_birthright_policy(db_override):
    group_id = await _seed_group(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/policies/birthright", json={"name": "Finance -> Finance Team", "match_field": "department", "match_value": "Finance", "resource_type": "GROUP", "resource_id": group_id})
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "ACTIVE"
    assert body["match_field"] == "department"


@pytest.mark.asyncio
async def test_duplicate_policy_name_is_rejected(db_override):
    group_id = await _seed_group(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/policies/birthright", json={"name": "Finance rule", "match_field": "department", "match_value": "Finance", "resource_type": "GROUP", "resource_id": group_id})
        second = await client.post("/api/v1/policies/birthright", json={"name": "Finance rule", "match_field": "department", "match_value": "Marketing", "resource_type": "GROUP", "resource_id": group_id})
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_policy_referencing_a_nonexistent_group_is_rejected(db_override):
    await _seed_group(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/policies/birthright", json={"name": "Bad rule", "match_field": "department", "match_value": "Finance", "resource_type": "GROUP", "resource_id": "00000000-0000-0000-0000-000000000000"})
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_a_joiner_matching_a_birthright_policy_gets_a_real_immediate_grant(db_override):
    """Core Phase 8+9 behavior: committing a CSV joiner provisions a REAL account (the MOCK connector here) and
    grants birthright-matched access for real immediately (bypass_activation) — birthright is day-one, automatic
    access, distinct from JIT/PIM access which still requires self-activation."""
    group_id = await _seed_group(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/policies/birthright", json={"name": "Finance -> Finance Team", "match_field": "department", "match_value": "Finance", "resource_type": "GROUP", "resource_id": group_id})

        csv_content = "employeeId,firstName,lastName,email,department,status\nEMP3001,New,Hire,new.hire@company.com,Finance,ACTIVE\n"
        uploaded = await client.post("/api/v1/onboarding/csv", json={"filename": "joiner.csv", "content": csv_content})
        committed = await client.post(f"/api/v1/onboarding/imports/{uploaded.json()['id']}/commit")
    assert committed.json()["real_accounts_provisioned_count"] == 1
    assert committed.json()["birthright_assignments_created_count"] == 1

    real_user = await _real_provisioned_user(db_override.factory, "new.hire@company.com")
    async with db_override.factory() as session:
        assignments = (await session.execute(select(AccessAssignment).where(AccessAssignment.user_id == real_user.id))).scalars().all()
    assert len(assignments) == 1
    assert assignments[0].status == "ACTIVE"  # real, immediate grant — birthright bypasses eligible/activate
    assert assignments[0].bypass_activation is True
    assert assignments[0].resource_type == "GROUP"
    assert str(assignments[0].resource_id) == group_id
    assert "Birthright policy" in assignments[0].justification


@pytest.mark.asyncio
async def test_a_non_matching_department_gets_no_birthright_assignment(db_override):
    group_id = await _seed_group(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/policies/birthright", json={"name": "Finance -> Finance Team", "match_field": "department", "match_value": "Finance", "resource_type": "GROUP", "resource_id": group_id})
        csv_content = "employeeId,firstName,lastName,email,department,status\nEMP3002,New,Hire,new.hire2@company.com,Engineering,ACTIVE\n"
        uploaded = await client.post("/api/v1/onboarding/csv", json={"filename": "joiner.csv", "content": csv_content})
        await client.post(f"/api/v1/onboarding/imports/{uploaded.json()['id']}/commit")

    async with db_override.factory() as session:
        user = (await session.execute(select(User).where(User.employee_id == "EMP3002"))).scalar_one()
        assignments = (await session.execute(select(AccessAssignment).where(AccessAssignment.user_id == user.id))).scalars().all()
    assert assignments == []


@pytest.mark.asyncio
async def test_re_committing_an_unchanged_identity_does_not_duplicate_the_birthright_assignment(db_override):
    group_id = await _seed_group(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/policies/birthright", json={"name": "Finance -> Finance Team", "match_field": "department", "match_value": "Finance", "resource_type": "GROUP", "resource_id": group_id})
        csv_content = "employeeId,firstName,lastName,email,department,jobTitle,status\nEMP3003,New,Hire,new.hire3@company.com,Finance,Analyst,ACTIVE\n"
        first = await client.post("/api/v1/onboarding/csv", json={"filename": "joiner.csv", "content": csv_content})
        await client.post(f"/api/v1/onboarding/imports/{first.json()['id']}/commit")

        # A mover update (job title changes, department stays Finance) re-triggers evaluation for the same user.
        mover_csv = "employeeId,firstName,lastName,email,department,jobTitle,status\nEMP3003,New,Hire,new.hire3@company.com,Finance,Senior Analyst,ACTIVE\n"
        second = await client.post("/api/v1/onboarding/csv", json={"filename": "mover.csv", "content": mover_csv})
        assert second.json()["updated_count"] == 1
        second_commit = await client.post(f"/api/v1/onboarding/imports/{second.json()['id']}/commit")
    assert second_commit.json()["birthright_assignments_created_count"] == 0  # already held — no duplicate

    real_user = await _real_provisioned_user(db_override.factory, "new.hire3@company.com")
    async with db_override.factory() as session:
        assignments = (await session.execute(select(AccessAssignment).where(AccessAssignment.user_id == real_user.id))).scalars().all()
    assert len(assignments) == 1  # not duplicated by the second (mover) commit


@pytest.mark.asyncio
async def test_disabling_a_policy_stops_it_from_matching_new_joiners(db_override):
    group_id = await _seed_group(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/policies/birthright", json={"name": "Finance -> Finance Team", "match_field": "department", "match_value": "Finance", "resource_type": "GROUP", "resource_id": group_id})
        policy_id = created.json()["id"]
        disabled = await client.patch(f"/api/v1/policies/birthright/{policy_id}", json={"status": "DISABLED"})
        assert disabled.json()["status"] == "DISABLED"

        csv_content = "employeeId,firstName,lastName,email,department,status\nEMP3004,New,Hire,new.hire4@company.com,Finance,ACTIVE\n"
        uploaded = await client.post("/api/v1/onboarding/csv", json={"filename": "joiner.csv", "content": csv_content})
        await client.post(f"/api/v1/onboarding/imports/{uploaded.json()['id']}/commit")

    async with db_override.factory() as session:
        user = (await session.execute(select(User).where(User.employee_id == "EMP3004"))).scalar_one()
        assignments = (await session.execute(select(AccessAssignment).where(AccessAssignment.user_id == user.id))).scalars().all()
    assert assignments == []


@pytest.mark.asyncio
async def test_deleting_a_policy_removes_it_from_the_list(db_override):
    group_id = await _seed_group(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/policies/birthright", json={"name": "Temp rule", "match_field": "department", "match_value": "Finance", "resource_type": "GROUP", "resource_id": group_id})
        policy_id = created.json()["id"]
        deleted = await client.delete(f"/api/v1/policies/birthright/{policy_id}")
        assert deleted.status_code == 204
        listed = await client.get("/api/v1/policies/birthright")
    assert listed.json() == []


@pytest.mark.asyncio
async def test_manual_evaluate_endpoint_applies_policies_to_an_already_synced_identity(db_override):
    """The manual endpoint is for identities that never went through CSV onboarding at all (e.g. a regular Entra
    directory sync) — it grants ELIGIBLE only (bypass_activation=False), not an immediate real grant, since the
    caller hasn't necessarily confirmed the target should get instant access the way a freshly provisioned
    onboarding joiner does."""
    group_id = await _seed_group(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/policies/birthright", json={"name": "Finance -> Finance Team", "match_field": "department", "match_value": "Finance", "resource_type": "GROUP", "resource_id": group_id})

        async with db_override.factory() as session:
            entra_provider = (await session.execute(select(IdentityProvider).where(IdentityProvider.type == "MOCK"))).scalar_one()
            synced_user = User(provider_id=entra_provider.id, external_id="entra-obj-1", email="already.synced@company.com", display_name="Already Synced", status="ACTIVE", department="Finance")
            session.add(synced_user)
            await session.commit()
            await session.refresh(synced_user)

        first = await client.post(f"/api/v1/policies/birthright/evaluate/{synced_user.id}")
        assert first.status_code == 200
        assert first.json()["matched_policies"] == 1

        second = await client.post(f"/api/v1/policies/birthright/evaluate/{synced_user.id}")
    assert second.json()["matched_policies"] == 0  # idempotent — already holds it, no duplicate

    async with db_override.factory() as session:
        assignments = (await session.execute(select(AccessAssignment).where(AccessAssignment.user_id == synced_user.id))).scalars().all()
    assert len(assignments) == 1
    assert assignments[0].status == "ELIGIBLE"  # NOT an immediate real grant, unlike an onboarding-provisioned joiner
    assert assignments[0].bypass_activation is False


@pytest.mark.asyncio
async def test_a_normal_user_cannot_manage_birthright_policies(db_override):
    group_id = await _seed_group(db_override.factory)
    authenticate_as("AccessPilot.User", subject="regular-user-oid")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/policies/birthright", json={"name": "x", "match_field": "department", "match_value": "Finance", "resource_type": "GROUP", "resource_id": group_id})
    assert response.status_code == 403
