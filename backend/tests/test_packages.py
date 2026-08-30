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
from app.models import AccessAssignment, AccessPackage, AccessPackageAssignment, Application, AuditLog, Group, IdentityProvider, Role, User, UserGroup
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


async def _seed_directory(factory):
    async with factory() as session:
        provider = IdentityProvider(name="Directory", type="MOCK", status="CONNECTED", tenant_id="t")
        session.add(provider)
        await session.flush()
        target_user = User(provider_id=provider.id, external_id="target-user", email="target@x.com", display_name="Target User", status="ACTIVE")
        approver = User(provider_id=provider.id, external_id="admin-oid", email="admin@x.com", display_name="Admin User", status="ACTIVE")
        group = Group(provider_id=provider.id, external_id="g1", name="Security Team", status="ACTIVE", is_privileged=False)
        role = Role(provider_id=provider.id, external_id="r1", name="Reports Reader", role_type="DIRECTORY_ROLE", status="ACTIVE", is_privileged=False)
        application = Application(
            provider_id=provider.id, external_id="app-001", name="Reporting Portal", status="ACTIVE",
            app_roles=[{"id": "approle-001", "name": "Viewer", "description": "Read-only"}],
        )
        session.add_all([target_user, approver, group, role, application])
        await session.commit()
        return {"provider_id": provider.id, "user_id": target_user.id, "approver_id": approver.id, "group_id": group.id, "role_id": role.id, "application_id": application.id}


def _package_payload(ids: dict) -> dict:
    return {
        "name": "Starter Kit",
        "description": "Baseline access for new hires",
        "items": [
            {"resource_type": "GROUP", "resource_id": str(ids["group_id"])},
            {"resource_type": "ROLE", "resource_id": str(ids["role_id"])},
            {"resource_type": "APPLICATION", "resource_id": str(ids["application_id"]), "app_role_external_id": "approle-001"},
        ],
    }


@pytest.mark.asyncio
async def test_create_package_with_group_role_and_application_item(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/packages", json=_package_payload(ids))
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Starter Kit"
    assert body["status"] == "ACTIVE"
    assert len(body["items"]) == 3
    resource_types = {item["resource_type"] for item in body["items"]}
    assert resource_types == {"GROUP", "ROLE", "APPLICATION"}


@pytest.mark.asyncio
async def test_create_package_rejects_unknown_resource_id(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    payload = {"name": "Bad Kit", "items": [{"resource_type": "GROUP", "resource_id": "00000000-0000-0000-0000-000000000000"}]}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/packages", json=payload)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "GROUP_NOT_FOUND"


@pytest.mark.asyncio
async def test_create_package_application_item_requires_app_role(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    payload = {"name": "No Role Kit", "items": [{"resource_type": "APPLICATION", "resource_id": str(ids["application_id"])}]}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/packages", json=payload)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_package_rejects_duplicate_item(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    payload = {"name": "Dup Kit", "items": [{"resource_type": "GROUP", "resource_id": str(ids["group_id"])}, {"resource_type": "GROUP", "resource_id": str(ids["group_id"])}]}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/packages", json=payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "DUPLICATE_PACKAGE_ITEM"


@pytest.mark.asyncio
async def test_create_package_rejects_duplicate_name(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/packages", json=_package_payload(ids))
        second = await client.post("/api/v1/packages", json=_package_payload(ids))
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "PACKAGE_ALREADY_EXISTS"


@pytest.mark.asyncio
async def test_assign_package_creates_one_assignment_per_item_as_eligible_then_activates(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/packages", json=_package_payload(ids))
        package_id = created.json()["id"]
        assigned = await client.post(f"/api/v1/packages/{package_id}/assign", json={"user_id": str(ids["user_id"]), "assignment_type": "PERMANENT", "justification": "Test justification."})
        member = assigned.json()["members"][0]
        first_item_id = member["results"][0]["assignment"]["id"]
        activated = await client.post(f"/api/v1/assignments/{first_item_id}/activate", json={"duration_hours": 2, "justification": "Test justification."})
    assert assigned.status_code == 201
    body = assigned.json()
    assert len(body["members"]) == 1
    assert member["user_id"] == str(ids["user_id"])
    assert len(member["results"]) == 3
    assert all(result["status"] == "CREATED" for result in member["results"])
    assert all(result["assignment"]["status"] == "ELIGIBLE" for result in member["results"])
    assert activated.status_code == 200
    assert activated.json()["status"] == "ACTIVE"

    async with db_override.factory() as session:
        batch_rows = (await session.execute(select(AccessPackageAssignment).where(AccessPackageAssignment.package_assignment_id == UUID(member["package_assignment_id"])))).scalars().all()
        assert len(batch_rows) == 3


@pytest.mark.asyncio
async def test_assign_package_with_approver_does_not_touch_existing_access_until_approved(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Pre-existing direct assignment to the same group, already ACTIVE.
        direct = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "justification": "Test justification."})
        direct_id = direct.json()["id"]
        await client.post(f"/api/v1/assignments/{direct_id}/activate", json={"duration_hours": 2, "justification": "Test justification."})

        created = await client.post("/api/v1/packages", json=_package_payload(ids))
        package_id = created.json()["id"]
        assigned = await client.post(f"/api/v1/packages/{package_id}/assign", json={"user_id": str(ids["user_id"]), "assignment_type": "PERMANENT", "approver_id": str(ids["approver_id"]), "justification": "Test justification."})
    assert assigned.status_code == 201
    member = assigned.json()["members"][0]
    assert all(result["assignment"]["status"] == "PENDING_APPROVAL" for result in member["results"])

    async with db_override.factory() as session:
        still_active = await session.get(AccessAssignment, UUID(direct_id))
        assert still_active.status == "ACTIVE"  # untouched while the package assignment is only pending


@pytest.mark.asyncio
async def test_approving_a_package_item_individually_via_existing_endpoint(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/packages", json=_package_payload(ids))
        package_id = created.json()["id"]
        assigned = await client.post(f"/api/v1/packages/{package_id}/assign", json={"user_id": str(ids["user_id"]), "assignment_type": "PERMANENT", "approver_id": str(ids["approver_id"]), "justification": "Test justification."})
        first_item_assignment_id = assigned.json()["members"][0]["results"][0]["assignment"]["id"]

        approved = await client.post(f"/api/v1/assignments/{first_item_assignment_id}/approve", json={"justification": "Test justification."})
        assert approved.status_code == 200
        assert approved.json()["status"] == "ELIGIBLE"
        activated = await client.post(f"/api/v1/assignments/{first_item_assignment_id}/activate", json={"duration_hours": 2, "justification": "Test justification."})
    assert activated.status_code == 200
    assert activated.json()["status"] == "ACTIVE"


@pytest.mark.asyncio
async def test_reassigning_same_package_to_same_user_does_not_supersede_until_activated(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/packages", json=_package_payload(ids))
        package_id = created.json()["id"]
        first = await client.post(f"/api/v1/packages/{package_id}/assign", json={"user_id": str(ids["user_id"]), "assignment_type": "PERMANENT", "justification": "Test justification."})
        first_ids = [UUID(r["assignment"]["id"]) for r in first.json()["members"][0]["results"]]
        for assignment_id in first_ids:
            await client.post(f"/api/v1/assignments/{assignment_id}/activate", json={"duration_hours": 2, "justification": "Test justification."})

        second = await client.post(f"/api/v1/packages/{package_id}/assign", json={"user_id": str(ids["user_id"]), "assignment_type": "PERMANENT", "justification": "Test justification."})
        second_ids = [UUID(r["assignment"]["id"]) for r in second.json()["members"][0]["results"]]
    assert second.status_code == 201
    assert all(result["assignment"]["status"] == "ELIGIBLE" for result in second.json()["members"][0]["results"])

    async with db_override.factory() as session:
        for assignment_id in first_ids:
            old = await session.get(AccessAssignment, assignment_id)
            assert old.status == "ACTIVE"  # untouched — the replacement batch is only eligible so far

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for assignment_id in second_ids:
            activated = await client.post(f"/api/v1/assignments/{assignment_id}/activate", json={"duration_hours": 2, "justification": "Test justification."})
            assert activated.status_code == 200

    async with db_override.factory() as session:
        for assignment_id in first_ids:
            old = await session.get(AccessAssignment, assignment_id)
            assert old.status == "REVOKED"


@pytest.mark.asyncio
async def test_activating_package_items_partial_failure_leaves_others_active(db_override, monkeypatch):
    """Package items are activated individually, one Graph call each — a failure activating one item (e.g. the
    ROLE) leaves it ELIGIBLE while the other items activate to real access unaffected."""
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")

    async def selectively_failing_activate(self, request):
        if request.get("resource_type") == "ROLE":
            from app.providers.graph_client import GraphError
            raise GraphError("PROVIDER_UNAVAILABLE", "boom", 503)
        if request.get("resource_type") == "GROUP":
            await self.add_group_member(request["target_external_id"], request["user_external_id"])
        if request.get("resource_type") == "APPLICATION":
            self.app_role_assignments.add((request["target_external_id"], request["app_role_external_id"], request["user_external_id"]))
        return True
    monkeypatch.setattr("app.providers.mock.MockProvider.activate_assignment", selectively_failing_activate)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/packages", json=_package_payload(ids))
        package_id = created.json()["id"]
        assigned = await client.post(f"/api/v1/packages/{package_id}/assign", json={"user_id": str(ids["user_id"]), "assignment_type": "PERMANENT", "justification": "Test justification."})
        results = assigned.json()["members"][0]["results"]
        assert all(result["status"] == "CREATED" for result in results)  # creation itself never touches the provider

        outcomes = {}
        for result in results:
            item_id = result["assignment"]["id"]
            activated = await client.post(f"/api/v1/assignments/{item_id}/activate", json={"duration_hours": 2, "justification": "Test justification."})
            outcomes[result["resource_type"]] = activated.status_code

    assert outcomes["ROLE"] in (502, 503)
    assert outcomes["GROUP"] == 200
    assert outcomes["APPLICATION"] == 200


@pytest.mark.asyncio
async def test_update_package_renames_and_redescribes(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/packages", json=_package_payload(ids))
        package_id = created.json()["id"]
        updated = await client.patch(f"/api/v1/packages/{package_id}", json={"name": "Renamed Kit", "description": "New description"})
    assert updated.status_code == 200
    assert updated.json()["name"] == "Renamed Kit"
    assert updated.json()["description"] == "New description"
    assert len(updated.json()["items"]) == 3  # items untouched when not provided


@pytest.mark.asyncio
async def test_update_package_replaces_items(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/packages", json=_package_payload(ids))
        package_id = created.json()["id"]
        updated = await client.patch(f"/api/v1/packages/{package_id}", json={"items": [{"resource_type": "GROUP", "resource_id": str(ids["group_id"])}]})
    assert updated.status_code == 200
    assert len(updated.json()["items"]) == 1
    assert updated.json()["items"][0]["resource_type"] == "GROUP"


@pytest.mark.asyncio
async def test_update_package_rejects_rename_to_existing_name(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/api/v1/packages", json=_package_payload(ids))
        second_payload = {**_package_payload(ids), "name": "Second Kit"}
        second = await client.post("/api/v1/packages", json=second_payload)
        conflict = await client.patch(f"/api/v1/packages/{second.json()['id']}", json={"name": first.json()["name"]})
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "PACKAGE_ALREADY_EXISTS"


@pytest.mark.asyncio
async def test_update_package_allowed_even_after_assignment_history(db_override):
    """Editing a package's items only affects FUTURE assignments — it's allowed even once it's been used."""
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/packages", json=_package_payload(ids))
        package_id = created.json()["id"]
        await client.post(f"/api/v1/packages/{package_id}/assign", json={"user_id": str(ids["user_id"]), "assignment_type": "PERMANENT", "justification": "Test justification."})
        updated = await client.patch(f"/api/v1/packages/{package_id}", json={"description": "Updated after use"})
    assert updated.status_code == 200
    assert updated.json()["description"] == "Updated after use"


@pytest.mark.asyncio
async def test_delete_unused_package_is_hard_deleted(db_override):
    """A package that has never been assigned to anyone is safe to remove outright — nothing references it."""
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/packages", json=_package_payload(ids))
        package_id = created.json()["id"]
        deleted = await client.delete(f"/api/v1/packages/{package_id}")
        listed = await client.get("/api/v1/packages")
        detail = await client.get(f"/api/v1/packages/{package_id}")
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True, "id": package_id}
    assert listed.json() == []
    assert detail.status_code == 404
    assert detail.json()["error"]["code"] == "PACKAGE_NOT_FOUND"

    async with db_override.factory() as session:
        remaining = (await session.execute(select(AccessPackage).where(AccessPackage.id == UUID(package_id)))).scalars().first()
        assert remaining is None


@pytest.mark.asyncio
async def test_delete_package_with_assignment_history_archives_instead(db_override):
    """A package that's already been assigned must not be hard-deleted — assignment history/audit still reference
    it. Deleting it archives it (no longer assignable, but still visible/traceable) instead."""
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/packages", json=_package_payload(ids))
        package_id = created.json()["id"]
        await client.post(f"/api/v1/packages/{package_id}/assign", json={"user_id": str(ids["user_id"]), "assignment_type": "PERMANENT", "justification": "Test justification."})
        deleted = await client.delete(f"/api/v1/packages/{package_id}")
        second_assign = await client.post(f"/api/v1/packages/{package_id}/assign", json={"user_id": str(ids["user_id"]), "assignment_type": "PERMANENT", "justification": "Test justification."})
    assert deleted.status_code == 200
    assert deleted.json()["status"] == "ARCHIVED"
    assert second_assign.status_code == 409
    assert second_assign.json()["error"]["code"] == "PACKAGE_ARCHIVED"


@pytest.mark.asyncio
async def test_package_endpoints_require_admin_permissions(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.User")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        listed = await client.get("/api/v1/packages")
        created = await client.post("/api/v1/packages", json=_package_payload(ids))
    assert listed.status_code == 403
    assert created.status_code == 403


@pytest.mark.asyncio
async def test_list_assignment_batches_groups_items_by_package_assignment_id(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/packages", json=_package_payload(ids))
        package_id = created.json()["id"]
        assigned = await client.post(f"/api/v1/packages/{package_id}/assign", json={"user_id": str(ids["user_id"]), "assignment_type": "PERMANENT", "justification": "Test justification."})
        batches = await client.get("/api/v1/packages/assignment-batches")
    assert batches.status_code == 200
    body = batches.json()
    assert len(body) == 1
    assert body[0]["package_assignment_id"] == assigned.json()["members"][0]["package_assignment_id"]
    assert len(body[0]["assignment_ids"]) == 3


@pytest.mark.asyncio
async def test_my_assignment_batches_is_available_to_a_non_admin_designated_approver(db_override):
    """Regression: the Approvals page must be able to group a package assignment for a non-admin approver too —
    PACKAGE_READ (Admin-only) must not gate this, or every non-admin approver silently loses batch grouping."""
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/packages", json=_package_payload(ids))
        package_id = created.json()["id"]
        assigned = await client.post(f"/api/v1/packages/{package_id}/assign", json={"user_id": str(ids["user_id"]), "assignment_type": "PERMANENT", "approver_id": str(ids["approver_id"]), "justification": "Test justification."})

    # The designated approver is a plain User, not an Admin — PACKAGE_READ would deny them.
    authenticate_as("AccessPilot.User", subject="admin-oid")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        mine = await client.get("/api/v1/packages/my-assignment-batches")
    assert mine.status_code == 200
    body = mine.json()
    assert len(body) == 1
    assert body[0]["package_assignment_id"] == assigned.json()["members"][0]["package_assignment_id"]
    assert len(body[0]["assignment_ids"]) == 3

    # An unrelated user who is not the designated approver sees nothing.
    authenticate_as("AccessPilot.User", subject="someone-else-oid")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        theirs = await client.get("/api/v1/packages/my-assignment-batches")
    assert theirs.status_code == 200
    assert theirs.json() == []


@pytest.mark.asyncio
async def test_assign_package_to_group_creates_one_batch_per_member(db_override):
    ids = await _seed_directory(db_override.factory)
    async with db_override.factory() as session:
        provider_id = ids["provider_id"]
        second_user = User(provider_id=provider_id, external_id="second-user", email="second@x.com", display_name="Second User", status="ACTIVE")
        session.add(second_user)
        await session.flush()
        session.add_all([
            UserGroup(user_id=ids["user_id"], group_id=ids["group_id"], source="SYNC"),
            UserGroup(user_id=second_user.id, group_id=ids["group_id"], source="SYNC"),
        ])
        await session.commit()
        second_user_id = second_user.id

    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/packages", json=_package_payload(ids))
        package_id = created.json()["id"]
        assigned = await client.post(f"/api/v1/packages/{package_id}/assign", json={"group_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "justification": "Test justification."})
    assert assigned.status_code == 201
    body = assigned.json()
    assert len(body["members"]) == 2
    member_user_ids = {member["user_id"] for member in body["members"]}
    assert member_user_ids == {str(ids["user_id"]), str(second_user_id)}
    for member in body["members"]:
        assert len(member["results"]) == 3
        assert all(result["status"] == "CREATED" for result in member["results"])
    batch_ids = {member["package_assignment_id"] for member in body["members"]}
    assert len(batch_ids) == 2  # each member gets their own distinct batch


@pytest.mark.asyncio
async def test_assign_package_to_empty_group_returns_conflict(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/packages", json=_package_payload(ids))
        package_id = created.json()["id"]
        assigned = await client.post(f"/api/v1/packages/{package_id}/assign", json={"group_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "justification": "Test justification."})
    assert assigned.status_code == 409
    assert assigned.json()["error"]["code"] == "GROUP_EMPTY"


@pytest.mark.asyncio
async def test_requestable_packages_empty_for_ineligible_user(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/packages", json=_package_payload(ids))

    authenticate_as("AccessPilot.User", subject="target-user")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        requestable = await client.get("/api/v1/packages/requestable")
    assert requestable.status_code == 200
    assert requestable.json() == []


@pytest.mark.asyncio
async def test_set_eligibility_by_individual_user_makes_package_requestable(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/packages", json=_package_payload(ids))
        package_id = created.json()["id"]
        set_eligibility = await client.put(f"/api/v1/packages/{package_id}/eligibility", json={"principals": [{"principal_type": "USER", "principal_id": str(ids["user_id"])}], "default_approver_id": str(ids["approver_id"])})
    assert set_eligibility.status_code == 200
    assert set_eligibility.json()["default_approver_id"] == str(ids["approver_id"])
    assert len(set_eligibility.json()["eligible_principals"]) == 1

    authenticate_as("AccessPilot.User", subject="target-user")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        requestable = await client.get("/api/v1/packages/requestable")
    assert requestable.status_code == 200
    assert len(requestable.json()) == 1
    assert requestable.json()[0]["id"] == package_id


@pytest.mark.asyncio
async def test_set_eligibility_deduplicates_a_repeated_principal_instead_of_erroring(db_override):
    """Real bug: access_package_eligibility has a unique (package_id, principal_type, principal_id) constraint —
    submitting the same principal twice (trivial from the UI: add a row, its picker still shows an already-used
    option) used to hit that constraint as an unhandled IntegrityError, silently rolling back the whole update.
    The count staying at 1 no matter how many times "add" was clicked was the visible symptom."""
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/packages", json=_package_payload(ids))
        package_id = created.json()["id"]
        set_eligibility = await client.put(f"/api/v1/packages/{package_id}/eligibility", json={"principals": [
            {"principal_type": "USER", "principal_id": str(ids["user_id"])},
            {"principal_type": "USER", "principal_id": str(ids["user_id"])},
        ]})
    assert set_eligibility.status_code == 200
    assert len(set_eligibility.json()["eligible_principals"]) == 1


@pytest.mark.asyncio
async def test_set_eligibility_by_group_includes_its_members(db_override):
    ids = await _seed_directory(db_override.factory)
    async with db_override.factory() as session:
        session.add(UserGroup(user_id=ids["user_id"], group_id=ids["group_id"], source="SYNC"))
        await session.commit()

    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/packages", json=_package_payload(ids))
        package_id = created.json()["id"]
        await client.put(f"/api/v1/packages/{package_id}/eligibility", json={"principals": [{"principal_type": "GROUP", "principal_id": str(ids["group_id"])}]})

    authenticate_as("AccessPilot.User", subject="target-user")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        requestable = await client.get("/api/v1/packages/requestable")
    assert len(requestable.json()) == 1


@pytest.mark.asyncio
async def test_request_package_with_default_approver_creates_pending_assignment(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/packages", json=_package_payload(ids))
        package_id = created.json()["id"]
        await client.put(f"/api/v1/packages/{package_id}/eligibility", json={"principals": [{"principal_type": "USER", "principal_id": str(ids["user_id"])}], "default_approver_id": str(ids["approver_id"])})

    authenticate_as("AccessPilot.User", subject="target-user")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        requested = await client.post(f"/api/v1/packages/{package_id}/request", json={"assignment_type": "PERMANENT", "justification": "Test justification."})
    assert requested.status_code == 201
    body = requested.json()
    assert body["user_id"] == str(ids["user_id"])
    assert all(result["assignment"]["status"] == "PENDING_APPROVAL" for result in body["results"])
    assert all(result["assignment"]["approved_by"] == str(ids["approver_id"]) for result in body["results"])


@pytest.mark.asyncio
async def test_request_package_without_default_approver_is_eligible_not_active(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/packages", json=_package_payload(ids))
        package_id = created.json()["id"]
        await client.put(f"/api/v1/packages/{package_id}/eligibility", json={"principals": [{"principal_type": "USER", "principal_id": str(ids["user_id"])}]})

    authenticate_as("AccessPilot.User", subject="target-user")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        requested = await client.post(f"/api/v1/packages/{package_id}/request", json={"assignment_type": "PERMANENT", "justification": "Test justification."})
    assert requested.status_code == 201
    assert all(result["assignment"]["status"] == "ELIGIBLE" for result in requested.json()["results"])


@pytest.mark.asyncio
async def test_request_package_denied_for_ineligible_user(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/packages", json=_package_payload(ids))
        package_id = created.json()["id"]

    authenticate_as("AccessPilot.User", subject="target-user")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        requested = await client.post(f"/api/v1/packages/{package_id}/request", json={"assignment_type": "PERMANENT", "justification": "Test justification."})
    assert requested.status_code == 403
    assert requested.json()["error"]["code"] == "NOT_ELIGIBLE"


@pytest.mark.asyncio
async def test_set_package_eligibility_requires_admin(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/packages", json=_package_payload(ids))
        package_id = created.json()["id"]

    authenticate_as("AccessPilot.User", subject="someone-else")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put(f"/api/v1/packages/{package_id}/eligibility", json={"principals": []})
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_assign_package_requires_exactly_one_of_user_or_group(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/packages", json=_package_payload(ids))
        package_id = created.json()["id"]
        neither = await client.post(f"/api/v1/packages/{package_id}/assign", json={"assignment_type": "PERMANENT", "justification": "Test justification."})
        both = await client.post(f"/api/v1/packages/{package_id}/assign", json={"user_id": str(ids["user_id"]), "group_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "justification": "Test justification."})
    assert neither.status_code == 422
    assert both.status_code == 422


@pytest.mark.asyncio
async def test_my_package_batches_lets_the_target_user_group_their_own_package_items(db_override):
    """Distinct from /my-assignment-batches (approver-scoped): this is scoped to the RECIPIENT of the package, so
    the end-user My Access page can show one row (and one Activate button) per package instead of per item."""
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/packages", json=_package_payload(ids))
        package_id = created.json()["id"]
        assigned = await client.post(f"/api/v1/packages/{package_id}/assign", json={"user_id": str(ids["user_id"]), "assignment_type": "PERMANENT", "justification": "Test justification."})

    authenticate_as("AccessPilot.User", subject="target-user")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        mine = await client.get("/api/v1/packages/my-package-batches")
    assert mine.status_code == 200
    body = mine.json()
    assert len(body) == 1
    assert body[0]["package_assignment_id"] == assigned.json()["members"][0]["package_assignment_id"]
    assert len(body[0]["assignment_ids"]) == 3

    # The Admin who created it (not the recipient) sees nothing here.
    authenticate_as("AccessPilot.User", subject="admin-oid")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        theirs = await client.get("/api/v1/packages/my-package-batches")
    assert theirs.status_code == 200
    assert theirs.json() == []


async def _seed_fallback_approver(factory, provider_id) -> UUID:
    async with factory() as session:
        fallback = User(provider_id=provider_id, external_id="fallback-oid", email="fallback@x.com", display_name="Fallback Approver", status="ACTIVE")
        session.add(fallback)
        await session.commit()
        return fallback.id


@pytest.mark.asyncio
async def test_fallback_approver_can_approve_a_package_request_when_primary_has_not(db_override):
    """Either the primary or the fallback approver configured on the package may approve — whichever acts first."""
    ids = await _seed_directory(db_override.factory)
    fallback_id = await _seed_fallback_approver(db_override.factory, ids["provider_id"])
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/packages", json=_package_payload(ids))
        package_id = created.json()["id"]
        eligibility = await client.put(f"/api/v1/packages/{package_id}/eligibility", json={"principals": [{"principal_type": "USER", "principal_id": str(ids["user_id"])}], "default_approver_id": str(ids["approver_id"]), "default_fallback_approver_id": str(fallback_id)})
    assert eligibility.status_code == 200
    assert eligibility.json()["default_fallback_approver_id"] == str(fallback_id)

    authenticate_as("AccessPilot.User", subject="target-user")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        requested = await client.post(f"/api/v1/packages/{package_id}/request", json={"assignment_type": "PERMANENT", "justification": "Need it for onboarding."})
    assert requested.status_code == 201
    item_id = requested.json()["results"][0]["assignment"]["id"]
    assert requested.json()["results"][0]["assignment"]["approved_by"] == str(ids["approver_id"])
    assert requested.json()["results"][0]["assignment"]["fallback_approver_id"] == str(fallback_id)

    # The fallback approver (not the primary) approves it.
    authenticate_as("AccessPilot.User", subject="fallback-oid")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        mine = await client.get("/api/v1/assignments/pending-approval")
        approved = await client.post(f"/api/v1/assignments/{item_id}/approve", json={"justification": "Approved as fallback."})
    assert len(mine.json()) >= 1
    assert approved.status_code == 200
    assert approved.json()["status"] == "ELIGIBLE"
    assert approved.json()["approved_by"] == str(fallback_id)


@pytest.mark.asyncio
async def test_someone_who_is_neither_approver_nor_fallback_cannot_approve_package_request(db_override):
    ids = await _seed_directory(db_override.factory)
    fallback_id = await _seed_fallback_approver(db_override.factory, ids["provider_id"])
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/packages", json=_package_payload(ids))
        package_id = created.json()["id"]
        await client.put(f"/api/v1/packages/{package_id}/eligibility", json={"principals": [{"principal_type": "USER", "principal_id": str(ids["user_id"])}], "default_approver_id": str(ids["approver_id"]), "default_fallback_approver_id": str(fallback_id)})

    authenticate_as("AccessPilot.User", subject="target-user")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        requested = await client.post(f"/api/v1/packages/{package_id}/request", json={"assignment_type": "PERMANENT", "justification": "Need it for onboarding."})
    item_id = requested.json()["results"][0]["assignment"]["id"]

    authenticate_as("AccessPilot.User", subject="someone-else-oid")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/api/v1/assignments/{item_id}/approve", json={"justification": "Trying anyway."})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ACCESS_DENIED"


@pytest.mark.asyncio
async def test_admin_assign_package_with_ad_hoc_approver_still_attaches_configured_fallback(db_override):
    """The package's configured fallback applies even when an admin picks a different one-off approver_id at
    assign time (rather than relying on the package's own default_approver_id via self-request)."""
    ids = await _seed_directory(db_override.factory)
    fallback_id = await _seed_fallback_approver(db_override.factory, ids["provider_id"])
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/packages", json=_package_payload(ids))
        package_id = created.json()["id"]
        await client.put(f"/api/v1/packages/{package_id}/eligibility", json={"principals": [], "default_fallback_approver_id": str(fallback_id)})
        assigned = await client.post(f"/api/v1/packages/{package_id}/assign", json={"user_id": str(ids["user_id"]), "assignment_type": "PERMANENT", "approver_id": str(ids["approver_id"]), "justification": "Test justification."})
    item = assigned.json()["members"][0]["results"][0]["assignment"]
    assert item["approved_by"] == str(ids["approver_id"])
    assert item["fallback_approver_id"] == str(fallback_id)


@pytest.mark.asyncio
async def test_create_package_with_approver_flow_setup_in_one_call(db_override):
    """The whole 'setup --> then add items' creation flow: name/description, who can request it, the approver +
    fallback approver + escalation window, and the items, all in a single POST /packages call."""
    ids = await _seed_directory(db_override.factory)
    fallback_id = await _seed_fallback_approver(db_override.factory, ids["provider_id"])
    authenticate_as("AccessPilot.Admin")
    payload = {
        **_package_payload(ids),
        "principals": [{"principal_type": "USER", "principal_id": str(ids["user_id"])}],
        "default_approver_id": str(ids["approver_id"]),
        "default_fallback_approver_id": str(fallback_id),
        "fallback_unlock_hours": 24,
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/packages", json=payload)
    assert created.status_code == 201
    body = created.json()
    assert body["default_approver_id"] == str(ids["approver_id"])
    assert body["default_fallback_approver_id"] == str(fallback_id)
    assert body["fallback_unlock_hours"] == 24
    assert len(body["eligible_principals"]) == 1
    assert len(body["items"]) == 3


@pytest.mark.asyncio
async def test_create_package_rejects_fallback_unlock_hours_without_fallback_approver(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    payload = {**_package_payload(ids), "fallback_unlock_hours": 24}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/packages", json=payload)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_fallback_approver_cannot_act_before_the_escalation_window_elapses(db_override):
    ids = await _seed_directory(db_override.factory)
    fallback_id = await _seed_fallback_approver(db_override.factory, ids["provider_id"])
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/packages", json={
            **_package_payload(ids),
            "principals": [{"principal_type": "USER", "principal_id": str(ids["user_id"])}],
            "default_approver_id": str(ids["approver_id"]),
            "default_fallback_approver_id": str(fallback_id),
            "fallback_unlock_hours": 24,
        })
        package_id = created.json()["id"]

    authenticate_as("AccessPilot.User", subject="target-user")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        requested = await client.post(f"/api/v1/packages/{package_id}/request", json={"assignment_type": "PERMANENT", "justification": "Need it now."})
    item_id = requested.json()["results"][0]["assignment"]["id"]
    assert requested.json()["results"][0]["assignment"]["fallback_unlock_at"] is not None

    # The fallback tries immediately — the 24-hour window hasn't elapsed yet.
    authenticate_as("AccessPilot.User", subject="fallback-oid")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        too_early = await client.post(f"/api/v1/assignments/{item_id}/approve", json={"justification": "Trying early."})
    assert too_early.status_code == 403
    assert too_early.json()["error"]["code"] == "FALLBACK_NOT_YET_AVAILABLE"

    # The primary approver, meanwhile, may still act at any time.
    authenticate_as("AccessPilot.User", subject="admin-oid")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        approved = await client.post(f"/api/v1/assignments/{item_id}/approve", json={"justification": "Approved by primary."})
    assert approved.status_code == 200
    assert approved.json()["status"] == "ELIGIBLE"


@pytest.mark.asyncio
async def test_fallback_approver_can_act_once_the_escalation_window_has_elapsed(db_override):
    ids = await _seed_directory(db_override.factory)
    fallback_id = await _seed_fallback_approver(db_override.factory, ids["provider_id"])
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/packages", json={
            **_package_payload(ids),
            "principals": [{"principal_type": "USER", "principal_id": str(ids["user_id"])}],
            "default_approver_id": str(ids["approver_id"]),
            "default_fallback_approver_id": str(fallback_id),
            "fallback_unlock_hours": 24,
        })
        package_id = created.json()["id"]

    authenticate_as("AccessPilot.User", subject="target-user")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        requested = await client.post(f"/api/v1/packages/{package_id}/request", json={"assignment_type": "PERMANENT", "justification": "Need it now."})
    item_id = UUID(requested.json()["results"][0]["assignment"]["id"])

    # Simulate the 24-hour window having already elapsed with no primary response.
    async with db_override.factory() as session:
        assignment = await session.get(AccessAssignment, item_id)
        assignment.fallback_unlock_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        await session.commit()

    authenticate_as("AccessPilot.User", subject="fallback-oid")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        approved = await client.post(f"/api/v1/assignments/{item_id}/approve", json={"justification": "Approved as fallback after timeout."})
    assert approved.status_code == 200
    assert approved.json()["status"] == "ELIGIBLE"
    assert approved.json()["approved_by"] == str(fallback_id)
