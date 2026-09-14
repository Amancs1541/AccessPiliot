from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import AccessAssignment, AccessPackage, AccessPackageAssignment, AccessPackageItem, Application, Group, IdentityProvider, Role, User, UserGroup
from app.providers.base import CreatedUser, NormalizedGroup, NormalizedUser, ProviderConflictError
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


def as_role(role: str):
    async def dependency():
        return AuthenticatedUser("actor-1", "Actor", "actor@example.com", "tenant", (role,), {})
    return dependency


def authenticate_as(role: str) -> None:
    app.dependency_overrides[require_authenticated_user] = as_role(role)


@pytest.mark.asyncio
async def test_users_groups_roles_empty_state_for_admin(db_override):
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        users_response = await client.get("/api/v1/users")
        groups_response = await client.get("/api/v1/groups")
        roles_response = await client.get("/api/v1/roles")
    assert users_response.status_code == 200 and users_response.json() == []
    assert groups_response.status_code == 200 and groups_response.json() == []
    assert roles_response.status_code == 200 and roles_response.json() == []


@pytest.mark.asyncio
async def test_normal_user_cannot_read_directory(db_override):
    authenticate_as("AccessPilot.User")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/users")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ACCESS_DENIED"


@pytest.mark.asyncio
async def test_users_and_groups_reflect_seeded_data(db_override):
    async with db_override.factory() as session:
        provider = IdentityProvider(name="Entra", type="ENTRA", status="CONNECTED", tenant_id="tenant-1")
        session.add(provider)
        await session.flush()
        seeded_user = User(provider_id=provider.id, external_id="u1", email="a@b.com", display_name="A B", status="ACTIVE")
        seeded_group = Group(provider_id=provider.id, external_id="g1", name="Group One", status="ACTIVE", is_privileged=False)
        session.add_all([seeded_user, seeded_group])
        await session.flush()
        session.add(UserGroup(user_id=seeded_user.id, group_id=seeded_group.id, source="SYNC"))
        await session.commit()

    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        users_response = await client.get("/api/v1/users")
        groups_response = await client.get("/api/v1/groups")
        members_response = await client.get(f"/api/v1/groups/{groups_response.json()[0]['id']}/members")
    assert len(users_response.json()) == 1 and users_response.json()[0]["display_name"] == "A B"
    assert len(groups_response.json()) == 1
    assert len(members_response.json()) == 1


@pytest.mark.asyncio
async def test_create_user_success_and_duplicate(db_override, monkeypatch):
    async with db_override.factory() as session:
        session.add(IdentityProvider(name="Entra", type="ENTRA", status="CONNECTED", tenant_id="tenant-1"))
        await session.commit()

    authenticate_as("AccessPilot.Admin")
    calls = {"count": 0}

    async def fake_create_user(self, request):
        calls["count"] += 1
        if calls["count"] > 1:
            raise ProviderConflictError("A user with this email already exists in Microsoft Entra.")
        return CreatedUser(user=NormalizedUser(external_id="new-1", email=request.user_principal_name, display_name=request.display_name), temporary_password="Temp-Pass-1!")

    monkeypatch.setattr("app.providers.entra.EntraProvider.create_user", fake_create_user)

    payload = {"display_name": "New User", "user_principal_name": "new.user@tenant.onmicrosoft.com"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/api/v1/users", json=payload)
        second = await client.post("/api/v1/users", json=payload)
    assert first.status_code == 201
    assert first.json()["temporary_password"] == "Temp-Pass-1!"
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "USER_ALREADY_EXISTS"


@pytest.mark.asyncio
async def test_create_user_denied_for_normal_user(db_override):
    authenticate_as("AccessPilot.User")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/users", json={"display_name": "X", "user_principal_name": "x@y.com"})
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_create_group_success_and_duplicate(db_override, monkeypatch):
    async with db_override.factory() as session:
        session.add(IdentityProvider(name="Entra", type="ENTRA", status="CONNECTED", tenant_id="tenant-1"))
        await session.commit()

    authenticate_as("AccessPilot.Admin")
    calls = {"count": 0}

    async def fake_create_group(self, request):
        calls["count"] += 1
        if calls["count"] > 1:
            raise ProviderConflictError("A group with this name already exists in Microsoft Entra.")
        return NormalizedGroup(external_id="new-group-1", name=request.display_name, description=request.description)

    monkeypatch.setattr("app.providers.entra.EntraProvider.create_group", fake_create_group)

    payload = {"display_name": "New Group"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/api/v1/groups", json=payload)
        second = await client.post("/api/v1/groups", json=payload)
    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "GROUP_ALREADY_EXISTS"


@pytest.mark.asyncio
async def test_create_user_without_provider_returns_not_found(db_override):
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/users", json={"display_name": "X", "user_principal_name": "x@y.com"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "PROVIDER_NOT_FOUND"


@pytest.mark.asyncio
async def test_dashboard_admin_returns_real_counts(db_override):
    async with db_override.factory() as session:
        provider = IdentityProvider(name="Entra", type="ENTRA", status="CONNECTED", tenant_id="tenant-1")
        session.add(provider)
        await session.flush()
        session.add(User(provider_id=provider.id, external_id="u1", email="a@b.com", display_name="A", status="ACTIVE"))
        session.add(Group(provider_id=provider.id, external_id="g1", name="G", status="ACTIVE", is_privileged=False))
        session.add(Role(provider_id=provider.id, external_id="r1", name="R", role_type="DIRECTORY_ROLE", status="ACTIVE", is_privileged=False))
        await session.commit()

    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/dashboard/admin")
    assert response.status_code == 200
    body = response.json()
    assert body["users"] == 1 and body["groups"] == 1 and body["roles"] == 1
    assert body["provider"]["status"] == "CONNECTED"


@pytest.mark.asyncio
async def test_dashboard_admin_denied_for_normal_user(db_override):
    authenticate_as("AccessPilot.User")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/dashboard/admin")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_user_access_summary_lists_active_assignments_and_package_name(db_override):
    async with db_override.factory() as session:
        provider = IdentityProvider(name="Directory", type="MOCK", status="CONNECTED", tenant_id="t")
        session.add(provider)
        await session.flush()
        target_user = User(provider_id=provider.id, external_id="target-user", email="target@x.com", display_name="Target User", status="ACTIVE")
        group = Group(provider_id=provider.id, external_id="g1", name="Security Team", status="ACTIVE", is_privileged=False)
        session.add_all([target_user, group])
        await session.flush()
        # A direct assignment (no package).
        direct = AccessAssignment(provider_id=provider.id, user_id=target_user.id, resource_type="GROUP", resource_id=group.id, assignment_type="PERMANENT", status="ACTIVE")
        # A package-originated assignment, linked via AccessPackageAssignment.
        package = AccessPackage(name="Starter Kit", status="ACTIVE")
        session.add_all([direct, package])
        await session.flush()
        from_package = AccessAssignment(provider_id=provider.id, user_id=target_user.id, resource_type="GROUP", resource_id=group.id, assignment_type="PERMANENT", status="SCHEDULED")
        session.add(from_package)
        await session.flush()
        session.add(AccessPackageAssignment(package_id=package.id, package_assignment_id=uuid4(), assignment_id=from_package.id, user_id=target_user.id))
        # A revoked assignment — must NOT appear in the summary.
        session.add(AccessAssignment(provider_id=provider.id, user_id=target_user.id, resource_type="GROUP", resource_id=group.id, assignment_type="PERMANENT", status="REVOKED"))
        await session.commit()
        user_id = target_user.id

    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/v1/users/{user_id}/access-summary")
    assert response.status_code == 200
    body = response.json()
    assert len(body["assignments"]) == 2
    assert body["licenses"] == []  # MOCK provider — no live Graph license lookup attempted
    package_item = next(item for item in body["assignments"] if item["package_name"] is not None)
    assert package_item["package_name"] == "Starter Kit"
    assert package_item["status"] == "SCHEDULED"


@pytest.mark.asyncio
async def test_user_access_summary_includes_group_membership_added_directly_in_entra(db_override):
    """A group membership with no corresponding AccessAssignment (only a synced UserGroup row) means the user
    was added to the group directly in Entra, not through AccessPilot — must still show up."""
    async with db_override.factory() as session:
        provider = IdentityProvider(name="Directory", type="MOCK", status="CONNECTED", tenant_id="t")
        session.add(provider)
        await session.flush()
        target_user = User(provider_id=provider.id, external_id="target-user", email="target@x.com", display_name="Target User", status="ACTIVE")
        group = Group(provider_id=provider.id, external_id="g1", name="Finance", status="ACTIVE", is_privileged=False)
        session.add_all([target_user, group])
        await session.flush()
        session.add(UserGroup(user_id=target_user.id, group_id=group.id, source="SYNC"))
        await session.commit()
        user_id = target_user.id

    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/v1/users/{user_id}/access-summary")
    assert response.status_code == 200
    body = response.json()
    assert len(body["assignments"]) == 1
    item = body["assignments"][0]
    assert item["resource_type"] == "GROUP"
    assert item["resource_display_name"] == "Finance"
    assert item["source"] == "DIRECT_IN_ENTRA"
    assert item["id"] is None


@pytest.mark.asyncio
async def test_user_access_summary_does_not_duplicate_group_tracked_by_both_sync_and_accesspilot(db_override):
    async with db_override.factory() as session:
        provider = IdentityProvider(name="Directory", type="MOCK", status="CONNECTED", tenant_id="t")
        session.add(provider)
        await session.flush()
        target_user = User(provider_id=provider.id, external_id="target-user", email="target@x.com", display_name="Target User", status="ACTIVE")
        group = Group(provider_id=provider.id, external_id="g1", name="Finance", status="ACTIVE", is_privileged=False)
        session.add_all([target_user, group])
        await session.flush()
        session.add(UserGroup(user_id=target_user.id, group_id=group.id, source="SYNC"))
        session.add(AccessAssignment(provider_id=provider.id, user_id=target_user.id, resource_type="GROUP", resource_id=group.id, assignment_type="PERMANENT", status="ACTIVE"))
        await session.commit()
        user_id = target_user.id

    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/v1/users/{user_id}/access-summary")
    body = response.json()
    assert len(body["assignments"]) == 1
    assert body["assignments"][0]["source"] == "ACCESSPILOT"


@pytest.mark.asyncio
async def test_user_access_summary_includes_application_role_assigned_directly_in_entra(db_override, monkeypatch):
    async with db_override.factory() as session:
        provider = IdentityProvider(name="Entra", type="ENTRA", status="CONNECTED", tenant_id="tenant-1")
        session.add(provider)
        await session.flush()
        target_user = User(provider_id=provider.id, external_id="target-user", email="target@x.com", display_name="Target User", status="ACTIVE")
        application = Application(provider_id=provider.id, external_id="app-1", name="Reporting Portal", status="ACTIVE", app_roles=[{"id": "role-1", "name": "Viewer", "description": None}])
        session.add_all([target_user, application])
        await session.commit()
        user_id = target_user.id

    async def fake_get_licenses(self, external_id):
        return []

    async def fake_get_app_roles(self, external_id):
        return [{"resource_id": "app-1", "resource_display_name": "Reporting Portal", "app_role_id": "role-1"}]

    monkeypatch.setattr("app.providers.entra.EntraProvider.get_user_licenses", fake_get_licenses)
    monkeypatch.setattr("app.providers.entra.EntraProvider.get_user_app_role_assignments", fake_get_app_roles)

    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/v1/users/{user_id}/access-summary")
    assert response.status_code == 200
    body = response.json()
    assert len(body["assignments"]) == 1
    item = body["assignments"][0]
    assert item["resource_type"] == "APPLICATION"
    assert item["resource_display_name"] == "Reporting Portal — Viewer"
    assert item["source"] == "DIRECT_IN_ENTRA"


@pytest.mark.asyncio
async def test_user_access_summary_does_not_duplicate_application_role_tracked_by_accesspilot(db_override, monkeypatch):
    async with db_override.factory() as session:
        provider = IdentityProvider(name="Entra", type="ENTRA", status="CONNECTED", tenant_id="tenant-1")
        session.add(provider)
        await session.flush()
        target_user = User(provider_id=provider.id, external_id="target-user", email="target@x.com", display_name="Target User", status="ACTIVE")
        application = Application(provider_id=provider.id, external_id="app-1", name="Reporting Portal", status="ACTIVE", app_roles=[{"id": "role-1", "name": "Viewer", "description": None}])
        session.add_all([target_user, application])
        await session.flush()
        session.add(AccessAssignment(provider_id=provider.id, user_id=target_user.id, resource_type="APPLICATION", resource_id=application.id, app_role_external_id="role-1", assignment_type="PERMANENT", status="ACTIVE"))
        await session.commit()
        user_id = target_user.id

    async def fake_get_licenses(self, external_id):
        return []

    async def fake_get_app_roles(self, external_id):
        return [{"resource_id": "app-1", "resource_display_name": "Reporting Portal", "app_role_id": "role-1"}]

    monkeypatch.setattr("app.providers.entra.EntraProvider.get_user_licenses", fake_get_licenses)
    monkeypatch.setattr("app.providers.entra.EntraProvider.get_user_app_role_assignments", fake_get_app_roles)

    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/v1/users/{user_id}/access-summary")
    body = response.json()
    assert len(body["assignments"]) == 1
    assert body["assignments"][0]["source"] == "ACCESSPILOT"


@pytest.mark.asyncio
async def test_user_access_summary_denied_for_normal_user(db_override):
    async with db_override.factory() as session:
        provider = IdentityProvider(name="Directory", type="MOCK", status="CONNECTED", tenant_id="t")
        session.add(provider)
        await session.flush()
        target_user = User(provider_id=provider.id, external_id="target-user", email="target@x.com", display_name="Target User", status="ACTIVE")
        session.add(target_user)
        await session.commit()
        user_id = target_user.id

    authenticate_as("AccessPilot.User")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/v1/users/{user_id}/access-summary")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_updating_a_users_department_pushes_a_real_write_to_the_provider_first(db_override, monkeypatch):
    """The AccessPilot -> Entra direction: editing department/job_title here must actually write to the
    connector (never a local-only edit), and the local row must end up matching whatever the connector reports
    back — not just whatever was requested — since the provider is the source of truth."""
    async with db_override.factory() as session:
        provider = IdentityProvider(name="Entra", type="ENTRA", status="CONNECTED", tenant_id="tenant-1")
        session.add(provider)
        await session.flush()
        target_user = User(provider_id=provider.id, external_id="obj-1", email="mover@x.com", display_name="Mover", status="ACTIVE", department="Engineering")
        session.add(target_user)
        await session.commit()
        user_id = target_user.id

    seen = {}

    async def fake_update_user(self, external_id, *, department, job_title):
        seen["external_id"], seen["department"], seen["job_title"] = external_id, department, job_title
        return NormalizedUser(external_id=external_id, email="mover@x.com", display_name="Mover", department=department, job_title=job_title)

    monkeypatch.setattr("app.providers.entra.EntraProvider.update_user", fake_update_user)

    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch(f"/api/v1/users/{user_id}/attributes", json={"department": "Sales", "job_title": "Account Executive"})
    assert response.status_code == 200
    assert response.json()["department"] == "Sales"
    assert seen == {"external_id": "obj-1", "department": "Sales", "job_title": "Account Executive"}


@pytest.mark.asyncio
async def test_updating_department_triggers_birthright_reconciliation(db_override, monkeypatch):
    async with db_override.factory() as session:
        provider = IdentityProvider(name="Entra", type="ENTRA", status="CONNECTED", tenant_id="tenant-1")
        session.add(provider)
        await session.flush()
        engineering_group = Group(provider_id=provider.id, external_id="g-eng", name="Engineering Team", status="ACTIVE", is_privileged=False)
        sales_group = Group(provider_id=provider.id, external_id="g-sales", name="Sales Team", status="ACTIVE", is_privileged=False)
        target_user = User(provider_id=provider.id, external_id="obj-1", email="mover@x.com", display_name="Mover", status="ACTIVE", department="Engineering")
        session.add_all([engineering_group, sales_group, target_user])
        await session.commit()
        user_id, engineering_group_id, sales_group_id = target_user.id, engineering_group.id, sales_group.id

    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/policies/birthright", json={"name": "Engineering -> Engineering Team", "match_field": "department", "match_value": "Engineering", "resource_type": "GROUP", "resource_id": str(engineering_group_id)})
        await client.post("/api/v1/policies/birthright", json={"name": "Sales -> Sales Team", "match_field": "department", "match_value": "Sales", "resource_type": "GROUP", "resource_id": str(sales_group_id)})
        await client.post(f"/api/v1/policies/birthright/evaluate/{user_id}")

        async def fake_update_user(self, external_id, *, department, job_title):
            return NormalizedUser(external_id=external_id, email="mover@x.com", display_name="Mover", department=department, job_title=job_title)
        monkeypatch.setattr("app.providers.entra.EntraProvider.update_user", fake_update_user)

        updated = await client.patch(f"/api/v1/users/{user_id}/attributes", json={"department": "Sales"})
    assert updated.status_code == 200

    async with db_override.factory() as session:
        assignments = (await session.execute(select(AccessAssignment).where(AccessAssignment.user_id == user_id))).scalars().all()
    by_resource = {a.resource_id: a.status for a in assignments}
    assert by_resource[engineering_group_id] == "REVOKED"
    assert by_resource[sales_group_id] == "ELIGIBLE"


@pytest.mark.asyncio
async def test_reconciliation_never_touches_a_manually_granted_assignment(db_override, monkeypatch):
    """The whole safety point of birthright_policy_id: an assignment an admin granted directly (bypass_activation,
    no policy involved at all) must survive a department change untouched, even though it happens to target a
    group with the same name/shape a birthright policy could otherwise match."""
    async with db_override.factory() as session:
        provider = IdentityProvider(name="Entra", type="ENTRA", status="CONNECTED", tenant_id="tenant-1")
        session.add(provider)
        await session.flush()
        group = Group(provider_id=provider.id, external_id="g-manual", name="Special Access", status="ACTIVE", is_privileged=False)
        target_user = User(provider_id=provider.id, external_id="obj-1", email="mover@x.com", display_name="Mover", status="ACTIVE", department="Engineering")
        session.add_all([group, target_user])
        await session.commit()
        user_id, group_id = target_user.id, group.id

    async def fake_activate_assignment(self, request):
        return True
    monkeypatch.setattr("app.providers.entra.EntraProvider.activate_assignment", fake_activate_assignment)

    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        manual = await client.post("/api/v1/assignments", json={"user_id": str(user_id), "resource_type": "GROUP", "resource_id": str(group_id), "assignment_type": "PERMANENT", "justification": "Manually granted by an admin, not a policy.", "bypass_activation": True})
        assert manual.status_code == 201

        async def fake_update_user(self, external_id, *, department, job_title):
            return NormalizedUser(external_id=external_id, email="mover@x.com", display_name="Mover", department=department, job_title=job_title)
        monkeypatch.setattr("app.providers.entra.EntraProvider.update_user", fake_update_user)

        updated = await client.patch(f"/api/v1/users/{user_id}/attributes", json={"department": "Sales"})
    assert updated.status_code == 200

    async with db_override.factory() as session:
        assignment = (await session.execute(select(AccessAssignment).where(AccessAssignment.user_id == user_id))).scalars().one()
    assert assignment.status == "ACTIVE"  # untouched — never linked to a birthright policy


@pytest.mark.asyncio
async def test_group_access_summary_reports_members_packages_and_policies(db_override):
    async with db_override.factory() as session:
        provider = IdentityProvider(name="Directory", type="MOCK", status="CONNECTED", tenant_id="t")
        session.add(provider)
        await session.flush()
        group = Group(provider_id=provider.id, external_id="g1", name="Finance Team", status="ACTIVE", is_privileged=False)
        member = User(provider_id=provider.id, external_id="u1", email="a@x.com", display_name="A", status="ACTIVE")
        session.add_all([group, member])
        await session.flush()
        session.add(UserGroup(user_id=member.id, group_id=group.id, source="SYNC"))
        package = AccessPackage(name="Finance Bundle", status="ACTIVE")
        session.add(package)
        await session.flush()
        session.add(AccessPackageItem(package_id=package.id, resource_type="GROUP", resource_id=group.id))
        await session.commit()
        group_id = group.id

    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/policies/birthright", json={"name": "Finance -> Finance Team", "match_field": "department", "match_value": "Finance", "resource_type": "GROUP", "resource_id": str(group_id)})
        response = await client.get(f"/api/v1/groups/{group_id}/access-summary")
    assert response.status_code == 200
    body = response.json()
    assert body["member_count"] == 1
    assert len(body["access_packages"]) == 1 and body["access_packages"][0]["name"] == "Finance Bundle"
    assert len(body["birthright_policies"]) == 1 and body["birthright_policies"][0]["name"] == "Finance -> Finance Team"
