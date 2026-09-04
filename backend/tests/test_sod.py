from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import AccessAssignment, AccessPackage, AccessPackageItem, Application, Group, IdentityProvider, Role, SodException, User, UserGroup
from app.security.auth import AuthenticatedUser, require_authenticated_user
from app.services.sod import revoke_lapsed_sod_exceptions
from app.workers.activation import activate_due_assignments


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
        return AuthenticatedUser(subject, "Test User", "user@example.com", "tenant", (role,), {})
    app.dependency_overrides[require_authenticated_user] = dependency


async def _seed_directory(factory):
    async with factory() as session:
        provider = IdentityProvider(name="Directory", type="MOCK", status="CONNECTED", tenant_id="t")
        session.add(provider)
        await session.flush()
        target_user = User(provider_id=provider.id, external_id="target-user", email="target@x.com", display_name="Target User", status="ACTIVE")
        admin_user = User(provider_id=provider.id, external_id="admin-oid", email="admin@x.com", display_name="Admin User", status="ACTIVE")
        group_a = Group(provider_id=provider.id, external_id="ga", name="Payment Initiator", status="ACTIVE", is_privileged=False)
        group_b = Group(provider_id=provider.id, external_id="gb", name="Payment Approver", status="ACTIVE", is_privileged=False)
        session.add_all([target_user, admin_user, group_a, group_b])
        await session.commit()
        return {"provider_id": provider.id, "user_id": target_user.id, "admin_id": admin_user.id, "group_a_id": group_a.id, "group_b_id": group_b.id}


async def _seed_directory_all_entity_types(factory):
    async with factory() as session:
        provider = IdentityProvider(name="Directory", type="MOCK", status="CONNECTED", tenant_id="t")
        session.add(provider)
        await session.flush()
        target_user = User(provider_id=provider.id, external_id="target-user", email="target@x.com", display_name="Target User", status="ACTIVE")
        admin_user = User(provider_id=provider.id, external_id="admin-oid", email="admin@x.com", display_name="Admin User", status="ACTIVE")
        group_a = Group(provider_id=provider.id, external_id="ga", name="Payment Initiator", status="ACTIVE", is_privileged=False)
        role_b = Role(provider_id=provider.id, external_id="rb", name="Finance Reviewer", role_type="DIRECTORY_ROLE", status="ACTIVE", is_privileged=False)
        application = Application(provider_id=provider.id, external_id="app-1", name="Finance App", status="ACTIVE", app_roles=[{"id": "approle-approver", "name": "Approver"}, {"id": "approle-initiator", "name": "Initiator"}])
        session.add_all([target_user, admin_user, group_a, role_b, application])
        await session.commit()
        return {"provider_id": provider.id, "user_id": target_user.id, "admin_id": admin_user.id, "group_a_id": group_a.id, "role_b_id": role_b.id, "application_id": application.id}


@pytest.mark.asyncio
async def test_group_vs_application_role_conflict_is_detected(db_override):
    ids = await _seed_directory_all_entity_types(db_override.factory)
    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/sod/policies", json={
            "name": "Group vs Application role",
            "entities": [
                {"conflict_side": "A", "entity_type": "GROUP", "entity_id": str(ids["group_a_id"])},
                {"conflict_side": "B", "entity_type": "APPLICATION", "entity_id": str(ids["application_id"]), "app_role_external_id": "approle-approver"},
            ],
        })
        print("CREATE POLICY", created.status_code, created.json())
        assert created.status_code == 201

        authenticate_as("AccessPilot.Admin")
        first = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_a_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Side A group."})
        print("GRANT GROUP", first.status_code, first.json())
        assert first.status_code == 201

        blocked = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "APPLICATION", "resource_id": str(ids["application_id"]), "app_role_external_id": "approle-approver", "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Side B application role."})
        print("GRANT APPLICATION", blocked.status_code, blocked.json())
    assert blocked.status_code == 409


@pytest.mark.asyncio
async def test_application_role_vs_group_conflict_is_detected_reverse_order(db_override):
    """Same pair, but the APPLICATION role is granted FIRST and the GROUP second — isolates whether the bug is
    direction-dependent (e.g. only detects a conflict when the already-held side is a GROUP, not when it's an
    APPLICATION role)."""
    ids = await _seed_directory_all_entity_types(db_override.factory)
    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/sod/policies", json={
            "name": "Group vs Application role (reverse)",
            "entities": [
                {"conflict_side": "A", "entity_type": "GROUP", "entity_id": str(ids["group_a_id"])},
                {"conflict_side": "B", "entity_type": "APPLICATION", "entity_id": str(ids["application_id"]), "app_role_external_id": "approle-approver"},
            ],
        })
        assert created.status_code == 201

        authenticate_as("AccessPilot.Admin")
        first = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "APPLICATION", "resource_id": str(ids["application_id"]), "app_role_external_id": "approle-approver", "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Side B application role first."})
        print("GRANT APPLICATION FIRST", first.status_code, first.json())
        assert first.status_code == 201

        blocked = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_a_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Side A group second."})
        print("GRANT GROUP SECOND", blocked.status_code, blocked.json())
    assert blocked.status_code == 409


@pytest.mark.asyncio
async def test_role_vs_role_conflict_is_detected(db_override):
    ids = await _seed_directory_all_entity_types(db_override.factory)
    async with db_override.factory() as session:
        from app.models import Role
        role_a = Role(provider_id=ids["provider_id"], external_id="ra", name="Global Reader", role_type="DIRECTORY_ROLE", status="ACTIVE", is_privileged=False)
        session.add(role_a)
        await session.commit()
        role_a_id = role_a.id

    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/sod/policies", json={
            "name": "Role vs Role",
            "entities": [
                {"conflict_side": "A", "entity_type": "ROLE", "entity_id": str(role_a_id)},
                {"conflict_side": "B", "entity_type": "ROLE", "entity_id": str(ids["role_b_id"])},
            ],
        })
        assert created.status_code == 201

        authenticate_as("AccessPilot.Admin")
        first = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "ROLE", "resource_id": str(role_a_id), "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Side A role."})
        assert first.status_code == 201
        blocked = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "ROLE", "resource_id": str(ids["role_b_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Side B role."})
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "SOD_CONFLICT"


@pytest.mark.asyncio
async def test_role_vs_application_role_conflict_is_detected(db_override):
    ids = await _seed_directory_all_entity_types(db_override.factory)
    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/sod/policies", json={
            "name": "Role vs Application role",
            "entities": [
                {"conflict_side": "A", "entity_type": "ROLE", "entity_id": str(ids["role_b_id"])},
                {"conflict_side": "B", "entity_type": "APPLICATION", "entity_id": str(ids["application_id"]), "app_role_external_id": "approle-initiator"},
            ],
        })
        assert created.status_code == 201

        authenticate_as("AccessPilot.Admin")
        first = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "ROLE", "resource_id": str(ids["role_b_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Side A role."})
        assert first.status_code == 201
        blocked = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "APPLICATION", "resource_id": str(ids["application_id"]), "app_role_external_id": "approle-initiator", "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Side B application role."})
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "SOD_CONFLICT"


@pytest.mark.asyncio
async def test_self_activation_of_an_application_role_is_blocked_by_a_group_conflict(db_override):
    """Verifies activate_assignment()'s OWN independent SoD gate specifically — distinct from create_assignment's
    now-stricter check_sod_at_creation gate (see test_admin_creation_of_a_conflicting_assignment_is_now_blocked_
    immediately below). The eligible row is seeded directly at the DB layer, bypassing create_assignment
    entirely, precisely so this test exercises only the activation-time gate, not the creation-time one."""
    ids = await _seed_directory_all_entity_types(db_override.factory)
    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/sod/policies", json={
            "name": "Group vs Application role (activate path)",
            "entities": [
                {"conflict_side": "A", "entity_type": "GROUP", "entity_id": str(ids["group_a_id"])},
                {"conflict_side": "B", "entity_type": "APPLICATION", "entity_id": str(ids["application_id"]), "app_role_external_id": "approle-approver"},
            ],
        })

        authenticate_as("AccessPilot.Admin")
        await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_a_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Side A group."})

    async with db_override.factory() as session:
        eligible_assignment = AccessAssignment(provider_id=ids["provider_id"], user_id=ids["user_id"], resource_type="APPLICATION", resource_id=ids["application_id"], app_role_external_id="approle-approver", assignment_type="PERMANENT", status="ELIGIBLE")
        session.add(eligible_assignment)
        await session.commit()
        assignment_id = str(eligible_assignment.id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        authenticate_as("AccessPilot.User", subject="target-user")
        activation = await client.post(f"/api/v1/assignments/{assignment_id}/activate", json={"duration_hours": 1, "justification": "Need it now."})
        print("SELF-ACTIVATE APPLICATION ROLE", activation.status_code, activation.json())
    assert activation.status_code == 409
    assert activation.json()["error"]["code"] == "SOD_CONFLICT"


@pytest.mark.asyncio
async def test_admin_creation_of_a_conflicting_assignment_is_now_blocked_immediately(db_override):
    """The new, stricter gate the user explicitly asked for: an admin-initiated assignment that would conflict
    is blocked at creation time, not left to sit ELIGIBLE until someone tries to activate it. Self-service
    package requests are deliberately NOT affected — see test_self_service_package_request_is_not_blocked_at_
    creation_time below."""
    ids = await _seed_directory_all_entity_types(db_override.factory)
    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/sod/policies", json={
            "name": "Group vs Application role (creation-time)",
            "entities": [
                {"conflict_side": "A", "entity_type": "GROUP", "entity_id": str(ids["group_a_id"])},
                {"conflict_side": "B", "entity_type": "APPLICATION", "entity_id": str(ids["application_id"]), "app_role_external_id": "approle-approver"},
            ],
        })

        authenticate_as("AccessPilot.Admin")
        await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_a_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Side A group."})
        blocked = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "APPLICATION", "resource_id": str(ids["application_id"]), "app_role_external_id": "approle-approver", "assignment_type": "PERMANENT", "justification": "Requesting the conflicting application role, no bypass."})
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "SOD_CONFLICT"


@pytest.mark.asyncio
async def test_admin_creating_two_merely_eligible_conflicting_items_is_blocked(db_override):
    """The exact gap the user reported: an ELIGIBLE-but-not-yet-activated item is still a standing grant that can
    be turned real at any moment with no further review, so two conflicting items both sitting ELIGIBLE for the
    same user must be caught at creation time too — not just an ACTIVE-vs-ACTIVE conflict. Side A here is never
    activated or bypassed; it lands ordinary ELIGIBLE, and that alone must be enough to block Side B."""
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/sod/policies", json=_policy_payload(ids["group_a_id"], ids["group_b_id"]))

        authenticate_as("AccessPilot.Admin")
        side_a = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_a_id"]), "assignment_type": "PERMANENT", "justification": "Side A, no bypass, no approver."})
        assert side_a.status_code == 201
        assert side_a.json()["status"] == "ELIGIBLE"

        blocked = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_b_id"]), "assignment_type": "PERMANENT", "justification": "Side B, also no bypass — still must be blocked."})
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "SOD_CONFLICT"


@pytest.mark.asyncio
async def test_violations_scan_finds_a_conflict_between_two_merely_eligible_items(db_override):
    """The detective-scan counterpart of the test above — two ELIGIBLE (never activated) conflicting items for
    the same user must show up in the live violations scan too, not just an ACTIVE-vs-ACTIVE pair."""
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/sod/policies", json=_policy_payload(ids["group_a_id"], ids["group_b_id"]))

        authenticate_as("AccessPilot.Admin")
        side_a = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_a_id"]), "assignment_type": "PERMANENT", "justification": "Side A, no bypass."})
        assert side_a.json()["status"] == "ELIGIBLE"
        side_b = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_b_id"]), "assignment_type": "PERMANENT", "override_sod": True, "justification": "Side B, no bypass, admin override to seed the eligible-vs-eligible state."})
        assert side_b.json()["status"] == "ELIGIBLE"

        authenticate_as("AccessPilot.SoDAdmin")
        violations = await client.get("/api/v1/sod/violations")
    assert violations.status_code == 200
    body = violations.json()
    assert len(body) == 1
    assert body[0]["user_id"] == str(ids["user_id"])
    assert {h["resource_id"] for h in body[0]["side_a_holdings"]} == {str(ids["group_a_id"])}
    assert {h["resource_id"] for h in body[0]["side_b_holdings"]} == {str(ids["group_b_id"])}


@pytest.mark.asyncio
async def test_self_service_package_request_is_not_blocked_at_creation_time(db_override):
    """The flip side of the previous test: check_sod_at_creation is deliberately False for request_package(), so
    a self-service request for a doomed-to-conflict item still lands ELIGIBLE (as it always has) — SoD is only
    enforced when the user later tries to self-activate it. Admin-initiated grants are the only thing made
    stricter by this feature."""
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/sod/policies", json=_policy_payload(ids["group_a_id"], ids["group_b_id"]))

        authenticate_as("AccessPilot.Admin")
        await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_a_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Side A."})

        package = await client.post("/api/v1/packages", json={"name": "Side B Package", "items": [{"resource_type": "GROUP", "resource_id": str(ids["group_b_id"])}]})
        package_id = package.json()["id"]
        eligibility = await client.put(f"/api/v1/packages/{package_id}/eligibility", json={"principals": [{"principal_type": "USER", "principal_id": str(ids["user_id"])}]})
        assert eligibility.status_code == 200

        authenticate_as("AccessPilot.User", subject="target-user")
        requested = await client.post(f"/api/v1/packages/{package_id}/request", json={"assignment_type": "PERMANENT", "justification": "Requesting side B for myself."})
    assert requested.status_code == 201
    body = requested.json()
    assert body["results"][0]["status"] == "CREATED"
    assert body["results"][0]["assignment"]["status"] == "ELIGIBLE"


async def _seed_directory_entra(factory):
    """Same shape as _seed_directory_all_entity_types, but a real ENTRA-type provider — required for the
    direct-in-Entra augmentation in _user_holds_any to even attempt a live check (it no-ops for MOCK)."""
    async with factory() as session:
        provider = IdentityProvider(name="Entra", type="ENTRA", status="CONNECTED", tenant_id="tenant-1")
        session.add(provider)
        await session.flush()
        target_user = User(provider_id=provider.id, external_id="target-user", email="target@x.com", display_name="Target User", status="ACTIVE")
        admin_user = User(provider_id=provider.id, external_id="admin-oid", email="admin@x.com", display_name="Admin User", status="ACTIVE")
        group_a = Group(provider_id=provider.id, external_id="ga", name="Payment Initiator", status="ACTIVE", is_privileged=False)
        group_b = Group(provider_id=provider.id, external_id="gb", name="Payment Approver", status="ACTIVE", is_privileged=False)
        role_b = Role(provider_id=provider.id, external_id="entra-role-b", name="Global Administrator", role_type="DIRECTORY_ROLE", status="ACTIVE", is_privileged=True)
        application = Application(provider_id=provider.id, external_id="app-1", name="AccessPilot-API-DEV", status="ACTIVE", app_roles=[{"id": "approle-admin", "name": "AccessPilot Admin", "description": None}, {"id": "approle-user", "name": "AccessPilot User", "description": None}])
        session.add_all([target_user, admin_user, group_a, group_b, role_b, application])
        await session.commit()
        return {"provider_id": provider.id, "user_id": target_user.id, "admin_id": admin_user.id, "group_a_id": group_a.id, "group_b_id": group_b.id, "role_b_id": role_b.id, "application_id": application.id}


@pytest.mark.asyncio
async def test_preventive_check_catches_a_group_held_directly_in_entra(db_override):
    """Reproduces the real scenario: the opposite side was never granted through AccessPilot at all — only a
    plain synced UserGroup row exists, no AccessAssignment. This is exactly the gap the user hit live."""
    ids = await _seed_directory_entra(db_override.factory)
    async with db_override.factory() as session:
        session.add(UserGroup(user_id=ids["user_id"], group_id=ids["group_b_id"], source="ENTRA_SYNC"))
        await session.commit()

    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/sod/policies", json=_policy_payload(ids["group_a_id"], ids["group_b_id"], name="Direct-in-Entra group conflict"))

        authenticate_as("AccessPilot.Admin")
        blocked = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_a_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Side A group."})
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "SOD_CONFLICT"


@pytest.mark.asyncio
async def test_preventive_check_catches_a_directory_role_held_directly_in_entra(db_override, monkeypatch):
    ids = await _seed_directory_entra(db_override.factory)

    async def fake_get_user_directory_role_ids(self, external_id):
        return ["entra-role-b"]

    monkeypatch.setattr("app.providers.entra.EntraProvider.get_user_directory_role_ids", fake_get_user_directory_role_ids)

    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/sod/policies", json={
            "name": "Direct-in-Entra role conflict",
            "entities": [
                {"conflict_side": "A", "entity_type": "GROUP", "entity_id": str(ids["group_a_id"])},
                {"conflict_side": "B", "entity_type": "ROLE", "entity_id": str(ids["role_b_id"])},
            ],
        })

        authenticate_as("AccessPilot.Admin")
        blocked = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_a_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Side A group."})
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "SOD_CONFLICT"


@pytest.mark.asyncio
async def test_preventive_check_catches_an_application_role_held_directly_in_entra(db_override, monkeypatch):
    """This is the exact live scenario reported: AccessPilot's own Admin/User app roles are always assigned
    directly in Entra's Enterprise Application blade, never through AccessPilot's own assignment engine."""
    ids = await _seed_directory_entra(db_override.factory)

    async def fake_get_user_app_role_assignments(self, external_id):
        return [{"resource_id": "app-1", "resource_display_name": "AccessPilot-API-DEV", "app_role_id": "approle-user"}]

    monkeypatch.setattr("app.providers.entra.EntraProvider.get_user_app_role_assignments", fake_get_user_app_role_assignments)

    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/sod/policies", json={
            "name": "AccessPilot Admin vs User (direct-in-Entra)",
            "entities": [
                {"conflict_side": "A", "entity_type": "APPLICATION", "entity_id": str(ids["application_id"]), "app_role_external_id": "approle-admin"},
                {"conflict_side": "B", "entity_type": "APPLICATION", "entity_id": str(ids["application_id"]), "app_role_external_id": "approle-user"},
            ],
        })

        authenticate_as("AccessPilot.Admin")
        blocked = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "APPLICATION", "resource_id": str(ids["application_id"]), "app_role_external_id": "approle-admin", "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Side A application role."})
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "SOD_CONFLICT"


@pytest.mark.asyncio
async def test_violations_scan_includes_a_group_held_only_directly_in_entra(db_override):
    ids = await _seed_directory_entra(db_override.factory)
    async with db_override.factory() as session:
        # Side A seeded directly at the DB layer (an ordinary ACTIVE AccessPilot-tracked grant) — going through
        # the real HTTP bypass-create endpoint here would attempt an actual Graph call against this test's
        # ENTRA-type provider (needed so the DIRECT_IN_ENTRA augmentation activates) and fail on missing
        # credentials; that path is already covered by the preventive-check tests above using a MOCK provider.
        session.add(AccessAssignment(provider_id=ids["provider_id"], user_id=ids["user_id"], resource_type="GROUP", resource_id=ids["group_a_id"], assignment_type="PERMANENT", status="ACTIVE"))
        session.add(UserGroup(user_id=ids["user_id"], group_id=ids["group_b_id"], source="ENTRA_SYNC"))
        await session.commit()

    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/sod/policies", json=_policy_payload(ids["group_a_id"], ids["group_b_id"], name="Detective scan direct-in-Entra"))
        violations = await client.get("/api/v1/sod/violations")
    assert violations.status_code == 200
    body = violations.json()
    matching = [v for v in body if v["policy_name"] == "Detective scan direct-in-Entra"]
    assert len(matching) == 1
    side_b = matching[0]["side_b_holdings"]
    assert len(side_b) == 1
    assert side_b[0]["source"] == "DIRECT_IN_ENTRA"
    assert side_b[0]["assignment_id"] is None


@pytest.mark.asyncio
async def test_violations_scan_finds_an_application_role_conflict_that_exists_only_directly_in_entra(db_override, monkeypatch):
    """The exact live gap found: AccessPilot's own Admin/User app roles are never tracked as AccessAssignment
    rows (they're assigned directly in Entra's Enterprise Application blade) — a user holding both must still
    surface in the violations scan, not just get blocked on a NEW grant attempt."""
    ids = await _seed_directory_entra(db_override.factory)

    async def fake_get_user_app_role_assignments(self, external_id):
        if external_id != "target-user":
            return []
        return [
            {"resource_id": "app-1", "resource_display_name": "AccessPilot-API-DEV", "app_role_id": "approle-admin"},
            {"resource_id": "app-1", "resource_display_name": "AccessPilot-API-DEV", "app_role_id": "approle-user"},
        ]

    monkeypatch.setattr("app.providers.entra.EntraProvider.get_user_app_role_assignments", fake_get_user_app_role_assignments)

    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/sod/policies", json={
            "name": "AccessPilot Admin vs User (detective scan)",
            "entities": [
                {"conflict_side": "A", "entity_type": "APPLICATION", "entity_id": str(ids["application_id"]), "app_role_external_id": "approle-admin"},
                {"conflict_side": "B", "entity_type": "APPLICATION", "entity_id": str(ids["application_id"]), "app_role_external_id": "approle-user"},
            ],
        })
        violations = await client.get("/api/v1/sod/violations")
    assert violations.status_code == 200
    body = violations.json()
    matching = [v for v in body if v["policy_name"] == "AccessPilot Admin vs User (detective scan)"]
    assert len(matching) == 1
    assert matching[0]["user_id"] == str(ids["user_id"])
    assert matching[0]["side_a_holdings"][0]["source"] == "DIRECT_IN_ENTRA"
    assert matching[0]["side_a_holdings"][0]["assignment_id"] is None
    assert matching[0]["side_b_holdings"][0]["source"] == "DIRECT_IN_ENTRA"


def _policy_payload(group_a_id, group_b_id, **overrides) -> dict:
    payload = {
        "name": "Payment Initiator vs Approver",
        "description": "No one may both initiate and approve a payment.",
        "severity": "HIGH",
        "entities": [
            {"conflict_side": "A", "entity_type": "GROUP", "entity_id": str(group_a_id)},
            {"conflict_side": "B", "entity_type": "GROUP", "entity_id": str(group_b_id)},
        ],
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_sodadmin_can_create_a_policy_but_plain_admin_cannot(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        denied = await client.post("/api/v1/sod/policies", json=_policy_payload(ids["group_a_id"], ids["group_b_id"]))
        assert denied.status_code == 403

        authenticate_as("AccessPilot.SoDAdmin")
        allowed = await client.post("/api/v1/sod/policies", json=_policy_payload(ids["group_a_id"], ids["group_b_id"]))
    assert allowed.status_code == 201
    body = allowed.json()
    assert body["name"] == "Payment Initiator vs Approver"
    assert body["status"] == "ACTIVE"
    assert len(body["entities"]) == 2


@pytest.mark.asyncio
async def test_deleting_a_policy_with_no_history_removes_it_entirely(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/sod/policies", json=_policy_payload(ids["group_a_id"], ids["group_b_id"]))
        policy_id = created.json()["id"]

        deleted = await client.delete(f"/api/v1/sod/policies/{policy_id}")
        assert deleted.status_code == 200
        assert deleted.json() == {"deleted": True, "id": policy_id}

        listing = await client.get("/api/v1/sod/policies")
    assert all(p["id"] != policy_id for p in listing.json())


@pytest.mark.asyncio
async def test_deleting_a_policy_with_exception_history_disables_it_instead(db_override):
    """Real bug caught live: sod_exceptions/sod_notifications/sod_exception_requests are all deliberately
    permanent, full-history tables with a real FK to sod_policy_id and no cascade — a policy that has ever had
    an exception granted (or a notification fired) against it can never be hard-deleted at all. Confirmed against
    the real Postgres FK constraint (SQLite's test DB doesn't enforce it, so this needs an explicit test here to
    stay caught)."""
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/sod/policies", json=_policy_payload(ids["group_a_id"], ids["group_b_id"]))
        policy_id = created.json()["id"]
        future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        exception = await client.post("/api/v1/sod/exceptions", json={"sod_policy_id": policy_id, "user_id": str(ids["user_id"]), "justification": "Has real history now.", "expires_at": future})
        assert exception.status_code == 201

        deleted = await client.delete(f"/api/v1/sod/policies/{policy_id}")
        assert deleted.status_code == 200
        body = deleted.json()
        assert body["id"] == policy_id
        assert body["status"] == "DISABLED"

        listing = await client.get("/api/v1/sod/policies")
    matching = [p for p in listing.json() if p["id"] == policy_id]
    assert len(matching) == 1
    assert matching[0]["status"] == "DISABLED"


@pytest.mark.asyncio
async def test_creating_a_policy_dedupes_a_repeated_entity_instead_of_erroring(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.SoDAdmin")
    payload = _policy_payload(ids["group_a_id"], ids["group_b_id"])
    payload["entities"].append({"conflict_side": "A", "entity_type": "GROUP", "entity_id": str(ids["group_a_id"])})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/sod/policies", json=payload)
    assert response.status_code == 201
    assert len(response.json()["entities"]) == 2


@pytest.mark.asyncio
async def test_the_same_entity_on_both_sides_is_rejected(db_override):
    """Putting the same real entitlement on both Side A and Side B would make the rule fire for every holder of
    it — the same 'baseline access' failure mode as an accidentally-universal item, just self-inflicted through
    the rule's own shape."""
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.SoDAdmin")
    payload = {
        "name": "Self-conflicting rule",
        "entities": [
            {"conflict_side": "A", "entity_type": "GROUP", "entity_id": str(ids["group_a_id"])},
            {"conflict_side": "B", "entity_type": "GROUP", "entity_id": str(ids["group_a_id"])},
        ],
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/sod/policies", json=payload)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_updating_a_policy_to_put_the_same_entity_on_both_sides_is_also_rejected(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/sod/policies", json=_policy_payload(ids["group_a_id"], ids["group_b_id"]))
        policy_id = created.json()["id"]
        bad_update = _policy_payload(ids["group_a_id"], ids["group_a_id"], status="ACTIVE")
        response = await client.patch(f"/api/v1/sod/policies/{policy_id}", json=bad_update)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_admin_can_read_violations_but_not_manage_rules(db_override):
    """AccessPilot.SoDAdmin is sourced exclusively from a real Entra App Role now — there is deliberately no
    in-app path for a plain Admin to grant or manage it at all (see security/auth.py's PERMISSIONS comment), on
    top of the pre-existing restriction that an Admin can never edit SoD rules directly either."""
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        violations = await client.get("/api/v1/sod/violations")
        assert violations.status_code == 200
        manage_denied = await client.patch("/api/v1/sod/policies/00000000-0000-0000-0000-000000000000", json=_policy_payload(ids["group_a_id"], ids["group_b_id"]))
        assert manage_denied.status_code == 403


@pytest.mark.asyncio
async def test_bypass_create_is_blocked_by_an_active_conflict_and_can_be_overridden(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/sod/policies", json=_policy_payload(ids["group_a_id"], ids["group_b_id"]))

        authenticate_as("AccessPilot.Admin")
        first = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_a_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Initiator access."})
        assert first.status_code == 201

        blocked = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_b_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Approver access."})
        assert blocked.status_code == 409
        assert blocked.json()["error"]["code"] == "SOD_CONFLICT"

        overridden = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_b_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "override_sod": True, "justification": "Approved exception for coverage."})
        assert overridden.status_code == 201
        assert overridden.json()["status"] == "ACTIVE"


@pytest.mark.asyncio
async def test_self_activation_is_blocked_by_a_conflict_but_admin_override_succeeds(db_override):
    """Eligible row seeded directly at the DB layer — going through POST /assignments would now be blocked at
    creation time (see test_admin_creation_of_a_conflicting_assignment_is_now_blocked_immediately), which is a
    different enforcement point than the one this test targets (activate_assignment's own gate + override)."""
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/sod/policies", json=_policy_payload(ids["group_a_id"], ids["group_b_id"]))

        authenticate_as("AccessPilot.Admin")
        await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_a_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Initiator access."})

    async with db_override.factory() as session:
        eligible_assignment = AccessAssignment(provider_id=ids["provider_id"], user_id=ids["user_id"], resource_type="GROUP", resource_id=ids["group_b_id"], assignment_type="PERMANENT", status="ELIGIBLE")
        session.add(eligible_assignment)
        await session.commit()
        assignment_id = str(eligible_assignment.id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        authenticate_as("AccessPilot.User", subject="target-user")
        self_activate = await client.post(f"/api/v1/assignments/{assignment_id}/activate", json={"duration_hours": 1, "justification": "Need it now."})
        assert self_activate.status_code == 409
        assert self_activate.json()["error"]["code"] == "SOD_CONFLICT"

        authenticate_as("AccessPilot.Admin")
        admin_override = await client.post(f"/api/v1/assignments/{assignment_id}/activate", json={"duration_hours": 1, "justification": "Approved exception.", "override_sod": True})
        assert admin_override.status_code == 200
        assert admin_override.json()["status"] == "ACTIVE"


@pytest.mark.asyncio
async def test_cooldown_blocks_the_deactivate_then_activate_opposite_side_cycle(db_override):
    """The anti-gaming fix: without a cooldown, a user could deactivate side A and immediately activate side B
    (no live conflict at that instant), then flip back later — never simultaneously holding both, but never
    really giving up either. With cooldown_enabled, a recent ASSIGNMENT_REVOKED/DEACTIVATED audit entry for the
    opposite side within the window blocks the activation just as if it were still held."""
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/sod/policies", json=_policy_payload(ids["group_a_id"], ids["group_b_id"]))
        settings_update = await client.patch("/api/v1/sod/notification-settings", json={"notify_on_new_violation": True, "notify_on_exception_expiring": True, "exception_expiring_warning_days": 7, "notify_on_exception_requested": True, "cooldown_enabled": True, "cooldown_hours": 24})
        assert settings_update.status_code == 200

        authenticate_as("AccessPilot.Admin")
        granted = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_a_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Initiator access."})
        assignment_id = granted.json()["id"]
        revoked = await client.post(f"/api/v1/assignments/{assignment_id}/revoke", json={"justification": "Rotating off initiator duties."})
        assert revoked.status_code == 200

    async with db_override.factory() as session:
        eligible_assignment = AccessAssignment(provider_id=ids["provider_id"], user_id=ids["user_id"], resource_type="GROUP", resource_id=ids["group_b_id"], assignment_type="PERMANENT", status="ELIGIBLE")
        session.add(eligible_assignment)
        await session.commit()
        eligible_id = str(eligible_assignment.id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        authenticate_as("AccessPilot.User", subject="target-user")
        self_activate = await client.post(f"/api/v1/assignments/{eligible_id}/activate", json={"duration_hours": 1, "justification": "Need it now."})
    assert self_activate.status_code == 409
    assert self_activate.json()["error"]["code"] == "SOD_CONFLICT"


@pytest.mark.asyncio
async def test_cooldown_disabled_by_default_does_not_block_the_same_cycle(db_override):
    """Same deactivate-then-activate-opposite-side sequence as the cooldown test above, but with cooldown left at
    its default (disabled) — proves the block above is genuinely caused by the cooldown feature, not some other
    side effect of revoking the first assignment."""
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/sod/policies", json=_policy_payload(ids["group_a_id"], ids["group_b_id"]))

        authenticate_as("AccessPilot.Admin")
        granted = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_a_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Initiator access."})
        assignment_id = granted.json()["id"]
        revoked = await client.post(f"/api/v1/assignments/{assignment_id}/revoke", json={"justification": "Rotating off initiator duties."})
        assert revoked.status_code == 200

    async with db_override.factory() as session:
        eligible_assignment = AccessAssignment(provider_id=ids["provider_id"], user_id=ids["user_id"], resource_type="GROUP", resource_id=ids["group_b_id"], assignment_type="PERMANENT", status="ELIGIBLE")
        session.add(eligible_assignment)
        await session.commit()
        eligible_id = str(eligible_assignment.id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        authenticate_as("AccessPilot.User", subject="target-user")
        self_activate = await client.post(f"/api/v1/assignments/{eligible_id}/activate", json={"duration_hours": 1, "justification": "Need it now."})
    assert self_activate.status_code == 200
    assert self_activate.json()["status"] == "ACTIVE"


@pytest.mark.asyncio
async def test_scheduled_worker_activation_is_blocked_by_a_conflict_and_retries(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/sod/policies", json=_policy_payload(ids["group_a_id"], ids["group_b_id"]))

        authenticate_as("AccessPilot.Admin")
        past_start = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        scheduled = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_b_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "start_time": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(), "justification": "Scheduled approver access."})
        assert scheduled.status_code == 201
        assert scheduled.json()["status"] == "SCHEDULED"
        assignment_id = scheduled.json()["id"]

        # Now grant the conflicting side directly (immediate bypass) so a conflict exists by the time the
        # scheduled item's start_time arrives.
        await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_a_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Initiator access."})

    # Force the scheduled item's start_time into the past so the worker considers it due, then run the worker.
    async with db_override.factory() as session:
        from uuid import UUID as _UUID
        assignment = await session.get(AccessAssignment, _UUID(assignment_id))
        assignment.start_time = datetime.now(timezone.utc) - timedelta(minutes=1)
        await session.commit()

    activated_count = await activate_due_assignments(db_override.factory)
    assert activated_count == 0

    async with db_override.factory() as session:
        from uuid import UUID as _UUID
        assignment = await session.get(AccessAssignment, _UUID(assignment_id))
        assert assignment.status == "SCHEDULED"


@pytest.mark.asyncio
async def test_get_violations_finds_a_pre_existing_conflict(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/sod/policies", json=_policy_payload(ids["group_a_id"], ids["group_b_id"]))

        authenticate_as("AccessPilot.Admin")
        await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_a_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "override_sod": True, "justification": "Seeded side A."})
        await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_b_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "override_sod": True, "justification": "Seeded side B."})

        authenticate_as("AccessPilot.SoDAdmin")
        violations = await client.get("/api/v1/sod/violations")
    assert violations.status_code == 200
    body = violations.json()
    assert len(body) == 1
    assert body[0]["user_id"] == str(ids["user_id"])
    assert {h["resource_id"] for h in body[0]["side_a_holdings"]} == {str(ids["group_a_id"])}
    assert {h["resource_id"] for h in body[0]["side_b_holdings"]} == {str(ids["group_b_id"])}


@pytest.mark.asyncio
async def test_package_type_entity_expands_to_its_items(db_override):
    ids = await _seed_directory(db_override.factory)
    async with db_override.factory() as session:
        package = AccessPackage(name="Payments Bundle", status="ACTIVE")
        session.add(package)
        await session.flush()
        session.add(AccessPackageItem(package_id=package.id, resource_type="GROUP", resource_id=ids["group_b_id"]))
        await session.commit()
        package_id = package.id

    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/sod/policies", json={
            "name": "Package-based conflict",
            "severity": "MEDIUM",
            "entities": [
                {"conflict_side": "A", "entity_type": "GROUP", "entity_id": str(ids["group_a_id"])},
                {"conflict_side": "B", "entity_type": "PACKAGE", "entity_id": str(package_id)},
            ],
        })
        assert created.status_code == 201

        authenticate_as("AccessPilot.Admin")
        first = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_a_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Side A."})
        assert first.status_code == 201
        blocked = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_b_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Held via the package's own item."})
    assert blocked.status_code == 409


@pytest.mark.asyncio
async def test_dangling_package_reference_resolves_as_unresolved_not_an_error(db_override):
    ids = await _seed_directory(db_override.factory)
    async with db_override.factory() as session:
        package = AccessPackage(name="Temporary Bundle", status="ACTIVE")
        session.add(package)
        await session.flush()
        package_id = package.id
        await session.commit()

    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/sod/policies", json={
            "name": "Dangling package rule",
            "entities": [
                {"conflict_side": "A", "entity_type": "GROUP", "entity_id": str(ids["group_a_id"])},
                {"conflict_side": "B", "entity_type": "PACKAGE", "entity_id": str(package_id)},
            ],
        })
        assert created.status_code == 201

    async with db_override.factory() as session:
        await session.delete(await session.get(AccessPackage, package_id))
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        listed = await client.get("/api/v1/sod/policies")
    assert listed.status_code == 200
    entities = listed.json()[0]["entities"]
    package_entity = next(e for e in entities if e["entity_type"] == "PACKAGE")
    assert package_entity["entity_resolved"] is False


@pytest.mark.asyncio
async def test_a_real_entra_role_grants_sod_admin_with_no_local_grant_needed(db_override):
    """AccessPilot.SoDAdmin is recognized purely from the token's own roles claim now — authenticate_as(...)
    simulates exactly that (a real Entra app role assignment showing up in the JWT), with no local table
    involved at all. This is the regression test replacing the old roster-driven is_sod_admin() check."""
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.SoDAdmin", subject="target-user")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/sod/policies", json=_policy_payload(ids["group_a_id"], ids["group_b_id"]))
    assert response.status_code == 201


@pytest.mark.asyncio
async def test_check_endpoint_defaults_to_the_caller_and_denies_checking_someone_else(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.User", subject="target-user")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        own_check = await client.post("/api/v1/sod/check", json={"resource_type": "GROUP", "resource_id": str(ids["group_a_id"])})
        assert own_check.status_code == 200

        probing_another_user = await client.post("/api/v1/sod/check", json={"user_id": str(ids["admin_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_a_id"])})
    assert probing_another_user.status_code == 403


@pytest.mark.asyncio
async def test_check_endpoint_allows_an_admin_to_check_on_behalf_of_another_user(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/sod/check", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_a_id"])})
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_dangling_group_and_role_references_resolve_as_unresolved_not_an_error(db_override):
    """Same graceful-degradation guarantee as the existing dangling-PACKAGE test, extended to the two other
    entity types that can also be deleted out from under a rule after it's created."""
    ids = await _seed_directory_all_entity_types(db_override.factory)
    async with db_override.factory() as session:
        from app.models import Role as RoleModel
        dangling_role = RoleModel(provider_id=ids["provider_id"], external_id="temp-role", name="Temporary Role", role_type="DIRECTORY_ROLE", status="ACTIVE", is_privileged=False)
        session.add(dangling_role)
        await session.commit()
        dangling_role_id = dangling_role.id
        dangling_group = Group(provider_id=ids["provider_id"], external_id="temp-group", name="Temporary Group", status="ACTIVE", is_privileged=False)
        session.add(dangling_group)
        await session.commit()
        dangling_group_id = dangling_group.id

    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/sod/policies", json={
            "name": "Dangling group and role rule",
            "entities": [
                {"conflict_side": "A", "entity_type": "GROUP", "entity_id": str(dangling_group_id)},
                {"conflict_side": "B", "entity_type": "ROLE", "entity_id": str(dangling_role_id)},
            ],
        })
        assert created.status_code == 201

    async with db_override.factory() as session:
        from app.models import Role as RoleModel
        await session.delete(await session.get(Group, dangling_group_id))
        await session.delete(await session.get(RoleModel, dangling_role_id))
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        listed = await client.get("/api/v1/sod/policies")
    assert listed.status_code == 200
    entities = next(p for p in listed.json() if p["name"] == "Dangling group and role rule")["entities"]
    assert all(e["entity_resolved"] is False for e in entities)

    # And the enforcement side degrades the same way: a policy referencing only now-deleted entities can never
    # be a live conflict for anyone, but must not error the check either.
    async with db_override.factory() as session:
        from app.services.sod import check_sod_conflicts
        conflicts = await check_sod_conflicts(session, ids["user_id"], "GROUP", dangling_group_id)
    assert conflicts == []


@pytest.mark.asyncio
async def test_sod_activity_shows_rule_changes_and_blocked_grants_but_not_unrelated_assignments(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/sod/policies", json=_policy_payload(ids["group_a_id"], ids["group_b_id"]))
        assert created.status_code == 201

        authenticate_as("AccessPilot.Admin")
        first = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_a_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Side A."})
        assert first.status_code == 201
        blocked = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_b_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Side B, should be blocked."})
        assert blocked.status_code == 409
        # An unrelated, non-conflicting assignment must NOT show up in SoD activity.
        unrelated_user_response = await client.post("/api/v1/assignments", json={"user_id": str(ids["admin_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_a_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Totally unrelated grant."})
        assert unrelated_user_response.status_code == 201

        authenticate_as("AccessPilot.SoDAdmin")
        activity = await client.get("/api/v1/sod/activity")
    assert activity.status_code == 200
    actions = [entry["action"] for entry in activity.json()]
    assert "SOD_POLICY_CREATED" in actions
    assert "ASSIGNMENT_CREATE_BLOCKED" in actions
    blocked_entries = [e for e in activity.json() if e["action"] == "ASSIGNMENT_CREATE_BLOCKED"]
    assert len(blocked_entries) == 1
    # The unrelated ASSIGNMENT_CREATED action for the admin's own grant is not an SoD action at all, so it can't
    # appear here regardless of metadata — confirms this endpoint isn't just "every recent audit entry".
    assert all(e["action"] != "ASSIGNMENT_CREATED" for e in activity.json())


@pytest.mark.asyncio
async def test_granting_an_exception_lets_a_previously_blocked_grant_through(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        policy = await client.post("/api/v1/sod/policies", json=_policy_payload(ids["group_a_id"], ids["group_b_id"]))
        policy_id = policy.json()["id"]

        authenticate_as("AccessPilot.Admin")
        first = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_a_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Side A."})
        assert first.status_code == 201

        blocked = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_b_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Side B, expect blocked."})
        assert blocked.status_code == 409

        authenticate_as("AccessPilot.SoDAdmin")
        future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        exception = await client.post("/api/v1/sod/exceptions", json={"sod_policy_id": policy_id, "user_id": str(ids["user_id"]), "justification": "Reviewed by compliance, accepted for Q3.", "expires_at": future})
        assert exception.status_code == 201
        assert exception.json()["is_active"] is True

        authenticate_as("AccessPilot.Admin")
        now_allowed = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_b_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Side B, now excepted."})
    assert now_allowed.status_code == 201


@pytest.mark.asyncio
async def test_plain_admin_cannot_grant_an_exception(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        policy = await client.post("/api/v1/sod/policies", json=_policy_payload(ids["group_a_id"], ids["group_b_id"]))
        policy_id = policy.json()["id"]

        authenticate_as("AccessPilot.Admin")
        future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        response = await client.post("/api/v1/sod/exceptions", json={"sod_policy_id": policy_id, "user_id": str(ids["user_id"]), "justification": "Trying to self-approve.", "expires_at": future})
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_an_exception_in_the_past_is_rejected(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        policy = await client.post("/api/v1/sod/policies", json=_policy_payload(ids["group_a_id"], ids["group_b_id"]))
        policy_id = policy.json()["id"]
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        response = await client.post("/api/v1/sod/exceptions", json={"sod_policy_id": policy_id, "user_id": str(ids["user_id"]), "justification": "Already expired.", "expires_at": past})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_revoking_an_exception_makes_the_next_grant_blocked_again(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        policy = await client.post("/api/v1/sod/policies", json=_policy_payload(ids["group_a_id"], ids["group_b_id"]))
        policy_id = policy.json()["id"]
        future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        exception = await client.post("/api/v1/sod/exceptions", json={"sod_policy_id": policy_id, "user_id": str(ids["user_id"]), "justification": "Temporary acceptance.", "expires_at": future})
        exception_id = exception.json()["id"]

        authenticate_as("AccessPilot.Admin")
        first = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_a_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Side A."})
        assert first.status_code == 201
        allowed = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_b_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Side B, excepted."})
        assert allowed.status_code == 201

        authenticate_as("AccessPilot.SoDAdmin")
        revoke = await client.delete(f"/api/v1/sod/exceptions/{exception_id}")
        assert revoke.status_code == 204

        authenticate_as("AccessPilot.Admin")
        target_role_id = str(ids["group_a_id"])  # a distinct new grant attempt for the same conflicting pair, on a different user
        blocked_again = await client.post("/api/v1/assignments", json={"user_id": str(ids["admin_id"]), "resource_type": "GROUP", "resource_id": target_role_id, "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Side A for a different user."})
        assert blocked_again.status_code == 201  # sanity: admin_id never got side B, so this alone is fine
        second_side_b = await client.post("/api/v1/assignments", json={"user_id": str(ids["admin_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_b_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Side B for a different user, exception is revoked and scoped to the original user only."})
    assert second_side_b.status_code == 409


@pytest.mark.asyncio
async def test_disabling_exception_requested_notifications_stops_generating_them(db_override):
    """The dedicated toggle for the EXCEPTION_REQUESTED notification — distinct from notify_on_new_violation,
    since this notification is fired eagerly at request time, not by the reconciliation pass. Turning it off
    must not affect the request itself (still created, still gates a retry the same way) — only the notification
    that would have pinged the SoDAdmin about it."""
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        policy = await client.post("/api/v1/sod/policies", json=_policy_payload(ids["group_a_id"], ids["group_b_id"]))
        policy_id = policy.json()["id"]
        settings_update = await client.patch("/api/v1/sod/notification-settings", json={"notify_on_new_violation": True, "notify_on_exception_expiring": True, "exception_expiring_warning_days": 7, "notify_on_exception_requested": False, "cooldown_enabled": False, "cooldown_hours": 24})
        assert settings_update.status_code == 200
        assert settings_update.json()["notify_on_exception_requested"] is False

        authenticate_as("AccessPilot.Admin")
        await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_a_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Side A."})
        request_response = await client.post("/api/v1/sod/exception-requests", json={"sod_policy_id": policy_id, "user_id": str(ids["user_id"]), "justification": "Asking anyway, notifications muted.", "resource_type": "GROUP", "resource_id": str(ids["group_b_id"])})
        assert request_response.status_code == 201
        assert request_response.json()["status"] == "PENDING"

        authenticate_as("AccessPilot.SoDAdmin")
        notifications = await client.get("/api/v1/sod/notifications")
    assert notifications.status_code == 200
    assert all(n["notification_type"] != "EXCEPTION_REQUESTED" for n in notifications.json())


@pytest.mark.asyncio
async def test_exception_request_workflow_grant_lets_a_retry_succeed(db_override):
    """The full bridge the user asked for: an admin's blocked assignment attempt -> a request the SoDAdmin sees
    as a notification -> a grant -> a real exception the admin can now use to retry the same grant. The grant
    deliberately does NOT auto-replay the original assignment — the admin must retry it themselves."""
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        policy = await client.post("/api/v1/sod/policies", json=_policy_payload(ids["group_a_id"], ids["group_b_id"]))
        policy_id = policy.json()["id"]

        authenticate_as("AccessPilot.Admin")
        first = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_a_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Side A."})
        assert first.status_code == 201
        blocked = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_b_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Side B, expect blocked."})
        assert blocked.status_code == 409

        request_response = await client.post("/api/v1/sod/exception-requests", json={"sod_policy_id": policy_id, "user_id": str(ids["user_id"]), "justification": "Business needs both roles for the Q3 close.", "resource_type": "GROUP", "resource_id": str(ids["group_b_id"])})
        assert request_response.status_code == 201
        request_body = request_response.json()
        assert request_body["status"] == "PENDING"
        request_id = request_body["id"]

        authenticate_as("AccessPilot.SoDAdmin")
        notifications = await client.get("/api/v1/sod/notifications")
        matching = [n for n in notifications.json() if n["notification_type"] == "EXCEPTION_REQUESTED" and n["sod_policy_id"] == policy_id]
        assert len(matching) == 1
        assert matching[0]["resolved_at"] is None

        future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        granted = await client.post(f"/api/v1/sod/exception-requests/{request_id}/grant", json={"expires_at": future})
        assert granted.status_code == 200
        granted_body = granted.json()
        assert granted_body["status"] == "GRANTED"
        assert granted_body["sod_exception_id"] is not None

        notifications_after = await client.get("/api/v1/sod/notifications")
        matching_after = [n for n in notifications_after.json() if n["notification_type"] == "EXCEPTION_REQUESTED" and n["sod_policy_id"] == policy_id]
        assert len(matching_after) == 1
        assert matching_after[0]["resolved_at"] is not None

        authenticate_as("AccessPilot.Admin")
        retried = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_b_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Side B, retrying now that an exception was granted."})
        assert retried.status_code == 201

        # Closes the loop back to the requesting admin specifically — the general, per-user notification system
        # (backend/app/services/notifications.py), not the shared SoD-only log above.
        general_notifications = await client.get("/api/v1/notifications")
    matching_general = [n for n in general_notifications.json() if n["notification_type"] == "EXCEPTION_REQUEST_GRANTED"]
    assert len(matching_general) == 1
    assert "granted" in matching_general[0]["message"].lower()


@pytest.mark.asyncio
async def test_granting_an_exception_request_makes_the_user_eligible_without_a_retry(db_override):
    """The user's explicit ask: granting must actually create the ELIGIBLE assignment for the target user, not
    just clear the way for the admin to redo it manually."""
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        policy = await client.post("/api/v1/sod/policies", json=_policy_payload(ids["group_a_id"], ids["group_b_id"]))
        policy_id = policy.json()["id"]

        authenticate_as("AccessPilot.Admin")
        await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_a_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Side A."})
        blocked = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_b_id"]), "assignment_type": "PERMANENT", "justification": "Side B, expect blocked."})
        assert blocked.status_code == 409

        request_response = await client.post("/api/v1/sod/exception-requests", json={"sod_policy_id": policy_id, "user_id": str(ids["user_id"]), "justification": "Business need.", "resource_type": "GROUP", "resource_id": str(ids["group_b_id"])})
        request_id = request_response.json()["id"]

        authenticate_as("AccessPilot.SoDAdmin")
        future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        granted = await client.post(f"/api/v1/sod/exception-requests/{request_id}/grant", json={"expires_at": future})
        assert granted.status_code == 200

        # No retry by the admin at all — granting alone must be enough.
        authenticate_as("AccessPilot.Admin")
        assignments = await client.get("/api/v1/assignments")
        general_notifications = await client.get("/api/v1/notifications")
    matching = [a for a in assignments.json() if a["user_id"] == str(ids["user_id"]) and a["resource_id"] == str(ids["group_b_id"]) and a["status"] == "ELIGIBLE"]
    assert len(matching) == 1
    matching_notif = [n for n in general_notifications.json() if n["notification_type"] == "EXCEPTION_REQUEST_GRANTED"]
    assert "now has eligible access" in matching_notif[0]["message"]


@pytest.mark.asyncio
async def test_granting_an_exception_request_with_an_approver_routes_through_approval(db_override):
    """The user's specific follow-up ask: if the originally-blocked assignment had an approver configured,
    granting must route it through that same approver — not skip straight to ELIGIBLE — and only the approver's
    own decision makes it eligible for the end user."""
    ids = await _seed_directory(db_override.factory)
    async with db_override.factory() as session:
        approver = User(provider_id=ids["provider_id"], external_id="approver-oid", email="approver@x.com", display_name="Approver User", status="ACTIVE")
        session.add(approver)
        await session.commit()
        approver_id = approver.id

    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        policy = await client.post("/api/v1/sod/policies", json=_policy_payload(ids["group_a_id"], ids["group_b_id"]))
        policy_id = policy.json()["id"]

        authenticate_as("AccessPilot.Admin")
        await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_a_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Side A."})
        blocked = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_b_id"]), "assignment_type": "PERMANENT", "approver_id": str(approver_id), "justification": "Side B, needs approval, expect blocked."})
        assert blocked.status_code == 409

        request_response = await client.post("/api/v1/sod/exception-requests", json={"sod_policy_id": policy_id, "user_id": str(ids["user_id"]), "justification": "Business need.", "resource_type": "GROUP", "resource_id": str(ids["group_b_id"]), "approver_id": str(approver_id)})
        assert request_response.status_code == 201
        request_id = request_response.json()["id"]

        authenticate_as("AccessPilot.SoDAdmin")
        future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        granted = await client.post(f"/api/v1/sod/exception-requests/{request_id}/grant", json={"expires_at": future})
        assert granted.status_code == 200

        # Nothing should be ELIGIBLE yet — it must be sitting PENDING_APPROVAL for the configured approver.
        authenticate_as("AccessPilot.Admin")
        assignments_after_grant = await client.get("/api/v1/assignments")
        pending = [a for a in assignments_after_grant.json() if a["user_id"] == str(ids["user_id"]) and a["resource_id"] == str(ids["group_b_id"])]
        assert len(pending) == 1
        assert pending[0]["status"] == "PENDING_APPROVAL"
        assignment_id = pending[0]["id"]

        general_notifications = await client.get("/api/v1/notifications")
        matching = [n for n in general_notifications.json() if n["notification_type"] == "EXCEPTION_REQUEST_GRANTED"]
        assert len(matching) == 1
        assert "pending" in matching[0]["message"].lower()

        # Only the approver's own decision makes it eligible for the end user.
        authenticate_as("AccessPilot.User", subject="approver-oid")
        approved = await client.post(f"/api/v1/assignments/{assignment_id}/approve", json={"justification": "Looks fine."})
    assert approved.status_code == 200
    assert approved.json()["status"] == "ELIGIBLE"


@pytest.mark.asyncio
async def test_granting_only_one_of_two_conflicting_policies_does_not_yet_create_the_assignment(db_override):
    """Real edge case: a single blocked attempt can be caught by more than one policy at once (the frontend files
    one exception request per conflicting policy). Granting only one must not create the assignment while another,
    ungranted conflict still applies — the requester's notification must say so, not falsely claim success."""
    ids = await _seed_directory(db_override.factory)
    async with db_override.factory() as session:
        group_c = Group(provider_id=ids["provider_id"], external_id="gc", name="Third Group", status="ACTIVE", is_privileged=False)
        session.add(group_c)
        await session.commit()
        group_c_id = group_c.id

    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        policy_1 = await client.post("/api/v1/sod/policies", json=_policy_payload(ids["group_a_id"], ids["group_b_id"], name="Policy One"))
        policy_1_id = policy_1.json()["id"]
        policy_2 = await client.post("/api/v1/sod/policies", json={
            "name": "Policy Two",
            "entities": [
                {"conflict_side": "A", "entity_type": "GROUP", "entity_id": str(group_c_id)},
                {"conflict_side": "B", "entity_type": "GROUP", "entity_id": str(ids["group_b_id"])},
            ],
        })
        policy_2_id = policy_2.json()["id"]

        authenticate_as("AccessPilot.Admin")
        await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_a_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Side A of policy one."})
        await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(group_c_id), "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Side A of policy two."})

        blocked = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_b_id"]), "assignment_type": "PERMANENT", "justification": "Conflicts with both."})
        assert blocked.status_code == 409
        assert len(blocked.json()["error"]["details"]["conflicts"]) == 2

        request_1 = await client.post("/api/v1/sod/exception-requests", json={"sod_policy_id": policy_1_id, "user_id": str(ids["user_id"]), "justification": "For policy one.", "resource_type": "GROUP", "resource_id": str(ids["group_b_id"])})
        request_1_id = request_1.json()["id"]
        request_2 = await client.post("/api/v1/sod/exception-requests", json={"sod_policy_id": policy_2_id, "user_id": str(ids["user_id"]), "justification": "For policy two.", "resource_type": "GROUP", "resource_id": str(ids["group_b_id"])})
        request_2_id = request_2.json()["id"]

        authenticate_as("AccessPilot.SoDAdmin")
        future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        grant_1 = await client.post(f"/api/v1/sod/exception-requests/{request_1_id}/grant", json={"expires_at": future})
        assert grant_1.status_code == 200

        authenticate_as("AccessPilot.Admin")
        assignments_after_one = await client.get("/api/v1/assignments")
        still_missing = [a for a in assignments_after_one.json() if a["user_id"] == str(ids["user_id"]) and a["resource_id"] == str(ids["group_b_id"])]
        assert len(still_missing) == 0
        general_notifications = await client.get("/api/v1/notifications")
        matching_partial = [n for n in general_notifications.json() if n["notification_type"] == "EXCEPTION_REQUEST_GRANTED"]
        assert "still blocked by another" in matching_partial[0]["message"].lower()

        authenticate_as("AccessPilot.SoDAdmin")
        grant_2 = await client.post(f"/api/v1/sod/exception-requests/{request_2_id}/grant", json={"expires_at": future})
        assert grant_2.status_code == 200

        authenticate_as("AccessPilot.Admin")
        assignments_after_both = await client.get("/api/v1/assignments")
    matching_final = [a for a in assignments_after_both.json() if a["user_id"] == str(ids["user_id"]) and a["resource_id"] == str(ids["group_b_id"]) and a["status"] == "ELIGIBLE"]
    assert len(matching_final) == 1


@pytest.mark.asyncio
async def test_denying_an_exception_request_notifies_the_requesting_admin(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        policy = await client.post("/api/v1/sod/policies", json=_policy_payload(ids["group_a_id"], ids["group_b_id"]))
        policy_id = policy.json()["id"]

        authenticate_as("AccessPilot.Admin")
        await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_a_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Side A."})
        request_response = await client.post("/api/v1/sod/exception-requests", json={"sod_policy_id": policy_id, "user_id": str(ids["user_id"]), "justification": "Asking anyway.", "resource_type": "GROUP", "resource_id": str(ids["group_b_id"])})
        request_id = request_response.json()["id"]

        authenticate_as("AccessPilot.SoDAdmin")
        denied = await client.post(f"/api/v1/sod/exception-requests/{request_id}/deny", json={"reason": "Not justified."})
        assert denied.status_code == 200

        authenticate_as("AccessPilot.Admin")
        general_notifications = await client.get("/api/v1/notifications")
    matching = [n for n in general_notifications.json() if n["notification_type"] == "EXCEPTION_REQUEST_DENIED"]
    assert len(matching) == 1
    assert "not justified" in matching[0]["message"].lower()


@pytest.mark.asyncio
async def test_denying_an_exception_request_leaves_the_grant_blocked(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        policy = await client.post("/api/v1/sod/policies", json=_policy_payload(ids["group_a_id"], ids["group_b_id"]))
        policy_id = policy.json()["id"]

        authenticate_as("AccessPilot.Admin")
        await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_a_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Side A."})
        request_response = await client.post("/api/v1/sod/exception-requests", json={"sod_policy_id": policy_id, "user_id": str(ids["user_id"]), "justification": "Asking anyway.", "resource_type": "GROUP", "resource_id": str(ids["group_b_id"])})
        request_id = request_response.json()["id"]

        authenticate_as("AccessPilot.SoDAdmin")
        denied = await client.post(f"/api/v1/sod/exception-requests/{request_id}/deny", json={"reason": "Not justified — same person cannot hold both roles."})
        assert denied.status_code == 200
        denied_body = denied.json()
        assert denied_body["status"] == "DENIED"
        assert denied_body["sod_exception_id"] is None

        notifications = await client.get("/api/v1/sod/notifications")
        matching = [n for n in notifications.json() if n["notification_type"] == "EXCEPTION_REQUESTED" and n["sod_policy_id"] == policy_id]
        assert len(matching) == 1
        assert matching[0]["resolved_at"] is not None

        authenticate_as("AccessPilot.Admin")
        still_blocked = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_b_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Side B, still blocked after denial."})
    assert still_blocked.status_code == 409


@pytest.mark.asyncio
async def test_plain_admin_cannot_grant_or_deny_an_exception_request(db_override):
    """SOD_MANAGE-gated, same reasoning as granting a direct exception — an Admin deciding their own exception
    request would defeat the whole point of routing it through the SoDAdmin."""
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        policy = await client.post("/api/v1/sod/policies", json=_policy_payload(ids["group_a_id"], ids["group_b_id"]))
        policy_id = policy.json()["id"]

        authenticate_as("AccessPilot.Admin")
        request_response = await client.post("/api/v1/sod/exception-requests", json={"sod_policy_id": policy_id, "user_id": str(ids["user_id"]), "justification": "Trying to self-serve.", "resource_type": "GROUP", "resource_id": str(ids["group_b_id"])})
        request_id = request_response.json()["id"]

        future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        grant_denied = await client.post(f"/api/v1/sod/exception-requests/{request_id}/grant", json={"expires_at": future})
        assert grant_denied.status_code == 403
        deny_denied = await client.post(f"/api/v1/sod/exception-requests/{request_id}/deny", json={"reason": "Trying to self-serve."})
    assert deny_denied.status_code == 403


@pytest.mark.asyncio
async def test_violations_scan_flags_an_excepted_conflict_instead_of_hiding_it(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        policy = await client.post("/api/v1/sod/policies", json=_policy_payload(ids["group_a_id"], ids["group_b_id"]))
        policy_id = policy.json()["id"]
        future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        await client.post("/api/v1/sod/exceptions", json={"sod_policy_id": policy_id, "user_id": str(ids["user_id"]), "justification": "Accepted risk.", "expires_at": future})

        authenticate_as("AccessPilot.Admin")
        await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_a_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Side A."})
        await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_b_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Side B, excepted."})

        authenticate_as("AccessPilot.SoDAdmin")
        violations = await client.get("/api/v1/sod/violations")
    assert violations.status_code == 200
    matching = [v for v in violations.json() if v["policy_id"] == policy_id]
    assert len(matching) == 1
    assert matching[0]["exception_active"] is True
    assert matching[0]["exception_expires_at"] is not None


@pytest.mark.asyncio
async def test_a_new_violation_generates_a_notification(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        policy = await client.post("/api/v1/sod/policies", json=_policy_payload(ids["group_a_id"], ids["group_b_id"]))
        policy_id = policy.json()["id"]

        authenticate_as("AccessPilot.Admin")
        await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_a_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "override_sod": True, "justification": "Side A."})
        await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_b_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "override_sod": True, "justification": "Side B."})

        authenticate_as("AccessPilot.SoDAdmin")
        notifications = await client.get("/api/v1/sod/notifications")
    assert notifications.status_code == 200
    matching = [n for n in notifications.json() if n["notification_type"] == "NEW_VIOLATION" and n["sod_policy_id"] == policy_id]
    assert len(matching) == 1
    assert matching[0]["read_at"] is None
    assert matching[0]["resolved_at"] is None


@pytest.mark.asyncio
async def test_calling_notifications_twice_does_not_duplicate_the_same_open_violation(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        policy = await client.post("/api/v1/sod/policies", json=_policy_payload(ids["group_a_id"], ids["group_b_id"]))
        policy_id = policy.json()["id"]

        authenticate_as("AccessPilot.Admin")
        await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_a_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "override_sod": True, "justification": "Side A."})
        await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_b_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "override_sod": True, "justification": "Side B."})

        authenticate_as("AccessPilot.SoDAdmin")
        await client.get("/api/v1/sod/notifications")
        second = await client.get("/api/v1/sod/notifications")
    matching = [n for n in second.json() if n["notification_type"] == "NEW_VIOLATION" and n["sod_policy_id"] == policy_id]
    assert len(matching) == 1


@pytest.mark.asyncio
async def test_resolving_a_violation_resolves_its_notification(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        policy = await client.post("/api/v1/sod/policies", json=_policy_payload(ids["group_a_id"], ids["group_b_id"]))
        policy_id = policy.json()["id"]

        authenticate_as("AccessPilot.Admin")
        await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_a_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "override_sod": True, "justification": "Side A."})
        second_grant = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_b_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "override_sod": True, "justification": "Side B."})
        assignment_id = second_grant.json()["id"]

        authenticate_as("AccessPilot.SoDAdmin")
        await client.get("/api/v1/sod/notifications")

        authenticate_as("AccessPilot.Admin")
        revoke = await client.post(f"/api/v1/assignments/{assignment_id}/revoke", json={"justification": "Resolving the conflict."})
        assert revoke.status_code == 200

        authenticate_as("AccessPilot.SoDAdmin")
        notifications = await client.get("/api/v1/sod/notifications")
    matching = [n for n in notifications.json() if n["notification_type"] == "NEW_VIOLATION" and n["sod_policy_id"] == policy_id]
    assert len(matching) == 1
    assert matching[0]["resolved_at"] is not None


@pytest.mark.asyncio
async def test_disabling_new_violation_notifications_stops_generating_them(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/sod/policies", json=_policy_payload(ids["group_a_id"], ids["group_b_id"]))
        settings_update = await client.patch("/api/v1/sod/notification-settings", json={"notify_on_new_violation": False, "notify_on_exception_expiring": True, "exception_expiring_warning_days": 7, "notify_on_exception_requested": True, "cooldown_enabled": False, "cooldown_hours": 24})
        assert settings_update.status_code == 200
        assert settings_update.json()["notify_on_new_violation"] is False

        authenticate_as("AccessPilot.Admin")
        await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_a_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "override_sod": True, "justification": "Side A."})
        await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_b_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "override_sod": True, "justification": "Side B."})

        authenticate_as("AccessPilot.SoDAdmin")
        notifications = await client.get("/api/v1/sod/notifications")
    assert notifications.status_code == 200
    assert all(n["notification_type"] != "NEW_VIOLATION" for n in notifications.json())


@pytest.mark.asyncio
async def test_plain_admin_cannot_change_notification_settings(db_override):
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch("/api/v1/sod/notification-settings", json={"notify_on_new_violation": False, "notify_on_exception_expiring": False, "exception_expiring_warning_days": 3, "notify_on_exception_requested": True, "cooldown_enabled": False, "cooldown_hours": 24})
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_an_expiring_exception_generates_a_notification(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        policy = await client.post("/api/v1/sod/policies", json=_policy_payload(ids["group_a_id"], ids["group_b_id"]))
        policy_id = policy.json()["id"]
        soon = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        exception = await client.post("/api/v1/sod/exceptions", json={"sod_policy_id": policy_id, "user_id": str(ids["user_id"]), "justification": "Short-lived acceptance.", "expires_at": soon})
        assert exception.status_code == 201

        notifications = await client.get("/api/v1/sod/notifications")
    matching = [n for n in notifications.json() if n["notification_type"] == "EXCEPTION_EXPIRING"]
    assert len(matching) == 1
    assert matching[0]["resolved_at"] is None


@pytest.mark.asyncio
async def test_revoking_an_exception_resolves_its_expiring_notification(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        policy = await client.post("/api/v1/sod/policies", json=_policy_payload(ids["group_a_id"], ids["group_b_id"]))
        policy_id = policy.json()["id"]
        soon = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        exception = await client.post("/api/v1/sod/exceptions", json={"sod_policy_id": policy_id, "user_id": str(ids["user_id"]), "justification": "Short-lived acceptance.", "expires_at": soon})
        exception_id = exception.json()["id"]
        await client.get("/api/v1/sod/notifications")

        await client.delete(f"/api/v1/sod/exceptions/{exception_id}")
        notifications = await client.get("/api/v1/sod/notifications")
    matching = [n for n in notifications.json() if n["notification_type"] == "EXCEPTION_EXPIRING"]
    assert len(matching) == 1
    assert matching[0]["resolved_at"] is not None


@pytest.mark.asyncio
async def test_an_expired_but_still_violating_exception_generates_a_notification(db_override):
    """The user's report: setting a time period on an exception did nothing once it lapsed — the underlying real
    access was never automatically revoked (deliberately — an exception is scoped to (policy, user), not a
    specific resource, so there's nothing a general worker could safely revoke on its own) but nothing told
    anyone either. This is the notify-only fix: EXCEPTION_EXPIRED fires once expires_at has passed, nothing has
    replaced it, and the conflict it was covering is still real."""
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        policy = await client.post("/api/v1/sod/policies", json=_policy_payload(ids["group_a_id"], ids["group_b_id"]))
        policy_id = policy.json()["id"]

    async with db_override.factory() as session:
        session.add(AccessAssignment(provider_id=ids["provider_id"], user_id=ids["user_id"], resource_type="GROUP", resource_id=ids["group_a_id"], assignment_type="PERMANENT", status="ACTIVE"))
        session.add(AccessAssignment(provider_id=ids["provider_id"], user_id=ids["user_id"], resource_type="GROUP", resource_id=ids["group_b_id"], assignment_type="PERMANENT", status="ACTIVE"))
        past = datetime.now(timezone.utc) - timedelta(days=1)
        session.add(SodException(sod_policy_id=UUID(policy_id), user_id=ids["user_id"], justification="Expired already.", expires_at=past))
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        notifications = await client.get("/api/v1/sod/notifications")
    matching = [n for n in notifications.json() if n["notification_type"] == "EXCEPTION_EXPIRED"]
    assert len(matching) == 1
    assert matching[0]["resolved_at"] is None
    assert "wasn't automatically revoked" in matching[0]["message"].lower()


@pytest.mark.asyncio
async def test_a_fresh_exception_resolves_the_expired_notification_for_the_old_one(db_override):
    """If a new exception is granted covering the same policy/user pair after the old one expired, the old
    EXCEPTION_EXPIRED notification must resolve — a fresh exception now covers it, nothing left to flag."""
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        policy = await client.post("/api/v1/sod/policies", json=_policy_payload(ids["group_a_id"], ids["group_b_id"]))
        policy_id = policy.json()["id"]

        async with db_override.factory() as session:
            session.add(AccessAssignment(provider_id=ids["provider_id"], user_id=ids["user_id"], resource_type="GROUP", resource_id=ids["group_a_id"], assignment_type="PERMANENT", status="ACTIVE"))
            session.add(AccessAssignment(provider_id=ids["provider_id"], user_id=ids["user_id"], resource_type="GROUP", resource_id=ids["group_b_id"], assignment_type="PERMANENT", status="ACTIVE"))
            past = datetime.now(timezone.utc) - timedelta(days=1)
            session.add(SodException(sod_policy_id=UUID(policy_id), user_id=ids["user_id"], justification="Expired already.", expires_at=past))
            await session.commit()

        first_check = await client.get("/api/v1/sod/notifications")
        assert len([n for n in first_check.json() if n["notification_type"] == "EXCEPTION_EXPIRED"]) == 1

        future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        fresh = await client.post("/api/v1/sod/exceptions", json={"sod_policy_id": policy_id, "user_id": str(ids["user_id"]), "justification": "Renewed acceptance.", "expires_at": future})
        assert fresh.status_code == 201

        second_check = await client.get("/api/v1/sod/notifications")
    still_open = [n for n in second_check.json() if n["notification_type"] == "EXCEPTION_EXPIRED" and n["resolved_at"] is None]
    assert len(still_open) == 0


@pytest.mark.asyncio
async def test_marking_a_notification_read_and_mark_all_read(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        policy = await client.post("/api/v1/sod/policies", json=_policy_payload(ids["group_a_id"], ids["group_b_id"]))
        policy_id = policy.json()["id"]
        soon = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        await client.post("/api/v1/sod/exceptions", json={"sod_policy_id": policy_id, "user_id": str(ids["user_id"]), "justification": "Short-lived.", "expires_at": soon})
        first_list = await client.get("/api/v1/sod/notifications")
        notification_id = first_list.json()[0]["id"]

        read_one = await client.post(f"/api/v1/sod/notifications/{notification_id}/read")
        assert read_one.status_code == 204
        after_one = await client.get("/api/v1/sod/notifications")
        assert next(n for n in after_one.json() if n["id"] == notification_id)["read_at"] is not None

        mark_all = await client.post("/api/v1/sod/notifications/read-all")
        assert mark_all.status_code == 204
        after_all = await client.get("/api/v1/sod/notifications")
    assert all(n["read_at"] is not None for n in after_all.json())


@pytest.mark.asyncio
async def test_revoking_an_exception_also_revokes_the_eligible_assignment_it_covered(db_override):
    """The user's explicit follow-up ask after testing the grant/revoke flow themselves: "revoke means revoke
    from eligible and active both, it doesn't matter" — revoking the risk acceptance must also end the specific
    access it was granted to cover, not just block future grants while the one already let through keeps
    sitting there untouched."""
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        policy = await client.post("/api/v1/sod/policies", json=_policy_payload(ids["group_a_id"], ids["group_b_id"]))
        policy_id = policy.json()["id"]

        authenticate_as("AccessPilot.Admin")
        await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_a_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Side A."})
        blocked = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_b_id"]), "assignment_type": "PERMANENT", "justification": "Side B, expect blocked."})
        assert blocked.status_code == 409

        request_response = await client.post("/api/v1/sod/exception-requests", json={"sod_policy_id": policy_id, "user_id": str(ids["user_id"]), "justification": "Business need.", "resource_type": "GROUP", "resource_id": str(ids["group_b_id"])})
        request_id = request_response.json()["id"]

        authenticate_as("AccessPilot.SoDAdmin")
        future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        granted = await client.post(f"/api/v1/sod/exception-requests/{request_id}/grant", json={"expires_at": future})
        exception_id = granted.json()["sod_exception_id"]

        authenticate_as("AccessPilot.Admin")
        assignments = await client.get("/api/v1/assignments")
        eligible = next(a for a in assignments.json() if a["user_id"] == str(ids["user_id"]) and a["resource_id"] == str(ids["group_b_id"]))
        assert eligible["status"] == "ELIGIBLE"

        authenticate_as("AccessPilot.SoDAdmin")
        revoke = await client.delete(f"/api/v1/sod/exceptions/{exception_id}")
        assert revoke.status_code == 204

        authenticate_as("AccessPilot.Admin")
        after = await client.get("/api/v1/assignments")
        revoked = next(a for a in after.json() if a["id"] == eligible["id"])
        assert revoked["status"] == "REVOKED"

        authenticate_as("AccessPilot.User", subject="target-user")
        target_notifications = await client.get("/api/v1/notifications")
    matching = [n for n in target_notifications.json() if n["notification_type"] == "SOD_EXCEPTION_LAPSED"]
    assert len(matching) == 1
    assert "was revoked" in matching[0]["message"]


@pytest.mark.asyncio
async def test_revoking_an_exception_revokes_the_right_assignment_not_an_old_unrelated_one(db_override):
    """Real bug found live testing this feature: an old, wholly unrelated ELIGIBLE assignment for the exact same
    (user, resource) target — left over from before this policy or exception ever existed — could be picked
    instead of the one this exception actually covers, because the original match had no ordering/tiebreaker at
    all. Confirmed live against real Postgres: a leftover row from weeks earlier got silently revoked instead of
    the actually-relevant one. _find_exception_granted_assignment now filters to assignments created at/after the
    exception request (with a small grace window for ordinary clock jitter between the two writes), so the old
    row must be left completely untouched and the new one revoked."""
    ids = await _seed_directory(db_override.factory)
    async with db_override.factory() as session:
        # created_at is set explicitly, days in the past, rather than left to just-now — a test run creates
        # everything within milliseconds of real wall-clock time, which would otherwise fall inside the fix's
        # own grace window (meant for ordinary clock jitter of a few seconds, not to distinguish two rows created
        # in the same test). This mirrors the real live bug: a leftover row from weeks earlier, not microseconds.
        stale_leftover = AccessAssignment(provider_id=ids["provider_id"], user_id=ids["user_id"], resource_type="GROUP", resource_id=ids["group_b_id"], assignment_type="PERMANENT", status="ELIGIBLE", created_at=datetime.now(timezone.utc) - timedelta(days=10))
        session.add(stale_leftover)
        await session.commit()
        stale_leftover_id = str(stale_leftover.id)

    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        policy = await client.post("/api/v1/sod/policies", json=_policy_payload(ids["group_a_id"], ids["group_b_id"]))
        policy_id = policy.json()["id"]

        authenticate_as("AccessPilot.Admin")
        await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_a_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Side A."})

        request_response = await client.post("/api/v1/sod/exception-requests", json={"sod_policy_id": policy_id, "user_id": str(ids["user_id"]), "justification": "Business need.", "resource_type": "GROUP", "resource_id": str(ids["group_b_id"])})
        request_id = request_response.json()["id"]

        authenticate_as("AccessPilot.SoDAdmin")
        future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        granted = await client.post(f"/api/v1/sod/exception-requests/{request_id}/grant", json={"expires_at": future})
        exception_id = granted.json()["sod_exception_id"]

        authenticate_as("AccessPilot.Admin")
        assignments = await client.get("/api/v1/assignments")
        candidates = [a for a in assignments.json() if a["user_id"] == str(ids["user_id"]) and a["resource_id"] == str(ids["group_b_id"]) and a["status"] == "ELIGIBLE"]
        assert len(candidates) == 2  # the stale leftover plus the one this grant just created
        newly_granted_id = next(a["id"] for a in candidates if a["id"] != stale_leftover_id)

        authenticate_as("AccessPilot.SoDAdmin")
        revoke = await client.delete(f"/api/v1/sod/exceptions/{exception_id}")
        assert revoke.status_code == 204

        authenticate_as("AccessPilot.Admin")
        after = await client.get("/api/v1/assignments")
    by_id = {a["id"]: a for a in after.json()}
    assert by_id[newly_granted_id]["status"] == "REVOKED"
    assert by_id[stale_leftover_id]["status"] == "ELIGIBLE"


@pytest.mark.asyncio
async def test_revoking_an_exception_also_revokes_the_active_assignment_it_covered(db_override):
    """Same as the ELIGIBLE case above, but for real ACTIVE access — "it doesn't matter" which status it's in."""
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        policy = await client.post("/api/v1/sod/policies", json=_policy_payload(ids["group_a_id"], ids["group_b_id"]))
        policy_id = policy.json()["id"]

        authenticate_as("AccessPilot.Admin")
        await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_a_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Side A."})
        blocked = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_b_id"]), "assignment_type": "PERMANENT", "justification": "Side B, expect blocked."})
        assert blocked.status_code == 409

        request_response = await client.post("/api/v1/sod/exception-requests", json={"sod_policy_id": policy_id, "user_id": str(ids["user_id"]), "justification": "Business need.", "resource_type": "GROUP", "resource_id": str(ids["group_b_id"])})
        request_id = request_response.json()["id"]

        authenticate_as("AccessPilot.SoDAdmin")
        future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        granted = await client.post(f"/api/v1/sod/exception-requests/{request_id}/grant", json={"expires_at": future})
        exception_id = granted.json()["sod_exception_id"]

        authenticate_as("AccessPilot.Admin")
        assignments = await client.get("/api/v1/assignments")
        eligible = next(a for a in assignments.json() if a["user_id"] == str(ids["user_id"]) and a["resource_id"] == str(ids["group_b_id"]))
        assignment_id = eligible["id"]

        authenticate_as("AccessPilot.User", subject="target-user")
        activated = await client.post(f"/api/v1/assignments/{assignment_id}/activate", json={"duration_hours": 3, "justification": "Need it now."})
        assert activated.status_code == 200
        assert activated.json()["status"] == "ACTIVE"

        authenticate_as("AccessPilot.SoDAdmin")
        revoke = await client.delete(f"/api/v1/sod/exceptions/{exception_id}")
        assert revoke.status_code == 204

        authenticate_as("AccessPilot.Admin")
        after = await client.get("/api/v1/assignments")
    revoked = next(a for a in after.json() if a["id"] == assignment_id)
    assert revoked["status"] == "REVOKED"


@pytest.mark.asyncio
async def test_an_expired_exception_is_auto_revoked_by_the_background_worker(db_override):
    """The user's other explicit ask: "Active is 3 hr time and grant is 4 min then it must revoke in 4 min" — an
    exception's own expiry, not the assignment's activation duration, is what should end real access once it's
    reached. revoke_lapsed_sod_exceptions() is the service function the new sod_exception_expiry_worker_loop
    background worker polls every 60s (see workers/sod_expiry.py) — this exercises it directly, the same way the
    pre-existing activation-worker tests call activate_due_assignments() directly rather than sleeping for real."""
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        policy = await client.post("/api/v1/sod/policies", json=_policy_payload(ids["group_a_id"], ids["group_b_id"]))
        policy_id = policy.json()["id"]

        authenticate_as("AccessPilot.Admin")
        await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_a_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Side A."})
        blocked = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_b_id"]), "assignment_type": "PERMANENT", "justification": "Side B, expect blocked."})
        assert blocked.status_code == 409

        request_response = await client.post("/api/v1/sod/exception-requests", json={"sod_policy_id": policy_id, "user_id": str(ids["user_id"]), "justification": "Business need.", "resource_type": "GROUP", "resource_id": str(ids["group_b_id"])})
        request_id = request_response.json()["id"]

        authenticate_as("AccessPilot.SoDAdmin")
        future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        granted = await client.post(f"/api/v1/sod/exception-requests/{request_id}/grant", json={"expires_at": future})
        exception_id = granted.json()["sod_exception_id"]

        authenticate_as("AccessPilot.Admin")
        assignments = await client.get("/api/v1/assignments")
        eligible = next(a for a in assignments.json() if a["user_id"] == str(ids["user_id"]) and a["resource_id"] == str(ids["group_b_id"]))
        assignment_id = eligible["id"]

        authenticate_as("AccessPilot.User", subject="target-user")
        activated = await client.post(f"/api/v1/assignments/{assignment_id}/activate", json={"duration_hours": 3, "justification": "Need it now."})
        assert activated.status_code == 200

    # Simulate the exception's 4-minute grant window having elapsed, well before the assignment's own 3-hour
    # activation window would naturally end.
    async with db_override.factory() as session:
        exception = await session.get(SodException, UUID(exception_id))
        exception.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        await session.commit()

        revoked_count = await revoke_lapsed_sod_exceptions(session)
        assert revoked_count == 1

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        authenticate_as("AccessPilot.Admin")
        after = await client.get("/api/v1/assignments")
        revoked = next(a for a in after.json() if a["id"] == assignment_id)
        assert revoked["status"] == "REVOKED"

        authenticate_as("AccessPilot.User", subject="target-user")
        target_notifications = await client.get("/api/v1/notifications")
    matching = [n for n in target_notifications.json() if n["notification_type"] == "SOD_EXCEPTION_LAPSED"]
    assert len(matching) == 1
    assert "expired" in matching[0]["message"]

    # Idempotent: a second poll finds nothing left to do for the same, already-revoked exception.
    async with db_override.factory() as session:
        second_pass_count = await revoke_lapsed_sod_exceptions(session)
    assert second_pass_count == 0


@pytest.mark.asyncio
async def test_a_directly_granted_exception_with_no_linked_request_has_nothing_to_auto_revoke(db_override):
    """An exception granted via the older, untargeted POST /sod/exceptions path has no specific resource behind
    it (see SodException's own docstring — scoped to (policy, user), not a resource) — revoking or expiring it
    must not crash looking for something to revoke, and correctly finds nothing."""
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        policy = await client.post("/api/v1/sod/policies", json=_policy_payload(ids["group_a_id"], ids["group_b_id"]))
        policy_id = policy.json()["id"]
        soon = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        created = await client.post("/api/v1/sod/exceptions", json={"sod_policy_id": policy_id, "user_id": str(ids["user_id"]), "justification": "Untargeted.", "expires_at": soon})
        exception_id = created.json()["id"]

        revoke = await client.delete(f"/api/v1/sod/exceptions/{exception_id}")
        assert revoke.status_code == 204

    async with db_override.factory() as session:
        exception = await session.get(SodException, UUID(exception_id))
        exception.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        await session.commit()
        revoked_count = await revoke_lapsed_sod_exceptions(session)
    assert revoked_count == 0


@pytest.mark.asyncio
async def test_an_eligible_assignment_covered_by_a_live_exception_shows_its_expiry(db_override):
    """The user's ask after seeing "No activation deadline" on an eligible row that was, in fact, time-boxed by
    an SoD exception: the assignment response should surface that real ceiling (sod_exception_expires_at) so the
    frontend can show it instead of implying there's no deadline at all."""
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        policy = await client.post("/api/v1/sod/policies", json=_policy_payload(ids["group_a_id"], ids["group_b_id"]))
        policy_id = policy.json()["id"]

        authenticate_as("AccessPilot.Admin")
        await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_a_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Side A."})

        request_response = await client.post("/api/v1/sod/exception-requests", json={"sod_policy_id": policy_id, "user_id": str(ids["user_id"]), "justification": "Business need.", "resource_type": "GROUP", "resource_id": str(ids["group_b_id"])})
        request_id = request_response.json()["id"]

        authenticate_as("AccessPilot.SoDAdmin")
        future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        await client.post(f"/api/v1/sod/exception-requests/{request_id}/grant", json={"expires_at": future})

        authenticate_as("AccessPilot.Admin")
        assignments = await client.get("/api/v1/assignments")
    eligible = next(a for a in assignments.json() if a["user_id"] == str(ids["user_id"]) and a["resource_id"] == str(ids["group_b_id"]))
    assert eligible["status"] == "ELIGIBLE"
    assert eligible["sod_exception_expires_at"] is not None
    assert eligible["sod_exception_expires_at"][:10] == future[:10]


@pytest.mark.asyncio
async def test_a_plain_assignment_with_no_covering_exception_has_a_null_sod_field(db_override):
    """An ordinary assignment never touched by the exception-request workflow must not show any exception ceiling."""
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_a_id"]), "assignment_type": "PERMANENT", "justification": "Plain, unrelated grant."})
    assert created.json()["sod_exception_expires_at"] is None
