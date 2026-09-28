from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import AccessAssignment, Group, IdentityAccount, IdentityProvider, LeaverPolicy, LifecycleEvent, Notification, User, UserGroup
from app.providers.graph_client import GraphError
from app.security.auth import AuthenticatedUser, require_authenticated_user
from app.services.lifecycle import leaver_due_at, resolve_leaver_policy, run_leaver, sweep_leavers


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
    calls: list = []
    fail_for: set = set()

    async def set_user_enabled(self, external_id, enabled):
        if external_id in FakeConnector.fail_for:
            raise GraphError("PROVIDER_UNAVAILABLE", "directory down", 503)
        FakeConnector.calls.append(("enabled", external_id, enabled))
        return True

    async def remove_group_member(self, group_external_id, user_external_id):
        FakeConnector.calls.append(("remove_group", group_external_id, user_external_id))
        return True


@pytest.fixture(autouse=True)
def fake_connectors(monkeypatch):
    FakeConnector.calls, FakeConnector.fail_for = [], set()
    for module in ("accounts", "lifecycle", "privileged_accounts"):
        monkeypatch.setattr(f"app.services.{module}._connector", lambda provider: FakeConnector())


async def _seed(session, *, department="Finance", employment_type=None, leaver_date=None):
    entra = IdentityProvider(name="Entra", type="ENTRA", status="CONNECTED", tenant_id="t1")
    okta = IdentityProvider(name="Okta", type="OKTA", status="CONNECTED", tenant_id="t2")
    session.add_all([entra, okta])
    await session.flush()
    boss = User(provider_id=entra.id, external_id="boss", email="boss@x.com", display_name="Boss", status="ACTIVE")
    person = User(provider_id=entra.id, external_id="ent-1", email="lee@x.com", display_name="Lee Leaver", status="ACTIVE", department=department, employment_type=employment_type, leaver_date=leaver_date)
    session.add_all([boss, person])
    await session.flush()
    person.manager_id = boss.id
    pu = User(provider_id=entra.id, external_id="ent-pu", email="pu@x.com", display_name="PU_Lee", status="ACTIVE", account_type="PU", linked_user_id=person.id)
    group = Group(provider_id=entra.id, external_id="g-1", name="Team", status="ACTIVE", is_privileged=False)
    session.add_all([pu, group])
    await session.flush()
    session.add_all([
        IdentityAccount(user_id=person.id, provider_id=entra.id, external_id="ent-1", username="lee@x.com", status="ACTIVE", provisioned_by="SYNC"),
        IdentityAccount(user_id=person.id, provider_id=okta.id, external_id="okta-1", username="lee@okta.x.com", status="ACTIVE", provisioned_by="JOINER"),
        AccessAssignment(provider_id=entra.id, user_id=person.id, resource_type="GROUP", resource_id=group.id, assignment_type="PERMANENT", status="ELIGIBLE", justification="Access."),
        UserGroup(user_id=person.id, group_id=group.id, source="SYNC"),
    ])
    await session.commit()
    return {"person": person.id, "boss": boss.id, "pu": pu.id, "entra": entra.id, "okta": okta.id, "group": group.id}


def test_due_time_uses_the_policy_time_in_the_app_timezone():
    # 23:59 Berlin on 15 Jan (UTC+1) is 22:59 UTC; in summer (UTC+2) it is 21:59 UTC.
    assert leaver_due_at(date(2027, 1, 15), "23:59", "Europe/Berlin") == datetime(2027, 1, 15, 22, 59, tzinfo=timezone.utc)
    assert leaver_due_at(date(2027, 6, 15), "23:59", "Europe/Berlin") == datetime(2027, 6, 15, 21, 59, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_the_first_matching_scoped_policy_wins_else_the_default(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session, department="Finance", employment_type="CONTRACTOR")
        session.add_all([
            LeaverPolicy(name="Contractors", priority=10, scope_type="EMPLOYMENT_TYPE", scope_value="CONTRACTOR"),
            LeaverPolicy(name="Finance staff", priority=5, scope_type="DEPARTMENT", scope_value="finance"),
            LeaverPolicy(name="Disabled one", priority=1, scope_type="ALL", status="DISABLED"),
        ])
        await session.commit()
        person = await session.get(User, ids["person"])
        assert (await resolve_leaver_policy(session, person)).name == "Finance staff"  # lower priority number, case-insensitive
        person.department = "Legal"
        assert (await resolve_leaver_policy(session, person)).name == "Contractors"
        person.employment_type = "EMPLOYEE"
        assert (await resolve_leaver_policy(session, person)).is_default is True  # nothing scoped matches, disabled ones ignored


@pytest.mark.asyncio
async def test_the_leaver_runner_revokes_access_and_disables_every_idp_and_linked_accounts(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
        event = await run_leaver(session, ids["person"], "MANUAL", "admin-oid", "req-1")
        assignments = (await session.scalars(select(AccessAssignment).where(AccessAssignment.user_id == ids["person"]))).all()
        accounts = {a.external_id: a.status for a in (await session.scalars(select(IdentityAccount).where(IdentityAccount.user_id == ids["person"]))).all()}
        person, pu = await session.get(User, ids["person"]), await session.get(User, ids["pu"])
        notes = (await session.scalars(select(Notification).where(Notification.notification_type == "LIFECYCLE_LEAVER"))).all()
    assert [a.status for a in assignments] == ["REVOKED"] and event.revoked_count == 1
    assert accounts == {"ent-1": "DISABLED", "okta-1": "DISABLED"}
    assert ("enabled", "ent-1", False) in FakeConnector.calls and ("enabled", "okta-1", False) in FakeConnector.calls and ("enabled", "ent-pu", False) in FakeConnector.calls
    assert person.status == "DISABLED" and person.leaver_processed_at is not None and pu.status == "DISABLED"
    assert event.event_type == "LEAVER" and event.source == "MANUAL" and event.changes["policy"] == "Default leaver policy" and event.review_note is None
    assert [n.user_id for n in notes] == [ids["boss"]]


@pytest.mark.asyncio
async def test_policy_switches_are_respected_and_group_memberships_can_be_removed(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session, department="Legal")
        session.add(LeaverPolicy(name="Legal keeps access", priority=1, scope_type="DEPARTMENT", scope_value="Legal", revoke_access=False, disable_accounts=False, disable_privileged_accounts=False, remove_group_memberships=True))
        await session.commit()
        await run_leaver(session, ids["person"], "MANUAL", "admin-oid", "req-2")
        assignments = (await session.scalars(select(AccessAssignment).where(AccessAssignment.user_id == ids["person"]))).all()
        memberships = (await session.scalars(select(UserGroup).where(UserGroup.user_id == ids["person"]))).all()
        pu = await session.get(User, ids["pu"])
    assert [a.status for a in assignments] == ["ELIGIBLE"] and pu.status == "ACTIVE"          # nothing revoked/disabled
    assert not any(c[0] == "enabled" for c in FakeConnector.calls)                                # no account touched
    assert memberships == [] and ("remove_group", "g-1", "ent-1") in FakeConnector.calls        # but group membership removed


@pytest.mark.asyncio
async def test_one_idp_failing_is_recorded_and_the_others_still_disabled(db_override):
    FakeConnector.fail_for = {"okta-1"}
    async with db_override.factory() as session:
        ids = await _seed(session)
        event = await run_leaver(session, ids["person"], "MANUAL", "admin-oid", "req-3")
        accounts = {a.external_id: a.status for a in (await session.scalars(select(IdentityAccount).where(IdentityAccount.user_id == ids["person"]))).all()}
    assert accounts == {"ent-1": "DISABLED", "okta-1": "ACTIVE"}
    assert event.review_note == "ACCOUNT_DISABLE_FAILED" and any(r["provider"] == "Okta" and not r["ok"] for r in event.changes["accounts"])


@pytest.mark.asyncio
async def test_a_sync_detected_leaver_skips_the_directory_that_reported_it(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
        await run_leaver(session, ids["person"], "SYNC", "system:lifecycle", "req-4", skip_provider_ids={ids["entra"]})
    assert ("enabled", "ent-1", False) not in FakeConnector.calls and ("enabled", "okta-1", False) in FakeConnector.calls


@pytest.mark.asyncio
async def test_the_worker_runs_due_leavers_once_reminds_once_and_ignores_future_ones(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session, leaver_date=date.today() - timedelta(days=1))
        assert await sweep_leavers(session) == 1
        assert await sweep_leavers(session) == 0                       # already processed
        person = await session.get(User, ids["person"])
        assert person.leaver_processed_at is not None
        # A second person leaving in 7 days gets exactly one reminder, and is not processed.
        other = User(provider_id=ids["entra"], external_id="other", email="o@x.com", display_name="Oscar Other", status="ACTIVE", manager_id=ids["boss"])
        session.add(other)
        await session.flush()
        from zoneinfo import ZoneInfo
        from app.models import SecuritySettings
        tz = (await session.scalars(select(SecuritySettings.timezone))).first() or "Europe/Berlin"
        other.leaver_date = datetime.now(ZoneInfo(tz)).date() + timedelta(days=7)
        await session.commit()
        assert await sweep_leavers(session) == 0
        assert await sweep_leavers(session) == 0
        reminders = (await session.scalars(select(Notification).where(Notification.notification_type == "LIFECYCLE_LEAVER_REMINDER"))).all()
        await session.refresh(other)
    assert len(reminders) == 1 and other.leaver_reminders_sent == [7] and other.leaver_processed_at is None


@pytest.mark.asyncio
async def test_policy_crud_validation_and_the_default_is_protected(db_override):
    async with db_override.factory() as session:
        await _seed(session)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        listed = (await client.get("/api/v1/lifecycle/leaver-policies")).json()
        default = next(p for p in listed if p["is_default"])
        created = await client.post("/api/v1/lifecycle/leaver-policies", json={"name": "Contractors", "priority": 10, "scope_type": "EMPLOYMENT_TYPE", "scope_value": "contractor", "effective_time": "17:30", "notify_days_before": [1, 7, 7, 400], "disable_privileged_accounts": False})
        no_value = await client.post("/api/v1/lifecycle/leaver-policies", json={"name": "Bad", "scope_type": "DEPARTMENT"})
        bad_type = await client.post("/api/v1/lifecycle/leaver-policies", json={"name": "Bad2", "scope_type": "EMPLOYMENT_TYPE", "scope_value": "WIZARD"})
        dup = await client.post("/api/v1/lifecycle/leaver-policies", json={"name": "Contractors"})
        edited = await client.patch(f"/api/v1/lifecycle/leaver-policies/{created.json()['id']}", json={"effective_time": "09:00", "status": "DISABLED"})
        del_default = await client.delete(f"/api/v1/lifecycle/leaver-policies/{default['id']}")
        rescope_default = await client.patch(f"/api/v1/lifecycle/leaver-policies/{default['id']}", json={"scope_type": "DEPARTMENT", "scope_value": "X"})
        deleted = await client.delete(f"/api/v1/lifecycle/leaver-policies/{created.json()['id']}")
        authenticate_as("AccessPilot.User", subject="regular")
        denied = await client.get("/api/v1/lifecycle/leaver-policies")
    assert default["name"] == "Default leaver policy" and default["scope_type"] == "ALL"
    assert created.status_code == 201 and created.json()["scope_value"] == "CONTRACTOR" and created.json()["notify_days_before"] == [7, 1]
    assert no_value.status_code == 422 and bad_type.status_code == 422 and dup.status_code == 409
    assert edited.json()["effective_time"] == "09:00" and edited.json()["status"] == "DISABLED"
    assert del_default.status_code == 409 and rescope_default.status_code == 422 and deleted.status_code == 204 and denied.status_code == 403


@pytest.mark.asyncio
async def test_setting_a_leaver_date_listing_it_and_starting_the_process_now(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    future_day = (date.today() + timedelta(days=30)).isoformat()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        set_date = await client.patch(f"/api/v1/lifecycle/people/{ids['person']}", json={"leaver_date": future_day, "employment_type": "CONTRACTOR"})
        scheduled = (await client.get("/api/v1/lifecycle/leavers/scheduled")).json()
        privileged = await client.patch(f"/api/v1/lifecycle/people/{ids['pu']}", json={"leaver_date": future_day})
        no_reason = await client.post(f"/api/v1/lifecycle/people/{ids['person']}/leave-now", json={"justification": "short"})
        now = await client.post(f"/api/v1/lifecycle/people/{ids['person']}/leave-now", json={"justification": "Employee resigned, last day agreed."})
        again = await client.post(f"/api/v1/lifecycle/people/{ids['person']}/leave-now", json={"justification": "Employee resigned, last day agreed."})
        pu_now = await client.post(f"/api/v1/lifecycle/people/{ids['pu']}/leave-now", json={"justification": "Employee resigned, last day agreed."})
        authenticate_as("AccessPilot.User", subject="regular")
        denied = await client.post(f"/api/v1/lifecycle/people/{ids['person']}/leave-now", json={"justification": "Employee resigned, last day agreed."})
    assert set_date.json()["leaver_date"] == future_day and set_date.json()["policy_name"] == "Default leaver policy"
    assert [s["user_display_name"] for s in scheduled] == ["Lee Leaver"] and scheduled[0]["status"] == "SCHEDULED"
    assert privileged.status_code == 422
    assert no_reason.status_code == 422                                                  # a justification is required
    assert now.status_code == 201 and now.json()["status"] == "PENDING" and now.json()["approvers"] == ["Boss"]
    assert again.status_code == 409 and pu_now.status_code == 422 and denied.status_code == 403


@pytest.mark.asyncio
async def test_clearing_the_leaver_date_cancels_it(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session, leaver_date=date.today() + timedelta(days=10))
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        cleared = await client.patch(f"/api/v1/lifecycle/people/{ids['person']}", json={"clear_leaver_date": True})
        scheduled = (await client.get("/api/v1/lifecycle/leavers/scheduled")).json()
    assert cleared.json()["leaver_date"] is None and scheduled == []


@pytest.mark.asyncio
async def test_a_csv_termination_now_disables_the_real_account_and_a_csv_leaver_date_is_stored(db_override):
    async with db_override.factory() as session:
        session.add(IdentityProvider(name="Directory", type="MOCK", status="CONNECTED", tenant_id="t"))
        await session.commit()
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        header = "employeeId,firstName,lastName,email,department,jobTitle,status,leaverDate\n"
        bad = await client.post("/api/v1/onboarding/csv", json={"filename": "bad.csv", "content": header + "EMP7001,Cy,Leaver,cy@company.com,Finance,Analyst,ACTIVE,31/12/2030\n"})
        joined = await client.post("/api/v1/onboarding/csv", json={"filename": "j.csv", "content": header + "EMP7001,Cy,Leaver,cy@company.com,Finance,Analyst,ACTIVE,2030-12-31\n"})
        await client.post(f"/api/v1/onboarding/imports/{joined.json()['id']}/commit")
        async with db_override.factory() as session:
            person = (await session.execute(select(User).where(User.employee_id == "EMP7001"))).scalar_one()
            stored = person.leaver_date
        FakeConnector.calls = []
        left = await client.post("/api/v1/onboarding/csv", json={"filename": "l.csv", "content": header + "EMP7001,Cy,Leaver,cy@company.com,Finance,Analyst,TERMINATED,\n"})
        await client.post(f"/api/v1/onboarding/imports/{left.json()['id']}/commit")
    assert bad.json()["failed_count"] == 1 and stored == date(2030, 12, 31)
    assert any(c[0] == "enabled" and c[2] is False for c in FakeConnector.calls)   # the REAL account was disabled, not just the local flag
    async with db_override.factory() as session:
        events = (await session.scalars(select(LifecycleEvent).where(LifecycleEvent.event_type == "LEAVER"))).all()
    assert [(e.source) for e in events] == ["CSV"]


@pytest.mark.asyncio
async def test_start_leaver_now_works_for_an_already_disabled_person(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
        person = await session.get(User, ids["person"])
        person.status = "DISABLED"     # e.g. a joiner still waiting for a start date
        await session.commit()
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post(f"/api/v1/lifecycle/people/{ids['person']}/leave-now", json={"justification": "Employee resigned, last day agreed."})
        approved = await client.post(f"/api/v1/lifecycle/leaver-requests/{first.json()['id']}/decision", json={"approve": True})
        again = await client.post(f"/api/v1/lifecycle/people/{ids['person']}/leave-now", json={"justification": "Employee resigned, last day agreed."})
    assert first.status_code == 201 and approved.status_code == 200 and again.status_code == 409
