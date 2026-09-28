from datetime import date, datetime, timedelta, timezone
from itertools import count

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
