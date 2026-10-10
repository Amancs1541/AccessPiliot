from datetime import date, datetime, timedelta, timezone
from itertools import count
from uuid import UUID

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import AccessAssignment, BirthrightPolicy, Group, IdentityAccount, IdentityProvider, JoinerRequest, LifecycleEvent, Notification, User
from app.providers.base import CreatedUser, NormalizedUser, ProviderConflictError
from app.providers.graph_client import GraphError
from app.security.auth import AuthenticatedUser, require_authenticated_user
from app.services.joiner import sweep_joiners


class TestSession:
    __test__ = False

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


class FakeConnector:
    """Records create/enable calls. `fail_create` provider names raise; `conflict` names raise a duplicate."""
    created: list = []
    enabled_calls: list = []
    fail_create: set = set()
    fail_enable: set = set()
    conflict: set = set()
    ids = count(1)

    def __init__(self, provider):
        self.provider = provider

    async def create_user(self, request):
        if self.provider.name in FakeConnector.conflict:
            raise ProviderConflictError("exists")
        if self.provider.name in FakeConnector.fail_create:
            raise GraphError("PROVIDER_UNAVAILABLE", "directory down", 503)
        FakeConnector.created.append((self.provider.name, request))
        external_id = f"{self.provider.name.lower()}-{next(FakeConnector.ids)}"
        return CreatedUser(user=NormalizedUser(external_id, request.user_principal_name, request.display_name, given_name=request.given_name, surname=request.surname, department=request.department, job_title=request.job_title, status="ACTIVE" if request.enabled else "DISABLED"), temporary_password=f"pw-{self.provider.name}")

    async def set_user_manager(self, external_id, manager_external_id):
        raise NotImplementedError("not supported")

    async def set_user_enabled(self, external_id, enabled):
        if self.provider.name in FakeConnector.fail_enable:
            raise GraphError("PROVIDER_UNAVAILABLE", "cannot enable", 503)
        FakeConnector.enabled_calls.append((self.provider.name, external_id, enabled))
        return True


@pytest.fixture(autouse=True)
def fake_connector(monkeypatch):
    FakeConnector.created, FakeConnector.enabled_calls = [], []
    FakeConnector.fail_create, FakeConnector.fail_enable, FakeConnector.conflict = set(), set(), set()
    monkeypatch.setattr("app.services.joiner._connector", lambda provider: FakeConnector(provider))


async def _seed(session):
    entra = IdentityProvider(name="Entra", type="ENTRA", status="CONNECTED", tenant_id="t1")
    okta = IdentityProvider(name="Okta", type="OKTA", status="CONNECTED", tenant_id="t2", provisioning_domain="okta.example.com", username_convention="{first}.{last}")
    csv = IdentityProvider(name="CSV", type="CSV", status="CONNECTED", tenant_id="csv")
    session.add_all([entra, okta, csv])
    await session.flush()
    boss = User(provider_id=entra.id, external_id="boss", email="boss@x.com", display_name="Boss Person", status="ACTIVE")
    group = Group(provider_id=entra.id, external_id="g-fin", name="Finance Group", status="ACTIVE", is_privileged=False)
    session.add_all([boss, group])
    await session.flush()
    session.add(BirthrightPolicy(name="Finance", match_field="department", match_value="Finance", resource_type="GROUP", resource_id=group.id))
    await session.commit()
    return {"entra": entra.id, "okta": okta.id, "csv": csv.id, "boss": boss.id}


def body(ids, *, start, targets=None, **extra):
    payload = {
        "first_name": "Nina", "last_name": "Newhire", "work_email": "nina.newhire@company.com", "employee_id": "EMP9001", "department": "Finance", "job_title": "Analyst",
        "manager_id": str(ids["boss"]), "employee_category": "EMPLOYEE", "employment_type": "EMPLOYEE", "start_at": start.isoformat(),
        "targets": targets or [{"provider_id": str(ids["entra"])}, {"provider_id": str(ids["okta"])}],
    }
    payload.update(extra)
    return payload


def future(days=5):
    return datetime.now(timezone.utc) + timedelta(days=days)


@pytest.mark.asyncio
async def test_a_future_joiner_gets_disabled_accounts_in_every_chosen_idp_and_one_time_passwords(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    leaver = (date.today() + timedelta(days=400)).isoformat()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/lifecycle/joiners", json=body(ids, start=future(), leaver_date=leaver))
        listed = (await client.get("/api/v1/lifecycle/joiners")).json()
    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "SCHEDULED" and data["display_name"] == "Nina Newhire"
    by_name = {t["provider_name"]: t for t in data["targets"]}
    assert by_name["Entra"]["status"] == "CREATED" and by_name["Entra"]["username"] == "nina.newhire@company.com" and by_name["Entra"]["temporary_password"] == "pw-Entra"
    assert by_name["Okta"]["username"] == "nina.newhire@okta.example.com" and by_name["Okta"]["temporary_password"] == "pw-Okta"   # convention + provisioning domain
    assert all(request.enabled is False for _, request in FakeConnector.created)                                                       # created DISABLED
    assert FakeConnector.enabled_calls == []
    assert all(t["temporary_password"] is None for t in listed[0]["targets"])                                                          # never shown again
    async with db_override.factory() as session:
        person = (await session.scalars(select(User).where(User.employee_id == "EMP9001"))).one()
        accounts = {a.provider_id: (a.status, a.provisioned_by) for a in (await session.scalars(select(IdentityAccount).where(IdentityAccount.user_id == person.id))).all()}
        stored = (await session.scalars(select(JoinerRequest))).one()
        grants = (await session.scalars(select(AccessAssignment).where(AccessAssignment.user_id == person.id))).all()
    assert person.status == "DISABLED" and person.manager_id == ids["boss"] and person.employee_category == "EMPLOYEE" and person.leaver_date == date.fromisoformat(leaver) and person.source == "JOINER"
    assert accounts == {ids["entra"]: ("DISABLED", "JOINER"), ids["okta"]: ("DISABLED", "JOINER")}
    assert "pw-Entra" not in str(stored.targets) and grants == []          # passwords not stored; no access until the start date
    assert (await_first_request := FakeConnector.created[0][1]).given_name == "Nina" and await_first_request.employee_id == "EMP9001"


@pytest.mark.asyncio
async def test_a_joiner_starting_now_is_enabled_immediately_with_eligible_access_and_notifications(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/lifecycle/joiners", json=body(ids, start=datetime.now(timezone.utc)))
    assert response.json()["status"] == "ACTIVE" and {t["status"] for t in response.json()["targets"]} == {"ENABLED"}
    assert sorted(c[0] for c in FakeConnector.enabled_calls) == ["Entra", "Okta"] and all(c[2] is True for c in FakeConnector.enabled_calls)
    async with db_override.factory() as session:
        person = (await session.scalars(select(User).where(User.employee_id == "EMP9001"))).one()
        grants = (await session.scalars(select(AccessAssignment).where(AccessAssignment.user_id == person.id))).all()
        event = (await session.scalars(select(LifecycleEvent).where(LifecycleEvent.event_type == "JOINER"))).one()
        notes = (await session.scalars(select(Notification).where(Notification.notification_type == "LIFECYCLE_JOINER"))).all()
        accounts = (await session.scalars(select(IdentityAccount).where(IdentityAccount.user_id == person.id))).all()
    assert person.status == "ACTIVE" and [g.status for g in grants] == ["ELIGIBLE"] and event.source == "JOINER" and event.granted_count == 1
    assert [n.user_id for n in notes] == [ids["boss"]] and {a.status for a in accounts} == {"ACTIVE"}


@pytest.mark.asyncio
async def test_a_joiner_without_a_manager_is_rejected(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    payload = body(ids, start=future())
    del payload["manager_id"]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/lifecycle/joiners", json=payload)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_submitting_a_joiner_notifies_the_manager_for_audit(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/lifecycle/joiners", json=body(ids, start=future()))
    assert response.status_code == 201
    async with db_override.factory() as session:
        notes = (await session.scalars(select(Notification).where(Notification.notification_type == "JOINER_SUBMITTED"))).all()
    assert len(notes) == 1 and notes[0].user_id == ids["boss"]


@pytest.mark.asyncio
async def test_the_worker_activates_due_joiners_only(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = (await client.post("/api/v1/lifecycle/joiners", json=body(ids, start=future(3)))).json()
    async with db_override.factory() as session:
        assert await sweep_joiners(session) == 0                                   # start date not reached
        joiner = await session.get(JoinerRequest, created["id"] if not isinstance(created["id"], str) else __import__("uuid").UUID(created["id"]))
        joiner.start_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        await session.commit()
        assert await sweep_joiners(session) == 1
        assert await sweep_joiners(session) == 0                                   # only once
        await session.refresh(joiner)
        person = await session.get(User, joiner.user_id)
    assert joiner.status == "ACTIVE" and joiner.activated_at is not None and person.status == "ACTIVE"


@pytest.mark.asyncio
async def test_one_idp_failing_leaves_the_joiner_partial_and_retry_creates_it_later(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    FakeConnector.fail_create = {"Okta"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = (await client.post("/api/v1/lifecycle/joiners", json=body(ids, start=future()))).json()
        by_name = {t["provider_name"]: t for t in created["targets"]}
        assert by_name["Entra"]["status"] == "CREATED" and by_name["Okta"]["status"] == "FAILED" and "directory down" in by_name["Okta"]["error"]
        FakeConnector.fail_create = set()
        retried = (await client.post(f"/api/v1/lifecycle/joiners/{created['id']}/retry")).json()
        nothing = await client.post(f"/api/v1/lifecycle/joiners/{created['id']}/retry")
    assert {t["provider_name"]: t["status"] for t in retried["targets"]} == {"Entra": "CREATED", "Okta": "CREATED"} and retried["status"] == "SCHEDULED"
    assert [t["temporary_password"] for t in retried["targets"] if t["provider_name"] == "Okta"] == ["pw-Okta"]
    assert nothing.status_code == 422


@pytest.mark.asyncio
async def test_if_no_directory_can_create_the_account_nothing_is_saved(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    FakeConnector.fail_create = {"Entra", "Okta"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/lifecycle/joiners", json=body(ids, start=future()))
    assert response.status_code == 502 and response.json()["error"]["code"] == "JOINER_PROVISIONING_FAILED"
    async with db_override.factory() as session:
        assert (await session.scalars(select(JoinerRequest))).all() == []
        assert (await session.scalars(select(User).where(User.employee_id == "EMP9001"))).all() == []


@pytest.mark.asyncio
async def test_validation_duplicates_switched_off_idps_and_csv_are_rejected(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
        okta = await session.get(IdentityProvider, ids["okta"])
        okta.provision_joiners = False
        await session.commit()
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        switched_off = await client.post("/api/v1/lifecycle/joiners", json=body(ids, start=future()))
        csv_target = await client.post("/api/v1/lifecycle/joiners", json=body(ids, start=future(), targets=[{"provider_id": str(ids["csv"])}]))
        no_targets = await client.post("/api/v1/lifecycle/joiners", json=body(ids, start=future(), targets=[]))
        early_leaver = await client.post("/api/v1/lifecycle/joiners", json=body(ids, start=future(10), leaver_date=date.today().isoformat()))
        bad_email = await client.post("/api/v1/lifecycle/joiners", json={**body(ids, start=future()), "work_email": "nope"})
        ok = await client.post("/api/v1/lifecycle/joiners", json=body(ids, start=future(), targets=[{"provider_id": str(ids["entra"])}]))
        duplicate = await client.post("/api/v1/lifecycle/joiners", json={**body(ids, start=future(), targets=[{"provider_id": str(ids["entra"])}]), "work_email": "other@company.com"})
    assert switched_off.status_code == 422 and csv_target.status_code == 404 and no_targets.status_code == 422 and early_leaver.status_code == 422 and bad_email.status_code == 422
    assert ok.status_code == 201 and duplicate.status_code == 409 and duplicate.json()["error"]["code"] == "EMPLOYEE_ID_TAKEN"


@pytest.mark.asyncio
async def test_idp_switches_and_username_overrides_and_cancel_and_permissions(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        targets = (await client.get("/api/v1/lifecycle/joiner-targets")).json()
        toggled = await client.put(f"/api/v1/lifecycle/joiner-targets/{ids['okta']}", json={"enabled": False})
        csv_toggle = await client.put(f"/api/v1/lifecycle/joiner-targets/{ids['csv']}", json={"enabled": False})
        await client.put(f"/api/v1/lifecycle/joiner-targets/{ids['okta']}", json={"enabled": True})
        created = (await client.post("/api/v1/lifecycle/joiners", json=body(ids, start=future(), targets=[{"provider_id": str(ids["entra"]), "username": "custom.name@company.com"}]))).json()
        cancelled = await client.delete(f"/api/v1/lifecycle/joiners/{created['id']}")
        again = await client.delete(f"/api/v1/lifecycle/joiners/{created['id']}")
        authenticate_as("AccessPilot.User", subject="regular")
        denied = await client.post("/api/v1/lifecycle/joiners", json=body(ids, start=future()))
        denied_list = await client.get("/api/v1/lifecycle/joiners")
    assert [(t["name"], t["provision_joiners"]) for t in targets] == [("Entra", True), ("Okta", True)]      # CSV bookkeeping provider is never a target
    assert {t["name"]: t["provision_joiners"] for t in toggled.json()}["Okta"] is False and csv_toggle.status_code == 404
    assert created["targets"][0]["username"] == "custom.name@company.com"
    assert cancelled.json()["status"] == "CANCELLED" and again.status_code == 409 and denied.status_code == 403 and denied_list.status_code == 403


@pytest.mark.asyncio
async def test_manager_is_pushed_to_each_directory_best_effort(db_override, monkeypatch):
    pushed = []

    async def set_manager(self, external_id, manager_external_id):
        if self.provider.name == "Okta":
            raise NotImplementedError("not supported")
        pushed.append((self.provider.name, external_id, manager_external_id))
        return True

    monkeypatch.setattr(FakeConnector, "set_user_manager", set_manager, raising=False)
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/lifecycle/joiners", json=body(ids, start=future()))
    assert response.status_code == 201                       # Okta not supporting it never fails the joiner
    assert [(n, m) for n, _, m in pushed] == [("Entra", "boss")]          # the boss lives in Entra; Okta has no boss account


@pytest.mark.asyncio
async def test_cancelling_a_joiner_can_also_delete_the_created_accounts(db_override, monkeypatch):
    deleted = []

    async def delete_user(self, external_id):
        deleted.append((self.provider.name, external_id))
        return True

    monkeypatch.setattr(FakeConnector, "delete_user", delete_user, raising=False)
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = (await client.post("/api/v1/lifecycle/joiners", json=body(ids, start=future()))).json()
        keep = await client.delete(f"/api/v1/lifecycle/joiners/{created['id']}")
    assert keep.status_code == 200 and deleted == []          # default: accounts remain, disabled
    async with db_override.factory() as session:
        ids2 = ids
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        second = (await client.post("/api/v1/lifecycle/joiners", json=body(ids2, start=future(), employee_id="EMP9002", work_email="nina2@company.com"))).json()
        gone = await client.delete(f"/api/v1/lifecycle/joiners/{second['id']}?delete_accounts=true")
    assert gone.status_code == 200 and {t["status"] for t in gone.json()["targets"]} == {"DELETED"}
    assert sorted(n for n, _ in deleted) == ["Entra", "Okta"]


def test_username_follows_the_provider_policy_at_creation_time():
    from app.services.joiner import _username_for
    both = IdentityProvider(name="A", type="ENTRA", provisioning_domain="corp.onmicrosoft.com", username_convention="{first}.{last}")
    domain_only = IdentityProvider(name="B", type="ENTRA", provisioning_domain="corp.onmicrosoft.com", username_convention=None)
    convention_only = IdentityProvider(name="C", type="OKTA", provisioning_domain=None, username_convention="{f}{last}")
    neither = IdentityProvider(name="D", type="OKTA", provisioning_domain=None, username_convention=None)
    email = "nina.newhire@company.com"
    assert _username_for(both, "Nina", "Newhire", email, None) == "nina.newhire@corp.onmicrosoft.com"
    assert _username_for(domain_only, "Nina", "Newhire", email, None) == "nina.newhire@corp.onmicrosoft.com"      # local part from the work email
    assert _username_for(convention_only, "Nina", "Newhire", email, None) == "nnewhire@company.com"                # convention + the work email's own domain
    assert _username_for(neither, "Nina", "Newhire", email, None) == email
    assert _username_for(both, "Nina", "Newhire", email, "custom@x.com") == "custom@x.com"                       # an explicit admin override still wins


@pytest.mark.asyncio
async def test_a_second_directory_is_auto_detected_when_the_department_has_birthright_access_there(db_override):
    """Account mapping: the admin only picked Entra as a target, but Design's birthright policy grants a group
    that lives in a THIRD, never-explicitly-picked provider — that directory's account must still be created,
    auto-detected from what the person's own department would be granted."""
    async with db_override.factory() as session:
        ids = await _seed(session)
        ad = IdentityProvider(name="Active Directory", type="ACTIVE_DIRECTORY", status="CONNECTED", tenant_id="t3")
        session.add(ad)
        await session.flush()
        ad_group = Group(provider_id=ad.id, external_id="ad-design", name="Design Team (AD)", status="ACTIVE", is_privileged=False)
        session.add(ad_group)
        await session.flush()
        session.add(BirthrightPolicy(name="Design", match_field="department", match_value="Design", resource_type="GROUP", resource_id=ad_group.id))
        await session.commit()
        ids["ad"] = ad.id

    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/lifecycle/joiners", json=body(ids, start=future(), targets=[{"provider_id": str(ids["entra"])}], department="Design"))
    assert response.status_code == 201
    by_name = {t["provider_name"]: t["status"] for t in response.json()["targets"]}
    assert by_name == {"Entra": "CREATED", "Active Directory": "CREATED"}  # AD was never explicitly picked
    assert any(name == "Active Directory" for name, _ in FakeConnector.created)


@pytest.mark.asyncio
async def test_auto_detected_directory_is_skipped_silently_when_provisioning_is_switched_off(db_override):
    """Unlike an explicitly picked, switched-off provider (which is a hard 422), an auto-DETECTED one that's
    switched off is just skipped — it was never something the admin actually asked for."""
    async with db_override.factory() as session:
        ids = await _seed(session)
        ad = IdentityProvider(name="Active Directory", type="ACTIVE_DIRECTORY", status="CONNECTED", tenant_id="t3", provision_joiners=False)
        session.add(ad)
        await session.flush()
        ad_group = Group(provider_id=ad.id, external_id="ad-design", name="Design Team (AD)", status="ACTIVE", is_privileged=False)
        session.add(ad_group)
        await session.flush()
        session.add(BirthrightPolicy(name="Design", match_field="department", match_value="Design", resource_type="GROUP", resource_id=ad_group.id))
        await session.commit()
        ids["ad"] = ad.id

    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/lifecycle/joiners", json=body(ids, start=future(), targets=[{"provider_id": str(ids["entra"])}], department="Design"))
    assert response.status_code == 201
    by_name = {t["provider_name"]: t["status"] for t in response.json()["targets"]}
    assert by_name == {"Entra": "CREATED"}  # AD silently skipped, not an error


@pytest.mark.asyncio
async def test_optional_profile_fields_reach_the_connectors_create_user_call(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/lifecycle/joiners", json=body(ids, start=future(), targets=[{"provider_id": str(ids["entra"])}], office="HQ-4", company="Acme Corp", mobile_phone="+1-555-0100", street_address="1 Main St", city="Springfield", state="IL", postal_code="62701", country="USA", description="A note"))
    assert response.status_code == 201
    request = next(r for name, r in FakeConnector.created if name == "Entra")
    assert request.office == "HQ-4" and request.company == "Acme Corp" and request.mobile_phone == "+1-555-0100"
    assert request.street_address == "1 Main St" and request.city == "Springfield" and request.state == "IL"
    assert request.postal_code == "62701" and request.country == "USA" and request.description == "A note"


@pytest.mark.asyncio
async def test_a_connector_that_cannot_create_accounts_yet_fails_that_one_target_cleanly(db_override, monkeypatch):
    """A provider whose connector doesn't support create_user yet (e.g. Active Directory's today) must report a
    clean FAILED target with an explanatory error, never an unhandled 500 — regardless of whether it was
    explicitly picked or auto-detected."""
    async def not_implemented_create_user(self, request):
        raise NotImplementedError("create_user is not yet implemented for this connector")
    monkeypatch.setattr(FakeConnector, "create_user", not_implemented_create_user, raising=False)

    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/lifecycle/joiners", json=body(ids, start=future(), targets=[{"provider_id": str(ids["entra"])}]))
    assert response.status_code == 502  # no directory could create the account
    assert "does not support creating accounts yet" in response.json()["error"]["message"]


def test_active_directory_defaults_its_username_to_its_own_domain_not_the_work_emails():
    """Real bug found live: an AD provider with no provisioning_domain/username_convention configured fell back
    to the work email AS GIVEN — meaning a joiner's real AD account ended up with a userPrincipalName on the
    Entra tenant's own cloud domain (…@tenant.onmicrosoft.com) instead of the AD domain. AD's base DN already IS
    its real domain name, so that's the correct default, not the work email's domain."""
    from app.services.joiner import _username_for
    ad_no_domain_configured = IdentityProvider(name="Corporate AD", type="ACTIVE_DIRECTORY", tenant_id="DC=TeamDEV,DC=local")
    assert _username_for(ad_no_domain_configured, "Hardeep", "Zanzmera", "hardeep.zanzmera@workamanvgmail.onmicrosoft.com", None) == "hardeep.zanzmera@TeamDEV.local"

    # An explicit provisioning_domain still wins over the derived one.
    ad_explicit_domain = IdentityProvider(name="Corporate AD", type="ACTIVE_DIRECTORY", tenant_id="DC=TeamDEV,DC=local", provisioning_domain="other.local")
    assert _username_for(ad_explicit_domain, "Hardeep", "Zanzmera", "hardeep.zanzmera@workamanvgmail.onmicrosoft.com", None) == "hardeep.zanzmera@other.local"

    # Unchanged for Entra/Okta: no base-DN concept to derive from, so the work email's own domain stays the default.
    entra_no_domain_configured = IdentityProvider(name="Entra", type="ENTRA", tenant_id="tenant-id-not-a-dn")
    assert _username_for(entra_no_domain_configured, "Hardeep", "Zanzmera", "hardeep.zanzmera@workamanvgmail.onmicrosoft.com", None) == "hardeep.zanzmera@workamanvgmail.onmicrosoft.com"


# ---------------------------------------------------------------- global provisioning delay


async def _set_provisioning_delay(client, days):
    response = await client.put("/api/v1/lifecycle/settings", json={"joiner_provisioning_delay_days": days})
    assert response.status_code == 200
    return response


@pytest.mark.asyncio
async def test_a_global_delay_defers_account_creation_until_the_sweep_runs(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await _set_provisioning_delay(client, 5)
        response = await client.post("/api/v1/lifecycle/joiners", json=body(ids, start=future(30)))
    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "PENDING_PROVISIONING" and data["targets"] == [] and data["user_id"] is None
    assert FakeConnector.created == []  # nothing created yet — this is the actual bug the delay fixes

    from app.services.joiner import sweep_pending_provisioning
    async with db_override.factory() as session:
        stored = (await session.scalars(select(JoinerRequest).where(JoinerRequest.id == UUID(data["id"])))).one()
        stored.provision_at = datetime.now(timezone.utc) - timedelta(minutes=1)  # simulate the delay having elapsed
        await session.commit()
        provisioned = await sweep_pending_provisioning(session)
    assert provisioned == 1
    async with db_override.factory() as session:
        stored = (await session.scalars(select(JoinerRequest).where(JoinerRequest.id == UUID(data["id"])))).one()
        assert stored.status == "SCHEDULED" and stored.user_id is not None
        assert sorted(t["provider_name"] for t in stored.targets) == ["Entra", "Okta"]
    assert sorted(name for name, _ in FakeConnector.created) == ["Entra", "Okta"]


@pytest.mark.asyncio
async def test_the_delay_never_pushes_provisioning_past_the_start_date(db_override):
    """A joiner starting soon must still get real accounts right away, exactly as before this feature existed —
    the global delay can only ever make creation earlier-relative-to-start, never later than the start date."""
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await _set_provisioning_delay(client, 30)
        response = await client.post("/api/v1/lifecycle/joiners", json=body(ids, start=future(1)))
    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "SCHEDULED"  # NOT deferred — created immediately despite the 30-day setting
    assert sorted(t["status"] for t in data["targets"]) == ["CREATED", "CREATED"]


@pytest.mark.asyncio
async def test_start_immediately_is_never_affected_by_the_delay(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await _set_provisioning_delay(client, 14)
        response = await client.post("/api/v1/lifecycle/joiners", json=body(ids, start=datetime.now(timezone.utc)))
    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "ACTIVE"  # created AND enabled immediately, exactly as if no delay were configured
    assert sorted(t["status"] for t in data["targets"]) == ["ENABLED", "ENABLED"]


@pytest.mark.asyncio
async def test_cancelling_a_pending_provisioning_joiner_stops_the_sweep_from_touching_it(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await _set_provisioning_delay(client, 5)
        created = await client.post("/api/v1/lifecycle/joiners", json=body(ids, start=future(30)))
        joiner_id = created.json()["id"]
        cancelled = await client.delete(f"/api/v1/lifecycle/joiners/{joiner_id}")
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "CANCELLED"

    from app.services.joiner import sweep_pending_provisioning
    async with db_override.factory() as session:
        stored = (await session.scalars(select(JoinerRequest).where(JoinerRequest.id == UUID(joiner_id)))).one()
        stored.provision_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        await session.commit()
        provisioned = await sweep_pending_provisioning(session)
    assert provisioned == 0 and FakeConnector.created == []


@pytest.mark.asyncio
async def test_sweep_also_activates_immediately_if_the_start_date_has_passed_by_the_time_it_runs(db_override):
    """A short delay relative to a near-term start date: by the time the sweep actually provisions the accounts,
    the start date may already be behind — the sweep must activate in the same pass, not leave it SCHEDULED
    waiting for sweep_joiners to notice on some later tick."""
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await _set_provisioning_delay(client, 2)
        response = await client.post("/api/v1/lifecycle/joiners", json=body(ids, start=future(3)))
    assert response.json()["status"] == "PENDING_PROVISIONING"
    joiner_id = response.json()["id"]

    from app.services.joiner import sweep_pending_provisioning
    async with db_override.factory() as session:
        stored = (await session.scalars(select(JoinerRequest).where(JoinerRequest.id == UUID(joiner_id)))).one()
        stored.provision_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        stored.start_at = datetime.now(timezone.utc) - timedelta(minutes=1)  # the start date has ALSO already passed
        await session.commit()
        await sweep_pending_provisioning(session)
    async with db_override.factory() as session:
        stored = (await session.scalars(select(JoinerRequest).where(JoinerRequest.id == UUID(joiner_id)))).one()
        assert stored.status == "ACTIVE"
