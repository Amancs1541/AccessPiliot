import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import AccessAssignment, BusinessRole, BusinessRoleItem, Group, IdentityProvider, Role, User, UserGroup
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
        provider = IdentityProvider(name="Directory", type="MOCK", status="CONNECTED", tenant_id="t")
        session.add(provider)
        await session.flush()
        group = Group(provider_id=provider.id, external_id="g1", name="IT Department", status="ACTIVE", is_privileged=False)
        role = Role(provider_id=provider.id, external_id="r1", name="IT Support Role", role_type="DIRECTORY_ROLE", status="ACTIVE")
        member = User(provider_id=provider.id, external_id="u1", email="member@x.com", display_name="Existing Member", status="ACTIVE")
        session.add_all([group, role, member])
        await session.flush()
        session.add(UserGroup(user_id=member.id, group_id=group.id, source="SYNC"))
        await session.commit()
        return {"provider_id": provider.id, "group_id": group.id, "role_id": role.id, "member_id": member.id}


@pytest.mark.asyncio
async def test_admin_can_create_a_group_role_mapping(db_override):
    seeded = await _seed(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/policies/group-role-mappings", json={"source_group_id": str(seeded["group_id"]), "resource_type": "ROLE", "resource_id": str(seeded["role_id"])})
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "ACTIVE"
    assert body["source_group_name"] == "IT Department"
    assert body["resource_display_name"] == "IT Support Role"


@pytest.mark.asyncio
async def test_creating_a_mapping_instantly_grants_every_current_member(db_override):
    """Layer-A instant enforcement: a member already in the group before the mapping existed must not have to
    wait for a sync or any other event — creating the mapping itself is the trigger."""
    seeded = await _seed(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/policies/group-role-mappings", json={"source_group_id": str(seeded["group_id"]), "resource_type": "ROLE", "resource_id": str(seeded["role_id"])})

    async with db_override.factory() as session:
        assignments = (await session.execute(select(AccessAssignment).where(AccessAssignment.user_id == seeded["member_id"]))).scalars().all()
    assert len(assignments) == 1
    assert assignments[0].resource_id == seeded["role_id"]
    assert assignments[0].status == "ELIGIBLE"
    assert assignments[0].group_role_mapping_id is not None


@pytest.mark.asyncio
async def test_disabling_a_mapping_instantly_revokes_current_holders(db_override):
    seeded = await _seed(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/policies/group-role-mappings", json={"source_group_id": str(seeded["group_id"]), "resource_type": "ROLE", "resource_id": str(seeded["role_id"])})
        mapping_id = created.json()["id"]
        disabled = await client.patch(f"/api/v1/policies/group-role-mappings/{mapping_id}", json={"status": "DISABLED"})
    assert disabled.json()["status"] == "DISABLED"

    async with db_override.factory() as session:
        assignment = (await session.execute(select(AccessAssignment).where(AccessAssignment.user_id == seeded["member_id"]))).scalars().one()
    assert assignment.status == "REVOKED"


@pytest.mark.asyncio
async def test_deleting_a_mapping_instantly_revokes_current_holders(db_override):
    seeded = await _seed(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/policies/group-role-mappings", json={"source_group_id": str(seeded["group_id"]), "resource_type": "ROLE", "resource_id": str(seeded["role_id"])})
        mapping_id = created.json()["id"]
        deleted = await client.delete(f"/api/v1/policies/group-role-mappings/{mapping_id}")
    assert deleted.status_code == 204

    async with db_override.factory() as session:
        assignment = (await session.execute(select(AccessAssignment).where(AccessAssignment.user_id == seeded["member_id"]))).scalars().one()
    assert assignment.status == "REVOKED"


async def _seed_with_business_role(factory, *, status: str = "ACTIVE"):
    """Same shape as _seed, plus a Business Role with two mapped items (a Group and a Role, distinct from the
    source group) — proves GROUP_ROLE_MAPPING's BUSINESS_ROLE action kind mirrors birthright's own fan-out."""
    async with factory() as session:
        provider = IdentityProvider(name="Directory", type="MOCK", status="CONNECTED", tenant_id="t")
        session.add(provider)
        await session.flush()
        source_group = Group(provider_id=provider.id, external_id="g-src", name="IT Department (GRM)", status="ACTIVE", is_privileged=False)
        item_group = Group(provider_id=provider.id, external_id="g-item", name="Finance Group (GRM item)", status="ACTIVE", is_privileged=False)
        item_role = Role(provider_id=provider.id, external_id="r-item", name="Finance Role (GRM item)", role_type="DIRECTORY_ROLE", status="ACTIVE")
        member = User(provider_id=provider.id, external_id="u-grm", email="grm-member@x.com", display_name="GRM Member", status="ACTIVE")
        session.add_all([source_group, item_group, item_role, member])
        await session.flush()
        session.add(UserGroup(user_id=member.id, group_id=source_group.id, source="SYNC"))
        business_role = BusinessRole(name="Finance Analyst (GRM test)", status=status)
        session.add(business_role)
        await session.flush()
        session.add_all([
            BusinessRoleItem(role_id=business_role.id, resource_type="GROUP", resource_id=item_group.id),
            BusinessRoleItem(role_id=business_role.id, resource_type="ROLE", resource_id=item_role.id),
        ])
        await session.commit()
        return {"provider_id": provider.id, "source_group_id": source_group.id, "item_group_id": item_group.id, "item_role_id": item_role.id, "member_id": member.id, "business_role_id": business_role.id}


@pytest.mark.asyncio
async def test_creating_a_mapping_to_a_business_role_instantly_grants_every_item(db_override):
    seeded = await _seed_with_business_role(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/policies/group-role-mappings", json={"source_group_id": str(seeded["source_group_id"]), "resource_type": "BUSINESS_ROLE", "resource_id": str(seeded["business_role_id"])})
    assert created.status_code == 201
    assert created.json()["resource_type"] == "BUSINESS_ROLE"
    assert created.json()["resource_display_name"] == "Finance Analyst (GRM test)"

    async with db_override.factory() as session:
        assignments = (await session.execute(select(AccessAssignment).where(AccessAssignment.user_id == seeded["member_id"]))).scalars().all()
    assert len(assignments) == 2
    assert sorted(a.resource_type for a in assignments) == ["GROUP", "ROLE"]
    assert all(a.status == "ELIGIBLE" for a in assignments)
    assert all(a.group_role_mapping_id is not None for a in assignments)
    assert all(a.business_role_id == seeded["business_role_id"] for a in assignments)
    assert assignments[0].role_assignment_id == assignments[1].role_assignment_id


@pytest.mark.asyncio
async def test_disabling_a_business_role_mapping_instantly_revokes_every_item(db_override):
    seeded = await _seed_with_business_role(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/policies/group-role-mappings", json={"source_group_id": str(seeded["source_group_id"]), "resource_type": "BUSINESS_ROLE", "resource_id": str(seeded["business_role_id"])})
        mapping_id = created.json()["id"]
        disabled = await client.patch(f"/api/v1/policies/group-role-mappings/{mapping_id}", json={"status": "DISABLED"})
    assert disabled.json()["status"] == "DISABLED"

    async with db_override.factory() as session:
        assignments = (await session.execute(select(AccessAssignment).where(AccessAssignment.user_id == seeded["member_id"]))).scalars().all()
    assert len(assignments) == 2
    assert all(a.status == "REVOKED" for a in assignments)


@pytest.mark.asyncio
async def test_a_business_role_mapping_rejects_an_unknown_role(db_override):
    seeded = await _seed_with_business_role(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/policies/group-role-mappings", json={"source_group_id": str(seeded["source_group_id"]), "resource_type": "BUSINESS_ROLE", "resource_id": "00000000-0000-0000-0000-000000000000"})
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_a_non_active_business_role_mapping_grants_nothing(db_override):
    seeded = await _seed_with_business_role(db_override.factory, status="DRAFT")
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/policies/group-role-mappings", json={"source_group_id": str(seeded["source_group_id"]), "resource_type": "BUSINESS_ROLE", "resource_id": str(seeded["business_role_id"])})

    async with db_override.factory() as session:
        assignments = (await session.execute(select(AccessAssignment).where(AccessAssignment.user_id == seeded["member_id"]))).scalars().all()
    assert assignments == []


@pytest.mark.asyncio
async def test_a_user_who_joins_the_mapped_group_later_gets_evaluated(db_override):
    """Mirrors the manual /evaluate endpoint (used elsewhere for already-synced identities, e.g. after a real
    directory sync notices a new group member) rather than requiring a full sync in this test."""
    from app.services.group_role_mapping import evaluate_group_role_mappings_for_user

    seeded = await _seed(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/policies/group-role-mappings", json={"source_group_id": str(seeded["group_id"]), "resource_type": "ROLE", "resource_id": str(seeded["role_id"])})

    async with db_override.factory() as session:
        provider_id = seeded["provider_id"]
        newcomer = User(provider_id=provider_id, external_id="u2", email="newcomer@x.com", display_name="Newcomer", status="ACTIVE")
        session.add(newcomer)
        await session.flush()
        session.add(UserGroup(user_id=newcomer.id, group_id=seeded["group_id"], source="SYNC"))
        await session.commit()
        await session.refresh(newcomer)
        newcomer_id = newcomer.id

        created_ids = await evaluate_group_role_mappings_for_user(session, newcomer_id, "admin-oid", "req-1")
        assert len(created_ids) == 1

        assignments = (await session.execute(select(AccessAssignment).where(AccessAssignment.user_id == newcomer_id))).scalars().all()
    assert len(assignments) == 1
    assert assignments[0].status == "ELIGIBLE"
    assert assignments[0].group_role_mapping_id is not None


@pytest.mark.asyncio
async def test_leaving_the_group_revokes_the_mapping_granted_assignment_but_not_a_manual_one(db_override, monkeypatch):
    """The safety property: reconcile_group_role_mappings_for_user only ever revokes an assignment it can prove
    it granted (group_role_mapping_id set) — a manual grant to the exact same role, held by the exact same user,
    must survive untouched even after they leave the group."""
    from app.services.group_role_mapping import reconcile_group_role_mappings_for_user

    seeded = await _seed(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/policies/group-role-mappings", json={"source_group_id": str(seeded["group_id"]), "resource_type": "ROLE", "resource_id": str(seeded["role_id"])})

        # A second, independent user holds the same role via a manual admin grant — not through the mapping.
        async with db_override.factory() as session:
            manual_user = User(provider_id=seeded["provider_id"], external_id="u3", email="manual@x.com", display_name="Manual Grant", status="ACTIVE")
            session.add(manual_user)
            await session.commit()
            await session.refresh(manual_user)
            manual_user_id = manual_user.id

        manual = await client.post("/api/v1/assignments", json={"user_id": str(manual_user_id), "resource_type": "ROLE", "resource_id": str(seeded["role_id"]), "assignment_type": "PERMANENT", "justification": "Manually granted by an admin.", "bypass_activation": True})
        assert manual.status_code == 201

    # The original member leaves the group (their UserGroup row is removed, simulating a sync-detected departure).
    async with db_override.factory() as session:
        membership = (await session.execute(select(UserGroup).where(UserGroup.user_id == seeded["member_id"], UserGroup.group_id == seeded["group_id"]))).scalar_one()
        await session.delete(membership)
        await session.commit()

        result = await reconcile_group_role_mappings_for_user(session, seeded["member_id"], "system:test", "req-2")
        assert len(result["revoked"]) == 1

        member_assignment = (await session.execute(select(AccessAssignment).where(AccessAssignment.user_id == seeded["member_id"]))).scalars().one()
        manual_assignment = (await session.execute(select(AccessAssignment).where(AccessAssignment.user_id == manual_user_id))).scalars().one()
    assert member_assignment.status == "REVOKED"
    assert manual_assignment.status == "ACTIVE"  # untouched — never linked to a group role mapping


@pytest.mark.asyncio
async def test_a_normal_user_cannot_manage_group_role_mappings(db_override):
    seeded = await _seed(db_override.factory)
    authenticate_as("AccessPilot.User", subject="regular-user-oid")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/policies/group-role-mappings", json={"source_group_id": str(seeded["group_id"]), "resource_type": "ROLE", "resource_id": str(seeded["role_id"])})
    assert response.status_code == 403


async def _seed_with_eligible_group_assignment(factory):
    """Same base seed, plus a real ELIGIBLE GROUP assignment for the member — the starting point every
    cascading-activation test needs (the group access someone is about to activate)."""
    seeded = await _seed(factory)
    async with factory() as session:
        group_assignment = AccessAssignment(provider_id=seeded["provider_id"], user_id=seeded["member_id"], resource_type="GROUP", resource_id=seeded["group_id"], assignment_type="PERMANENT", status="ELIGIBLE", justification="Eligible for testing.")
        session.add(group_assignment)
        await session.commit()
        await session.refresh(group_assignment)
        seeded["group_assignment_id"] = group_assignment.id
    return seeded


@pytest.mark.asyncio
async def test_activating_the_group_cascades_to_activate_the_mapped_role(db_override):
    seeded = await _seed_with_eligible_group_assignment(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/policies/group-role-mappings", json={"source_group_id": str(seeded["group_id"]), "resource_type": "ROLE", "resource_id": str(seeded["role_id"])})
        activated = await client.post(f"/api/v1/assignments/{seeded['group_assignment_id']}/activate", json={"duration_hours": 4, "justification": "Activating my IT Department access."})
    assert activated.status_code == 200
    assert activated.json()["status"] == "ACTIVE"

    async with db_override.factory() as session:
        role_assignment = (await session.execute(select(AccessAssignment).where(AccessAssignment.user_id == seeded["member_id"], AccessAssignment.resource_type == "ROLE"))).scalars().one()
    assert role_assignment.status == "ACTIVE"
    assert role_assignment.group_role_mapping_id is not None
    assert role_assignment.activated_at is not None


@pytest.mark.asyncio
async def test_cascade_activation_creates_the_role_assignment_if_it_didnt_exist_yet(db_override):
    """The mapping is created AFTER the group assignment already existed — proves timing never matters, the
    cascade creates the missing ELIGIBLE role assignment and activates it in the same action."""
    seeded = await _seed_with_eligible_group_assignment(db_override.factory)
    authenticate_as("AccessPilot.Admin")

    # Create the mapping directly against the DB, bypassing the API's own instant-reconcile-on-create step, to
    # isolate and prove specifically that activate_assignment's own cascade creates what's missing.
    async with db_override.factory() as session:
        from app.models import GroupRoleMapping
        session.add(GroupRoleMapping(source_group_id=seeded["group_id"], resource_type="ROLE", resource_id=seeded["role_id"]))
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        activated = await client.post(f"/api/v1/assignments/{seeded['group_assignment_id']}/activate", json={"duration_hours": 4, "justification": "Activating my IT Department access."})
    assert activated.status_code == 200

    async with db_override.factory() as session:
        role_assignment = (await session.execute(select(AccessAssignment).where(AccessAssignment.user_id == seeded["member_id"], AccessAssignment.resource_type == "ROLE"))).scalars().one()
    assert role_assignment.status == "ACTIVE"


@pytest.mark.asyncio
async def test_deactivating_the_group_cascades_to_deactivate_the_mapped_role(db_override):
    seeded = await _seed_with_eligible_group_assignment(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/policies/group-role-mappings", json={"source_group_id": str(seeded["group_id"]), "resource_type": "ROLE", "resource_id": str(seeded["role_id"])})
        await client.post(f"/api/v1/assignments/{seeded['group_assignment_id']}/activate", json={"duration_hours": 4, "justification": "Activating."})
        deactivated = await client.post(f"/api/v1/assignments/{seeded['group_assignment_id']}/deactivate")
    assert deactivated.status_code == 200
    assert deactivated.json()["status"] == "ELIGIBLE"

    async with db_override.factory() as session:
        role_assignment = (await session.execute(select(AccessAssignment).where(AccessAssignment.user_id == seeded["member_id"], AccessAssignment.resource_type == "ROLE"))).scalars().one()
    assert role_assignment.status == "ELIGIBLE"
    assert role_assignment.activated_at is None


@pytest.mark.asyncio
async def test_revoking_the_group_cascades_to_revoke_the_mapped_role(db_override):
    seeded = await _seed_with_eligible_group_assignment(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/policies/group-role-mappings", json={"source_group_id": str(seeded["group_id"]), "resource_type": "ROLE", "resource_id": str(seeded["role_id"])})
        await client.post(f"/api/v1/assignments/{seeded['group_assignment_id']}/activate", json={"duration_hours": 4, "justification": "Activating."})
        revoked = await client.post(f"/api/v1/assignments/{seeded['group_assignment_id']}/revoke", json={"justification": "Offboarding this group access."})
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "REVOKED"

    async with db_override.factory() as session:
        role_assignment = (await session.execute(select(AccessAssignment).where(AccessAssignment.user_id == seeded["member_id"], AccessAssignment.resource_type == "ROLE"))).scalars().one()
    assert role_assignment.status == "REVOKED"


@pytest.mark.asyncio
async def test_cascade_never_touches_a_manually_granted_role_to_the_same_user(db_override, monkeypatch):
    """The one non-negotiable rule this whole feature carries: only an assignment tagged with
    group_role_mapping_id is ever cascaded. A DIFFERENT role, granted manually to the same user, must survive
    the group's full activate/deactivate/revoke lifecycle completely untouched."""
    seeded = await _seed_with_eligible_group_assignment(db_override.factory)
    authenticate_as("AccessPilot.Admin")

    async with db_override.factory() as session:
        other_role = Role(provider_id=seeded["provider_id"], external_id="r2", name="Unrelated Role", role_type="DIRECTORY_ROLE", status="ACTIVE")
        session.add(other_role)
        await session.commit()
        await session.refresh(other_role)
        other_role_id = other_role.id

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/policies/group-role-mappings", json={"source_group_id": str(seeded["group_id"]), "resource_type": "ROLE", "resource_id": str(seeded["role_id"])})
        manual = await client.post("/api/v1/assignments", json={"user_id": str(seeded["member_id"]), "resource_type": "ROLE", "resource_id": str(other_role_id), "assignment_type": "PERMANENT", "justification": "Manually granted by an admin.", "bypass_activation": True})
        assert manual.status_code == 201

        await client.post(f"/api/v1/assignments/{seeded['group_assignment_id']}/activate", json={"duration_hours": 4, "justification": "Activating."})
        await client.post(f"/api/v1/assignments/{seeded['group_assignment_id']}/deactivate")

    async with db_override.factory() as session:
        manual_assignment = (await session.execute(select(AccessAssignment).where(AccessAssignment.user_id == seeded["member_id"], AccessAssignment.resource_id == other_role_id))).scalars().one()
    assert manual_assignment.status == "ACTIVE"
