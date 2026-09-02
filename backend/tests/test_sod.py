from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import AccessAssignment, AccessPackage, AccessPackageItem, Application, Group, IdentityProvider, Role, SodAdmin, User, UserGroup
from app.security.auth import AuthenticatedUser, require_authenticated_user
from app.services.sod import is_sod_admin
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
    """Mirrors the normal (non-bypass) Admin/end-user flow: request the application role (lands ELIGIBLE, no
    approver), then self-activate it — this is the enforcement point most real usage actually exercises, unlike
    the bypass-create tests above."""
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
        eligible = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "APPLICATION", "resource_id": str(ids["application_id"]), "app_role_external_id": "approle-approver", "assignment_type": "PERMANENT", "justification": "Requesting the conflicting application role."})
        print("REQUEST APPLICATION ROLE (non-bypass)", eligible.status_code, eligible.json())
        assert eligible.status_code == 201
        assert eligible.json()["status"] == "ELIGIBLE"
        assignment_id = eligible.json()["id"]

        authenticate_as("AccessPilot.User", subject="target-user")
        activation = await client.post(f"/api/v1/assignments/{assignment_id}/activate", json={"duration_hours": 1, "justification": "Need it now."})
        print("SELF-ACTIVATE APPLICATION ROLE", activation.status_code, activation.json())
    assert activation.status_code == 409
    assert activation.json()["error"]["code"] == "SOD_CONFLICT"


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
async def test_admin_can_read_violations_and_manage_roster_but_not_rules(db_override):
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        violations = await client.get("/api/v1/sod/violations")
        assert violations.status_code == 200
        roster = await client.post("/api/v1/sod/admins", json={"user_id": str(ids["user_id"])})
        assert roster.status_code == 201
        assert roster.json()["user_display_name"] == "Target User"
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
    ids = await _seed_directory(db_override.factory)
    authenticate_as("AccessPilot.SoDAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/sod/policies", json=_policy_payload(ids["group_a_id"], ids["group_b_id"]))

        authenticate_as("AccessPilot.Admin")
        await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_a_id"]), "assignment_type": "PERMANENT", "bypass_activation": True, "justification": "Initiator access."})
        eligible = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_b_id"]), "assignment_type": "PERMANENT", "justification": "Requesting approver access too."})
        assert eligible.status_code == 201
        assignment_id = eligible.json()["id"]

        authenticate_as("AccessPilot.User", subject="target-user")
        self_activate = await client.post(f"/api/v1/assignments/{assignment_id}/activate", json={"duration_hours": 1, "justification": "Need it now."})
        assert self_activate.status_code == 409
        assert self_activate.json()["error"]["code"] == "SOD_CONFLICT"

        authenticate_as("AccessPilot.Admin")
        admin_override = await client.post(f"/api/v1/assignments/{assignment_id}/activate", json={"duration_hours": 1, "justification": "Approved exception.", "override_sod": True})
        assert admin_override.status_code == 200
        assert admin_override.json()["status"] == "ACTIVE"


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
async def test_is_sod_admin_reflects_the_roster(db_override):
    ids = await _seed_directory(db_override.factory)
    async with db_override.factory() as session:
        assert await is_sod_admin(session, "target-user") is False
        session.add(SodAdmin(user_id=ids["user_id"]))
        await session.commit()
    async with db_override.factory() as session:
        assert await is_sod_admin(session, "target-user") is True
        assert await is_sod_admin(session, "breakglass:unrelated") is False


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
        settings_update = await client.patch("/api/v1/sod/notification-settings", json={"notify_on_new_violation": False, "notify_on_exception_expiring": True, "exception_expiring_warning_days": 7})
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
        response = await client.patch("/api/v1/sod/notification-settings", json={"notify_on_new_violation": False, "notify_on_exception_expiring": False, "exception_expiring_warning_days": 3})
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
