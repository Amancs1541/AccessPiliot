from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import AccessAssignment, Application, BusinessRole, BusinessRoleItem, BusinessRoleOwner, Group, IdentityProvider, Role, SodPolicy, SodPolicyEntity, User
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


async def _seed(session):
    provider = IdentityProvider(name="Microsoft Entra ID", type="MOCK", status="CONNECTED", tenant_id="t")
    session.add(provider)
    await session.flush()
    owner = User(provider_id=provider.id, external_id="owner", email="priya@x.com", display_name="Priya Nair", status="ACTIVE")
    target = User(provider_id=provider.id, external_id="target", email="sam@x.com", display_name="Sam Rivera", status="ACTIVE")
    group = Group(provider_id=provider.id, external_id="g-fin-l2", name="SG-FIN-L2", status="ACTIVE", is_privileged=False)
    application = Application(provider_id=provider.id, external_id="app-reporting", name="Reporting App", status="ACTIVE", app_roles=[{"id": "role-viewer", "name": "Viewer", "description": None}])
    session.add_all([owner, target, group, application])
    await session.commit()
    return {"provider_id": provider.id, "owner_id": owner.id, "target_id": target.id, "group_id": group.id, "application_id": application.id}


@pytest.mark.asyncio
async def test_create_business_role_with_items_and_owner_and_resource_reference_fields(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # The group's own resource_code/naming_convention are set directly on the resource first (the catalog
        # reference fields), then show up hydrated on the Business Role mapping below.
        tagged = await client.patch(f"/api/v1/business-roles/resources/group/{ids['group_id']}/reference", json={"resource_code": "RES-GRP-014", "naming_convention": "SG-{DEPT}-{LEVEL}"})
        created = await client.post("/api/v1/business-roles", json={
            "name": "Finance Analyst", "department": "Finance", "role_type": "BUSINESS", "risk_level": "MEDIUM",
            "owner_ids": [str(ids["owner_id"])],
            "items": [
                {"resource_type": "GROUP", "resource_id": str(ids["group_id"]), "it_role_label": "Finance-L2-ReadWrite"},
                {"resource_type": "APPLICATION", "resource_id": str(ids["application_id"]), "app_role_external_id": "role-viewer", "it_role_label": "Finance-Reporting-ReadOnly"},
            ],
        })
    assert tagged.status_code == 200 and tagged.json()["resource_code"] == "RES-GRP-014"
    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "DRAFT" and body["owners"][0]["display_name"] == "Priya Nair"
    by_label = {item["it_role_label"]: item for item in body["items"]}
    assert by_label["Finance-L2-ReadWrite"]["resource_code"] == "RES-GRP-014" and by_label["Finance-L2-ReadWrite"]["naming_convention"] == "SG-{DEPT}-{LEVEL}"
    assert by_label["Finance-L2-ReadWrite"]["provider_name"] == "Microsoft Entra ID"
    assert "Viewer" in by_label["Finance-Reporting-ReadOnly"]["resource_display_name"]


@pytest.mark.asyncio
async def test_one_entitlement_can_only_map_to_one_business_role_at_a_time(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
        other_group = Group(provider_id=ids["provider_id"], external_id="g-other", name="Other Group", status="ACTIVE", is_privileged=False)
        session.add(other_group)
        await session.commit()
        other_group_id = other_group.id
    authenticate_as("AccessPilot.Admin")
    item = {"resource_type": "GROUP", "resource_id": str(ids["group_id"])}
    other_item = {"resource_type": "GROUP", "resource_id": str(other_group_id)}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/api/v1/business-roles", json={"name": "Finance Analyst", "items": [item]})
        second = await client.post("/api/v1/business-roles", json={"name": "Finance Manager", "items": [item]})
        duplicate_in_same_role = await client.post("/api/v1/business-roles", json={"name": "Whatever", "items": [other_item, other_item]})
    assert first.status_code == 201
    assert second.status_code == 409 and second.json()["error"]["code"] == "ENTITLEMENT_ALREADY_MAPPED"
    assert duplicate_in_same_role.status_code == 422


@pytest.mark.asyncio
async def test_resource_code_must_be_unique_across_resources(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
        other_group = Group(provider_id=ids["provider_id"], external_id="g-other", name="Other Group", status="ACTIVE", is_privileged=False)
        session.add(other_group)
        await session.commit()
        other_group_id = other_group.id
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.patch(f"/api/v1/business-roles/resources/group/{ids['group_id']}/reference", json={"resource_code": "RES-001"})
        clash = await client.patch(f"/api/v1/business-roles/resources/group/{other_group_id}/reference", json={"resource_code": "RES-001"})
    assert first.status_code == 200 and clash.status_code == 409 and clash.json()["error"]["code"] == "RESOURCE_CODE_TAKEN"


@pytest.mark.asyncio
async def test_unmapped_entitlements_lists_everything_not_on_a_business_role(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        before = (await client.get("/api/v1/business-roles/unmapped-entitlements")).json()
        assert {(r["resource_type"], r["resource_id"]) for r in before} == {("GROUP", str(ids["group_id"])), ("APPLICATION", str(ids["application_id"]))}
        await client.post("/api/v1/business-roles", json={"name": "Finance Analyst", "items": [{"resource_type": "GROUP", "resource_id": str(ids["group_id"])}]})
        after = (await client.get("/api/v1/business-roles/unmapped-entitlements")).json()
    assert {(r["resource_type"], r["resource_id"]) for r in after} == {("APPLICATION", str(ids["application_id"]))}


@pytest.mark.asyncio
async def test_update_edit_status_lifecycle_and_an_archived_role_cannot_reactivate(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/business-roles", json={"name": "Finance Analyst", "items": [{"resource_type": "GROUP", "resource_id": str(ids["group_id"])}]})
        role_id = created.json()["id"]
        activated = await client.patch(f"/api/v1/business-roles/{role_id}", json={"status": "ACTIVE"})
        archived = await client.patch(f"/api/v1/business-roles/{role_id}", json={"status": "ARCHIVED"})
        reactivate = await client.patch(f"/api/v1/business-roles/{role_id}", json={"status": "ACTIVE"})
    assert activated.json()["status"] == "ACTIVE"
    assert archived.json()["status"] == "ARCHIVED"
    assert reactivate.status_code == 422


@pytest.mark.asyncio
async def test_delete_with_no_history_hard_deletes(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/business-roles", json={"name": "Finance Analyst", "items": [{"resource_type": "GROUP", "resource_id": str(ids["group_id"])}]})
        role_id = created.json()["id"]
        deleted = await client.delete(f"/api/v1/business-roles/{role_id}")
        missing = await client.get(f"/api/v1/business-roles/{role_id}")
    assert deleted.status_code == 204 and missing.status_code == 404
    async with db_override.factory() as session:
        assert (await session.scalars(select(BusinessRole))).first() is None
        assert (await session.scalars(select(BusinessRoleItem))).first() is None


@pytest.mark.asyncio
async def test_business_role_endpoints_are_admin_only(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.User", subject="regular")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        listed = await client.get("/api/v1/business-roles")
        created = await client.post("/api/v1/business-roles", json={"name": "Finance Analyst", "items": []})
    assert listed.status_code == 403 and created.status_code == 403


@pytest.mark.asyncio
async def test_duplicate_name_and_unknown_target_are_rejected(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/business-roles", json={"name": "Finance Analyst", "items": []})
        dup = await client.post("/api/v1/business-roles", json={"name": "Finance Analyst", "items": []})
        bad_target = await client.post("/api/v1/business-roles", json={"name": "Ghost Role", "items": [{"resource_type": "GROUP", "resource_id": str(uuid4())}]})
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "BUSINESS_ROLE_ALREADY_EXISTS"
    assert bad_target.status_code == 404


@pytest.mark.asyncio
async def test_assigning_an_active_role_fans_out_to_every_mapped_item(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/business-roles", json={
            "name": "Finance Analyst",
            "items": [
                {"resource_type": "GROUP", "resource_id": str(ids["group_id"]), "it_role_label": "Finance-L2-ReadWrite"},
                {"resource_type": "APPLICATION", "resource_id": str(ids["application_id"]), "app_role_external_id": "role-viewer"},
            ],
        })
        role_id = created.json()["id"]
        blocked = await client.post(f"/api/v1/business-roles/{role_id}/assign", json={"user_id": str(ids["target_id"]), "assignment_type": "PERMANENT", "justification": "Needs finance access"})
        await client.patch(f"/api/v1/business-roles/{role_id}", json={"status": "ACTIVE"})
        assigned = await client.post(f"/api/v1/business-roles/{role_id}/assign", json={"user_id": str(ids["target_id"]), "assignment_type": "PERMANENT", "justification": "Needs finance access"})
        role_after = await client.get(f"/api/v1/business-roles/{role_id}")
        holders = await client.get(f"/api/v1/business-roles/{role_id}/holders")
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "BUSINESS_ROLE_NOT_ACTIVE"
    assert assigned.status_code == 201
    body = assigned.json()
    assert body["user_display_name"] == "Sam Rivera" and len(body["results"]) == 2 and all(r["status"] == "CREATED" for r in body["results"])
    assert role_after.json()["assigned_user_count"] == 1
    assert len(holders.json()) == 1 and holders.json()[0]["user_display_name"] == "Sam Rivera" and len(holders.json()[0]["items"]) == 2


@pytest.mark.asyncio
async def test_an_assigned_role_archives_on_delete_instead_of_hard_deleting(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/business-roles", json={"name": "Finance Analyst", "items": [{"resource_type": "GROUP", "resource_id": str(ids["group_id"])}]})
        role_id = created.json()["id"]
        await client.patch(f"/api/v1/business-roles/{role_id}", json={"status": "ACTIVE"})
        await client.post(f"/api/v1/business-roles/{role_id}/assign", json={"user_id": str(ids["target_id"]), "assignment_type": "PERMANENT", "justification": "Needs finance access"})
        deleted = await client.delete(f"/api/v1/business-roles/{role_id}")
        still_there = await client.get(f"/api/v1/business-roles/{role_id}")
    assert deleted.status_code == 200 and deleted.json()["status"] == "ARCHIVED"
    assert still_there.status_code == 200 and still_there.json()["status"] == "ARCHIVED"


@pytest.mark.asyncio
async def test_assigning_a_role_with_no_items_or_an_unknown_user_is_rejected(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/business-roles", json={"name": "Empty Role", "items": []})
        role_id = created.json()["id"]
        await client.patch(f"/api/v1/business-roles/{role_id}", json={"status": "ACTIVE"})
        empty = await client.post(f"/api/v1/business-roles/{role_id}/assign", json={"user_id": str(ids["target_id"]), "assignment_type": "PERMANENT", "justification": "Needs finance access"})

        created2 = await client.post("/api/v1/business-roles", json={"name": "Finance Analyst", "items": [{"resource_type": "GROUP", "resource_id": str(ids["group_id"])}]})
        role_id2 = created2.json()["id"]
        await client.patch(f"/api/v1/business-roles/{role_id2}", json={"status": "ACTIVE"})
        unknown_user = await client.post(f"/api/v1/business-roles/{role_id2}/assign", json={"user_id": str(uuid4()), "assignment_type": "PERMANENT", "justification": "Needs finance access"})
    assert empty.status_code == 409 and empty.json()["error"]["code"] == "BUSINESS_ROLE_EMPTY"
    assert unknown_user.status_code == 404


@pytest.mark.asyncio
async def test_assign_endpoint_is_admin_only(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/business-roles", json={"name": "Finance Analyst", "items": [{"resource_type": "GROUP", "resource_id": str(ids["group_id"])}]})
        role_id = created.json()["id"]
        await client.patch(f"/api/v1/business-roles/{role_id}", json={"status": "ACTIVE"})
        authenticate_as("AccessPilot.User", subject="regular")
        denied = await client.post(f"/api/v1/business-roles/{role_id}/assign", json={"user_id": str(ids["target_id"]), "assignment_type": "PERMANENT", "justification": "Needs finance access"})
    assert denied.status_code == 403


@pytest.mark.asyncio
async def test_approver_sees_the_business_role_name_on_a_pending_item(db_override):
    """The request that prompted this: when a Business Role assignment needs approval, the approver must see
    which Business Role the item came from, not just the raw group/role/application name."""
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/business-roles", json={"name": "Finance Analyst", "items": [{"resource_type": "GROUP", "resource_id": str(ids["group_id"])}]})
        role_id = created.json()["id"]
        await client.patch(f"/api/v1/business-roles/{role_id}", json={"status": "ACTIVE"})
        assigned = await client.post(f"/api/v1/business-roles/{role_id}/assign", json={"user_id": str(ids["target_id"]), "assignment_type": "PERMANENT", "approver_id": str(ids["owner_id"]), "justification": "Needs finance access"})
        assert assigned.json()["results"][0]["assignment"]["status"] == "PENDING_APPROVAL"
        authenticate_as("AccessPilot.User", subject="owner")
        pending = (await client.get("/api/v1/assignments/pending-approval")).json()
    assert len(pending) == 1 and pending[0]["business_role_name"] == "Finance Analyst" and pending[0]["package_name"] is None


@pytest.mark.asyncio
async def test_assignment_batches_group_a_multi_item_role_grant_into_one_row(db_override):
    """The follow-up request: don't show a role's several mapped items as separate rows — one row per
    role_assignment_id, mirroring how package assignments already group."""
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/business-roles", json={
            "name": "Finance Analyst",
            "items": [
                {"resource_type": "GROUP", "resource_id": str(ids["group_id"])},
                {"resource_type": "APPLICATION", "resource_id": str(ids["application_id"]), "app_role_external_id": "role-viewer"},
            ],
        })
        role_id = created.json()["id"]
        await client.patch(f"/api/v1/business-roles/{role_id}", json={"status": "ACTIVE"})
        assigned = await client.post(f"/api/v1/business-roles/{role_id}/assign", json={"user_id": str(ids["target_id"]), "assignment_type": "PERMANENT", "justification": "Needs finance access"})
        batches = (await client.get("/api/v1/business-roles/assignment-batches")).json()
    assert len(batches) == 1
    batch = batches[0]
    assert batch["role_id"] == role_id and batch["role_name"] == "Finance Analyst" and batch["user_id"] == str(ids["target_id"])
    assert batch["role_assignment_id"] == assigned.json()["role_assignment_id"]
    assert sorted(batch["assignment_ids"]) == sorted(r["assignment"]["id"] for r in assigned.json()["results"])


@pytest.mark.asyncio
async def test_my_assignment_batches_scopes_to_the_approver_and_batches_pending_items(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/business-roles", json={
            "name": "Finance Analyst",
            "items": [
                {"resource_type": "GROUP", "resource_id": str(ids["group_id"])},
                {"resource_type": "APPLICATION", "resource_id": str(ids["application_id"]), "app_role_external_id": "role-viewer"},
            ],
        })
        role_id = created.json()["id"]
        await client.patch(f"/api/v1/business-roles/{role_id}", json={"status": "ACTIVE"})
        assigned = await client.post(f"/api/v1/business-roles/{role_id}/assign", json={"user_id": str(ids["target_id"]), "assignment_type": "PERMANENT", "approver_id": str(ids["owner_id"]), "justification": "Needs finance access"})

        authenticate_as("AccessPilot.User", subject="regular-other")
        unrelated = (await client.get("/api/v1/business-roles/my-assignment-batches")).json()

        authenticate_as("AccessPilot.User", subject="owner")
        mine = (await client.get("/api/v1/business-roles/my-assignment-batches")).json()
    assert unrelated == []
    assert len(mine) == 1 and mine[0]["role_name"] == "Finance Analyst"
    assert sorted(mine[0]["assignment_ids"]) == sorted(r["assignment"]["id"] for r in assigned.json()["results"])


@pytest.mark.asyncio
async def test_analytics_reports_counts_no_owners_unmapped_and_open_sod_conflicts(db_override):
    """Plan Step 6: a live-computed stats panel. Builds one ACTIVE, owned, assigned role; one DRAFT, unowned,
    empty role; leaves the seeded application unmapped; and puts the ACTIVE role's own item into a real SoD
    conflict with a second group that the same holder already holds, to prove the open-conflict count is real,
    not just 'referenced in any SoD policy'."""
    async with db_override.factory() as session:
        ids = await _seed(session)
        conflict_group = Group(provider_id=ids["provider_id"], external_id="g-conflict", name="Conflict Group", status="ACTIVE", is_privileged=False)
        session.add(conflict_group)
        await session.commit()
        await session.refresh(conflict_group)
        conflict_group_id = conflict_group.id

    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        active_role = (await client.post("/api/v1/business-roles", json={"name": "Finance Analyst", "owner_ids": [str(ids["owner_id"])], "items": [{"resource_type": "GROUP", "resource_id": str(ids["group_id"])}]})).json()
        await client.patch(f"/api/v1/business-roles/{active_role['id']}", json={"status": "ACTIVE"})
        await client.post(f"/api/v1/business-roles/{active_role['id']}/assign", json={"user_id": str(ids["target_id"]), "assignment_type": "PERMANENT", "justification": "Needs finance access"})

        draft_role = (await client.post("/api/v1/business-roles", json={"name": "Unowned Draft Role", "items": []})).json()

        authenticate_as("AccessPilot.SoDAdmin")
        sod = await client.post("/api/v1/sod/policies", json={
            "name": "Finance Analyst vs Conflict Group",
            "entities": [
                {"conflict_side": "A", "entity_type": "GROUP", "entity_id": str(conflict_group_id)},
                {"conflict_side": "B", "entity_type": "BUSINESS_ROLE", "entity_id": active_role["id"]},
            ],
        })
        assert sod.status_code == 201
        authenticate_as("AccessPilot.Admin")

    async with db_override.factory() as session:
        # The same holder already holds the OTHER side of the conflict directly — this is what makes it a real,
        # open violation rather than just a policy that merely references the role.
        session.add(AccessAssignment(provider_id=ids["provider_id"], user_id=ids["target_id"], resource_type="GROUP", resource_id=conflict_group_id, assignment_type="PERMANENT", status="ACTIVE", justification="Conflicting direct grant."))
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        analytics = (await client.get("/api/v1/business-roles/analytics")).json()

    assert analytics["total_roles"] == 2
    assert analytics["active_roles"] == 1
    assert analytics["draft_roles"] == 1
    assert analytics["roles_with_no_owner"] == 1  # the draft role; the active one has Priya as owner
    assert analytics["unmapped_entitlements"] >= 1  # the seeded Reporting App was never mapped to anything
    assert analytics["total_assigned_users"] == 1
    assert analytics["roles_with_open_sod_conflicts"] == 1
    assert analytics["top_roles_by_holders"] == [{"name": "Finance Analyst", "count": 1}]


@pytest.mark.asyncio
async def test_analytics_endpoint_is_admin_only(db_override):
    async with db_override.factory() as session:
        await _seed(session)
    authenticate_as("AccessPilot.User", subject="regular")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/business-roles/analytics")
    assert response.status_code == 403
