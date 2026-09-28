import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import AccessAssignment, AccessPackage, AccessPackageItem, Application, Group, IdentityProvider, Role, User
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
async def test_a_joiner_matching_a_birthright_policy_gets_an_eligible_grant_to_self_activate(db_override):
    """Committing a CSV joiner provisions a REAL account (the MOCK connector here) and creates a birthright-matched
    assignment — but it always lands ELIGIBLE, never bypassed straight to ACTIVE, even for a real account. This
    matches the rest of AccessPilot's custom PIM model: birthright decides WHAT a joiner is entitled to, but they
    (or an Admin on their behalf) still have to self-activate it via My Access, same as any other eligible grant."""
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
    assert assignments[0].status == "ELIGIBLE"  # not auto-granted — the joiner activates it themselves
    assert assignments[0].bypass_activation is False
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
    directory sync) — it grants ELIGIBLE only (bypass_activation=False), same as every birthright grant now,
    whether the target came from onboarding or an already-synced identity."""
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
async def test_reconciliation_revokes_a_birthright_grant_once_its_policy_is_disabled(db_override):
    """reconcile_birthright_policies_for_user is what a mover reconciliation (department/job_title change) runs
    under the hood — this exercises it directly against a policy that's disabled rather than a changed
    attribute, since "the policy stopped applying" covers both cases identically."""
    from app.services.birthright import evaluate_birthright_policies, reconcile_birthright_policies_for_user

    group_id = await _seed_group(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/policies/birthright", json={"name": "Finance -> Finance Team", "match_field": "department", "match_value": "Finance", "resource_type": "GROUP", "resource_id": group_id})
        policy_id = created.json()["id"]

    async with db_override.factory() as session:
        provider = (await session.execute(select(IdentityProvider))).scalars().first()
        user = User(provider_id=provider.id, external_id="u-mover", email="mover@x.com", display_name="Mover", status="ACTIVE", department="Finance")
        session.add(user)
        await session.commit()
        await session.refresh(user)
        user_id = user.id

        granted = await evaluate_birthright_policies(session, user_id, "admin-oid", "req-1")
        assert len(granted) == 1

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        authenticate_as("AccessPilot.Admin")
        await client.patch(f"/api/v1/policies/birthright/{policy_id}", json={"status": "DISABLED"})

    async with db_override.factory() as session:
        # Disabling the policy already revoked its grant at save time (re-check on save), so a manual reconcile
        # afterwards is a harmless no-op.
        result = await reconcile_birthright_policies_for_user(session, user_id, "admin-oid", "req-2")
        assert result["revoked"] == []
        assert result["granted"] == []

        assignments = (await session.execute(select(AccessAssignment).where(AccessAssignment.user_id == user_id))).scalars().all()
    assert len(assignments) == 1
    assert assignments[0].status == "REVOKED"


async def _seed_package_with_two_items(factory) -> dict:
    async with factory() as session:
        provider = IdentityProvider(name="Directory", type="MOCK", status="CONNECTED", tenant_id="t")
        session.add(provider)
        await session.flush()
        group = Group(provider_id=provider.id, external_id="g-pkg", name="Finance Team", status="ACTIVE", is_privileged=False)
        role = Role(provider_id=provider.id, external_id="r-pkg", name="Finance Reports Reader", role_type="DIRECTORY_ROLE", status="ACTIVE")
        session.add_all([group, role])
        await session.flush()
        package = AccessPackage(name="Finance Starter Kit", status="ACTIVE")
        session.add(package)
        await session.flush()
        session.add_all([
            AccessPackageItem(package_id=package.id, resource_type="GROUP", resource_id=group.id),
            AccessPackageItem(package_id=package.id, resource_type="ROLE", resource_id=role.id),
        ])
        await session.commit()
        return {"provider_id": provider.id, "group_id": group.id, "role_id": role.id, "package_id": package.id}


@pytest.mark.asyncio
async def test_a_birthright_policy_can_grant_a_package(db_override):
    """PACKAGE isn't a single-resource target — a matching birthright policy should fan out into one
    birthright-tagged AccessAssignment per item in the package, live-resolved against its current items."""
    seeded = await _seed_package_with_two_items(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/policies/birthright", json={"name": "Finance -> Starter Kit", "match_field": "department", "match_value": "Finance", "resource_type": "PACKAGE", "resource_id": str(seeded["package_id"])})
    assert created.status_code == 201
    assert created.json()["resource_type"] == "PACKAGE"

    async with db_override.factory() as session:
        user = User(provider_id=seeded["provider_id"], external_id="u-pkg", email="pkg.user@x.com", display_name="Pkg User", status="ACTIVE", department="Finance")
        session.add(user)
        await session.commit()
        await session.refresh(user)
        user_id = user.id

        from app.services.birthright import evaluate_birthright_policies
        granted = await evaluate_birthright_policies(session, user_id, "admin-oid", "req-pkg-1")
        assert len(granted) == 2

        assignments = (await session.execute(select(AccessAssignment).where(AccessAssignment.user_id == user_id))).scalars().all()
    resource_types = sorted(a.resource_type for a in assignments)
    assert resource_types == ["GROUP", "ROLE"]
    assert all(a.status == "ELIGIBLE" for a in assignments)
    assert all(a.birthright_policy_id is not None for a in assignments)


@pytest.mark.asyncio
async def test_re_evaluating_a_package_birthright_policy_does_not_duplicate_items(db_override):
    seeded = await _seed_package_with_two_items(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/policies/birthright", json={"name": "Finance -> Starter Kit", "match_field": "department", "match_value": "Finance", "resource_type": "PACKAGE", "resource_id": str(seeded["package_id"])})

    async with db_override.factory() as session:
        user = User(provider_id=seeded["provider_id"], external_id="u-pkg2", email="pkg.user2@x.com", display_name="Pkg User 2", status="ACTIVE", department="Finance")
        session.add(user)
        await session.commit()
        await session.refresh(user)
        user_id = user.id

        from app.services.birthright import evaluate_birthright_policies
        first = await evaluate_birthright_policies(session, user_id, "admin-oid", "req-pkg-2")
        assert len(first) == 2
        second = await evaluate_birthright_policies(session, user_id, "admin-oid", "req-pkg-3")
        assert second == []

        assignments = (await session.execute(select(AccessAssignment).where(AccessAssignment.user_id == user_id))).scalars().all()
    assert len(assignments) == 2


@pytest.mark.asyncio
async def test_package_birthright_grants_are_linked_to_their_package_and_backfill_repairs_old_ones(db_override):
    """Regression: birthright PACKAGE grants used to be invisible to anything package-aware (My Access's package
    column, User Detail, Access Review's PACKAGE scope) because no AccessPackageAssignment link was recorded."""
    from app.models import AccessPackageAssignment
    from app.services.birthright import backfill_birthright_package_links, evaluate_birthright_policies

    seeded = await _seed_package_with_two_items(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/policies/birthright", json={"name": "Finance -> Starter Kit", "match_field": "department", "match_value": "Finance", "resource_type": "PACKAGE", "resource_id": str(seeded["package_id"])})

    async with db_override.factory() as session:
        user = User(provider_id=seeded["provider_id"], external_id="u-pkg-link", email="pkg.link@x.com", display_name="Pkg Link", status="ACTIVE", department="Finance")
        session.add(user)
        await session.commit()
        await session.refresh(user)

        granted = await evaluate_birthright_policies(session, user.id, "admin-oid", "req-link-1")
        links = (await session.execute(select(AccessPackageAssignment).where(AccessPackageAssignment.user_id == user.id))).scalars().all()
        assert {l.assignment_id for l in links} == set(granted)
        assert {l.package_id for l in links} == {seeded["package_id"]}
        assert len({l.package_assignment_id for l in links}) == 1  # one batch per package grant

        # Simulate grants made before this fix: remove the links, then backfill restores exactly them.
        for link in links:
            await session.delete(link)
        await session.commit()
        assert await backfill_birthright_package_links(session) == 2
        assert await backfill_birthright_package_links(session) == 0  # idempotent


@pytest.mark.asyncio
async def test_reconciliation_revokes_every_item_of_a_disabled_package_birthright_policy(db_override):
    """The existing mover-reconciliation loop revokes purely by birthright_policy_id, regardless of
    resource_type — proves it covers every one of a PACKAGE policy's item-level assignments automatically, with
    no changes of its own needed for this feature."""
    from app.services.birthright import evaluate_birthright_policies, reconcile_birthright_policies_for_user

    seeded = await _seed_package_with_two_items(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/policies/birthright", json={"name": "Finance -> Starter Kit", "match_field": "department", "match_value": "Finance", "resource_type": "PACKAGE", "resource_id": str(seeded["package_id"])})
        policy_id = created.json()["id"]

    async with db_override.factory() as session:
        user = User(provider_id=seeded["provider_id"], external_id="u-pkg3", email="pkg.user3@x.com", display_name="Pkg User 3", status="ACTIVE", department="Finance")
        session.add(user)
        await session.commit()
        await session.refresh(user)
        user_id = user.id
        granted = await evaluate_birthright_policies(session, user_id, "admin-oid", "req-pkg-4")
        assert len(granted) == 2

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        authenticate_as("AccessPilot.Admin")
        await client.patch(f"/api/v1/policies/birthright/{policy_id}", json={"status": "DISABLED"})

    async with db_override.factory() as session:
        result = await reconcile_birthright_policies_for_user(session, user_id, "admin-oid", "req-pkg-5")
        assert result["revoked"] == []  # already revoked when the policy was disabled
        assignments = (await session.execute(select(AccessAssignment).where(AccessAssignment.user_id == user_id))).scalars().all()
    assert all(a.status == "REVOKED" for a in assignments)


@pytest.mark.asyncio
async def test_a_birthright_policy_referencing_a_nonexistent_package_is_rejected(db_override):
    await _seed_package_with_two_items(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/policies/birthright", json={"name": "Bad package rule", "match_field": "department", "match_value": "Finance", "resource_type": "PACKAGE", "resource_id": "00000000-0000-0000-0000-000000000000"})
    assert response.status_code == 404


async def _seed_json_rule_targets(factory) -> dict:
    async with factory() as session:
        provider = IdentityProvider(name="Directory", type="MOCK", status="CONNECTED", tenant_id="t")
        session.add(provider)
        await session.flush()
        group = Group(provider_id=provider.id, external_id="GRP-IT-EMPLOYEES", name="IT Employees Group", status="ACTIVE", is_privileged=False)
        application = Application(provider_id=provider.id, external_id="app-ext-1", name="Microsoft-365", status="ACTIVE", app_roles=[{"id": "approle-e3", "name": "E3 User"}])
        session.add_all([group, application])
        await session.commit()
        await session.refresh(group)
        await session.refresh(application)
        return {"provider_id": provider.id, "group_id": group.id, "application_id": application.id}


def _it_employee_json_policy() -> dict:
    return {
        "policyId": "BR-001",
        "policyType": "BIRTHRIGHT",
        "name": "IT Employee Access",
        "scope": {"identityType": "EMPLOYEE"},
        "rule": {"operator": "AND", "conditions": [
            {"field": "department", "operator": "EQUALS", "value": "IT"},
            {"field": "employmentStatus", "operator": "EQUALS", "value": "ACTIVE"},
        ]},
        "actions": [
            {"action": "ASSIGN", "resourceType": "GROUP", "resource": "GRP-IT-EMPLOYEES"},
            {"action": "ASSIGN", "resourceType": "APPLICATION", "resource": "Microsoft-365", "appRoleExternalId": "approle-e3"},
        ],
        "reconciliation": {"enabled": True, "removeWhenConditionFails": True},
        "audit": {"enabled": True},
    }


@pytest.mark.asyncio
async def test_creating_a_policy_via_json_matches_the_requested_shape(db_override):
    await _seed_json_rule_targets(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/policies/birthright/json", json=_it_employee_json_policy())
    assert response.status_code == 201
    body = response.json()
    assert body["policyId"] == "BR-001"
    assert body["policyType"] == "BIRTHRIGHT"
    assert body["rule"]["operator"] == "AND"
    assert len(body["rule"]["conditions"]) == 2
    assert {a["resourceType"] for a in body["actions"]} == {"GROUP", "APPLICATION"}
    # The human-readable resource key resolves to the real target's own display name on read-back.
    assert {a["resource"] for a in body["actions"]} == {"IT Employees Group", "Microsoft-365"}
    assert body["reconciliation"] == {"enabled": True, "removeWhenConditionFails": True}
    assert body["audit"] == {"enabled": True}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        listed = await client.get("/api/v1/policies/birthright")
    assert any(p["id"] == body["id"] and p["is_advanced"] and p["conditions_count"] == 2 and p["actions_count"] == 2 for p in listed.json())


@pytest.mark.asyncio
async def test_json_policy_grants_only_when_all_and_conditions_match(db_override):
    seeded = await _seed_json_rule_targets(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/policies/birthright/json", json=_it_employee_json_policy())

    async with db_override.factory() as session:
        full_match = User(provider_id=seeded["provider_id"], external_id="u-full", email="full@x.com", display_name="Full Match", status="ACTIVE", department="IT")
        partial_match = User(provider_id=seeded["provider_id"], external_id="u-partial", email="partial@x.com", display_name="Partial Match", status="DISABLED", department="IT")
        session.add_all([full_match, partial_match])
        await session.commit()
        await session.refresh(full_match)
        await session.refresh(partial_match)

        from app.services.birthright import evaluate_birthright_policies
        full_granted = await evaluate_birthright_policies(session, full_match.id, "admin-oid", "req-json-1")
        partial_granted = await evaluate_birthright_policies(session, partial_match.id, "admin-oid", "req-json-2")
    assert len(full_granted) == 2  # GROUP + APPLICATION
    assert partial_granted == []  # status != ACTIVE fails the second AND condition


@pytest.mark.asyncio
async def test_json_policy_with_or_operator_matches_either_condition(db_override):
    seeded = await _seed_json_rule_targets(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    or_policy = {**_it_employee_json_policy(), "name": "IT or Finance", "policyId": "BR-002", "rule": {"operator": "OR", "conditions": [
        {"field": "department", "operator": "EQUALS", "value": "IT"},
        {"field": "department", "operator": "EQUALS", "value": "Finance"},
    ]}, "actions": [{"action": "ASSIGN", "resourceType": "GROUP", "resource": "GRP-IT-EMPLOYEES"}]}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/policies/birthright/json", json=or_policy)

    async with db_override.factory() as session:
        finance_user = User(provider_id=seeded["provider_id"], external_id="u-finance", email="finance@x.com", display_name="Finance User", status="ACTIVE", department="Finance")
        session.add(finance_user)
        await session.commit()
        await session.refresh(finance_user)

        from app.services.birthright import evaluate_birthright_policies
        granted = await evaluate_birthright_policies(session, finance_user.id, "admin-oid", "req-json-3")
    assert len(granted) == 1


@pytest.mark.asyncio
async def test_viewing_a_legacy_policy_as_json_wraps_its_single_condition_and_action(db_override):
    group_id = await _seed_group(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/policies/birthright", json={"name": "Finance -> Finance Team", "match_field": "department", "match_value": "Finance", "resource_type": "GROUP", "resource_id": group_id})
        policy_id = created.json()["id"]
        response = await client.get(f"/api/v1/policies/birthright/{policy_id}/json")
    assert response.status_code == 200
    body = response.json()
    assert body["rule"]["conditions"] == [{"field": "department", "operator": "EQUALS", "value": "Finance"}]
    assert len(body["actions"]) == 1
    assert body["actions"][0]["resourceType"] == "GROUP"


@pytest.mark.asyncio
async def test_editing_a_legacy_policy_as_json_converts_it_to_advanced_mode(db_override):
    seeded_group = await _seed_group(db_override.factory)
    seeded = await _seed_json_rule_targets(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/policies/birthright", json={"name": "Finance -> Finance Team", "match_field": "department", "match_value": "Finance", "resource_type": "GROUP", "resource_id": seeded_group})
        policy_id = created.json()["id"]

        edited = {**_it_employee_json_policy(), "name": "Finance -> Finance Team", "actions": [{"action": "ASSIGN", "resourceType": "GROUP", "resource": "GRP-IT-EMPLOYEES"}]}
        response = await client.put(f"/api/v1/policies/birthright/{policy_id}/json", json=edited)
    assert response.status_code == 200
    assert response.json()["rule"]["conditions"][0]["field"] == "department"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        listed = await client.get("/api/v1/policies/birthright")
    row = next(p for p in listed.json() if p["id"] == policy_id)
    assert row["is_advanced"] is True
    assert row["match_field"] is None


@pytest.mark.asyncio
async def test_non_sticky_policy_grant_is_revoked_when_condition_stops_matching(db_override):
    seeded = await _seed_json_rule_targets(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    simple_policy = {
        "policyId": "BR-010", "policyType": "BIRTHRIGHT", "name": "IT default reconciling",
        "rule": {"operator": "AND", "conditions": [{"field": "department", "operator": "EQUALS", "value": "IT"}]},
        "actions": [{"action": "ASSIGN", "resourceType": "GROUP", "resource": "GRP-IT-EMPLOYEES"}],
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/policies/birthright/json", json=simple_policy)

    async with db_override.factory() as session:
        user = User(provider_id=seeded["provider_id"], external_id="u-reconcile", email="reconcile@x.com", display_name="Reconcile Me", status="ACTIVE", department="IT")
        session.add(user)
        await session.commit()
        await session.refresh(user)
        user_id = user.id

        from app.services.birthright import evaluate_birthright_policies, reconcile_birthright_policies_for_user
        granted = await evaluate_birthright_policies(session, user_id, "admin-oid", "req-recon-1")
        assert len(granted) == 1

        user.department = "Sales"
        await session.commit()
        result = await reconcile_birthright_policies_for_user(session, user_id, "admin-oid", "req-recon-2")
    assert len(result["revoked"]) == 1


@pytest.mark.asyncio
async def test_sticky_policy_grant_survives_when_condition_stops_matching(db_override):
    seeded = await _seed_json_rule_targets(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    sticky_policy = {
        "policyId": "BR-011", "policyType": "BIRTHRIGHT", "name": "IT sticky grant",
        "rule": {"operator": "AND", "conditions": [{"field": "department", "operator": "EQUALS", "value": "IT"}]},
        "actions": [{"action": "ASSIGN", "resourceType": "GROUP", "resource": "GRP-IT-EMPLOYEES"}],
        "reconciliation": {"enabled": False, "removeWhenConditionFails": False},
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/policies/birthright/json", json=sticky_policy)

    async with db_override.factory() as session:
        user = User(provider_id=seeded["provider_id"], external_id="u-sticky", email="sticky@x.com", display_name="Sticky Grant", status="ACTIVE", department="IT")
        session.add(user)
        await session.commit()
        await session.refresh(user)
        user_id = user.id

        from app.services.birthright import evaluate_birthright_policies, reconcile_birthright_policies_for_user
        granted = await evaluate_birthright_policies(session, user_id, "admin-oid", "req-sticky-1")
        assert len(granted) == 1

        user.department = "Sales"
        await session.commit()
        result = await reconcile_birthright_policies_for_user(session, user_id, "admin-oid", "req-sticky-2")
        assert result["revoked"] == []

        assignments = (await session.execute(select(AccessAssignment).where(AccessAssignment.user_id == user_id))).scalars().all()
    assert len(assignments) == 1
    assert assignments[0].status == "ELIGIBLE"  # never taken back automatically


@pytest.mark.asyncio
async def test_json_policy_rejects_disabling_audit(db_override):
    await _seed_json_rule_targets(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    payload = {**_it_employee_json_policy(), "audit": {"enabled": False}}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/policies/birthright/json", json=payload)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "AUDIT_CANNOT_BE_DISABLED"


@pytest.mark.asyncio
async def test_json_policy_rejects_an_unsupported_condition_field(db_override):
    await _seed_json_rule_targets(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    payload = {**_it_employee_json_policy(), "rule": {"operator": "AND", "conditions": [{"field": "favoriteColor", "operator": "EQUALS", "value": "blue"}]}}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/policies/birthright/json", json=payload)
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_json_policy_rejects_a_resource_that_does_not_exist(db_override):
    await _seed_json_rule_targets(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    payload = {**_it_employee_json_policy(), "actions": [{"action": "ASSIGN", "resourceType": "GROUP", "resource": "NO-SUCH-GROUP"}]}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/policies/birthright/json", json=payload)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_resolve_actions_reports_found_and_not_found_resources_without_saving(db_override):
    """The JSON editor's 'Check resources' button — a preview only, never creates a policy."""
    seeded = await _seed_json_rule_targets(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    payload = {"actions": [
        {"action": "ASSIGN", "resourceType": "GROUP", "resource": "GRP-IT-EMPLOYEES"},
        {"action": "ASSIGN", "resourceType": "APPLICATION", "resource": "Microsoft-365", "appRoleExternalId": "approle-e3"},
        {"action": "ASSIGN", "resourceType": "GROUP", "resource": "NO-SUCH-GROUP"},
    ]}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/policies/birthright/resolve-actions", json=payload)
    assert response.status_code == 200
    results = response.json()["results"]
    assert len(results) == 3
    assert results[0]["found"] is True
    assert results[0]["resolvedId"] == str(seeded["group_id"])
    assert results[0]["resolvedName"] == "IT Employees Group"
    assert results[1]["found"] is True
    assert results[1]["resolvedName"] == "Microsoft-365"
    assert results[2]["found"] is False
    assert results[2]["error"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        listed = await client.get("/api/v1/policies/birthright")
    assert listed.json() == []  # nothing was actually created


@pytest.mark.asyncio
async def test_resolve_actions_requires_policy_create_permission(db_override):
    await _seed_json_rule_targets(db_override.factory)
    authenticate_as("AccessPilot.User", subject="regular-user-oid")
    payload = {"actions": [{"action": "ASSIGN", "resourceType": "GROUP", "resource": "GRP-IT-EMPLOYEES"}]}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/policies/birthright/resolve-actions", json=payload)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_json_policy_rejects_an_application_action_with_no_app_role(db_override):
    await _seed_json_rule_targets(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    payload = {**_it_employee_json_policy(), "actions": [{"action": "ASSIGN", "resourceType": "APPLICATION", "resource": "Microsoft-365"}]}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/policies/birthright/json", json=payload)
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_json_policy_rejects_a_non_birthright_policy_type(db_override):
    await _seed_json_rule_targets(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    payload = {**_it_employee_json_policy(), "policyType": "SOMETHING_ELSE"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/policies/birthright/json", json=payload)
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_a_normal_user_cannot_manage_birthright_policies(db_override):
    group_id = await _seed_group(db_override.factory)
    authenticate_as("AccessPilot.User", subject="regular-user-oid")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/policies/birthright", json={"name": "x", "match_field": "department", "match_value": "Finance", "resource_type": "GROUP", "resource_id": group_id})
    assert response.status_code == 403


async def _seed_finance_people(factory):
    async with factory() as session:
        provider = IdentityProvider(name="Directory", type="MOCK", status="CONNECTED", tenant_id="t")
        session.add(provider)
        await session.flush()
        group = Group(provider_id=provider.id, external_id="g-fin-recheck", name="Finance Group", status="ACTIVE", is_privileged=False)
        sam = User(provider_id=provider.id, external_id="sam", email="sam@x.com", display_name="Sam", status="ACTIVE", department="Finance")
        amy = User(provider_id=provider.id, external_id="amy", email="amy@x.com", display_name="Amy", status="ACTIVE", department="Finance")
        ian = User(provider_id=provider.id, external_id="ian", email="ian@x.com", display_name="Ian", status="ACTIVE", department="IT")
        session.add_all([group, sam, amy, ian])
        await session.commit()
        return {"group": group.id, "sam": sam.id, "amy": amy.id, "ian": ian.id}


async def _statuses(factory, ids):
    async with factory() as session:
        rows = (await session.execute(select(AccessAssignment))).scalars().all()
    result = {}
    for name, user_id in (("sam", ids["sam"]), ("amy", ids["amy"]), ("ian", ids["ian"])):
        result[name] = sorted(r.status for r in rows if r.user_id == user_id)
    return result


@pytest.mark.asyncio
async def test_creating_a_policy_immediately_grants_everyone_it_already_matches(db_override):
    ids = await _seed_finance_people(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/policies/birthright", json={"name": "Finance access", "match_field": "department", "match_value": "Finance", "resource_type": "GROUP", "resource_id": str(ids["group"])})
    assert created.status_code == 201
    assert created.json()["recheck"] == {"users_checked": 2, "granted": 2, "revoked": 0}
    assert await _statuses(db_override.factory, ids) == {"sam": ["ELIGIBLE"], "amy": ["ELIGIBLE"], "ian": []}


@pytest.mark.asyncio
async def test_fixing_a_typo_in_a_policy_takes_effect_at_once_and_a_wrong_edit_takes_access_back(db_override):
    ids = await _seed_finance_people(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/policies/birthright", json={"name": "Finance access", "match_field": "department", "match_value": "Finanace", "resource_type": "GROUP", "resource_id": str(ids["group"])})
        assert created.json()["recheck"]["granted"] == 0  # the typo matches nobody
        policy_id = created.json()["id"]
        fixed = await client.patch(f"/api/v1/policies/birthright/{policy_id}", json={"match_value": "Finance"})
        assert fixed.json()["recheck"] == {"users_checked": 2, "granted": 2, "revoked": 0}
        assert await _statuses(db_override.factory, ids) == {"sam": ["ELIGIBLE"], "amy": ["ELIGIBLE"], "ian": []}
        broken = await client.patch(f"/api/v1/policies/birthright/{policy_id}", json={"match_value": "Legal"})
        assert broken.json()["recheck"] == {"users_checked": 2, "granted": 0, "revoked": 2}
    assert await _statuses(db_override.factory, ids) == {"sam": ["REVOKED"], "amy": ["REVOKED"], "ian": []}


@pytest.mark.asyncio
async def test_disabling_a_policy_revokes_what_it_granted_and_manual_grants_are_untouched(db_override):
    ids = await _seed_finance_people(db_override.factory)
    async with db_override.factory() as session:
        other = Group(provider_id=(await session.execute(select(IdentityProvider.id))).scalars().first(), external_id="g-manual-x", name="Manual", status="ACTIVE", is_privileged=False)
        session.add(other)
        await session.flush()
        session.add(AccessAssignment(provider_id=other.provider_id, user_id=ids["sam"], resource_type="GROUP", resource_id=other.id, assignment_type="PERMANENT", status="ELIGIBLE", justification="Manual."))
        await session.commit()
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/policies/birthright", json={"name": "Finance access", "match_field": "department", "match_value": "Finance", "resource_type": "GROUP", "resource_id": str(ids["group"])})
        disabled = await client.patch(f"/api/v1/policies/birthright/{created.json()['id']}", json={"status": "DISABLED"})
    assert disabled.json()["recheck"]["revoked"] == 2
    assert await _statuses(db_override.factory, ids) == {"sam": ["ELIGIBLE", "REVOKED"], "amy": ["REVOKED"], "ian": []}  # Sam's manual grant survives


@pytest.mark.asyncio
async def test_department_policy_returns_soft_warnings_but_still_saves(db_override):
    from app.models import Department
    group_id = await _seed_group(db_override.factory)
    async with db_override.factory() as session:
        provider = (await session.scalars(select(IdentityProvider))).first()
        session.add(Department(name="Finance"))
        session.add(User(provider_id=provider.id, external_id="fin-1", email="f@x.com", display_name="Fin User", department="finance", status="ACTIVE"))
        await session.commit()
    authenticate_as("AccessPilot.Admin")
    def payload(name, dept):
        return {"name": name, "match_field": "department", "match_value": dept, "resource_type": "GROUP", "resource_id": group_id}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        good = await client.post("/api/v1/policies/birthright", json=payload("Good", "Finance"))      # in list, one holder (case-insensitive)
        typo = await client.post("/api/v1/policies/birthright", json=payload("Typo", "Finanace"))     # not in list, nobody has it
        edited = await client.patch(f"/api/v1/policies/birthright/{typo.json()['id']}", json={"match_value": "Financ"})
        fixed = await client.patch(f"/api/v1/policies/birthright/{typo.json()['id']}", json={"match_value": "Finance"})
    assert good.status_code == 201 and good.json()["warnings"] == []
    assert typo.status_code == 201 and len(typo.json()["warnings"]) == 2 and "Departments list" in typo.json()["warnings"][0] and "matches nobody" in typo.json()["warnings"][1]
    assert edited.status_code == 200 and len(edited.json()["warnings"]) == 2
    assert fixed.json()["warnings"] == []
