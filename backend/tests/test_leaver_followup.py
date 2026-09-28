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
from app.models import AuditLog, IdentityAccount, IdentityProvider, LeaverPolicy, LifecycleSettings, Notification, ReenableRequest, User
from app.providers.graph_client import GraphError
from app.security.auth import AuthenticatedUser, require_authenticated_user
from app.services.leaver_followup import sweep_account_deletions


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
    fail_delete: set = set()

    async def set_user_enabled(self, external_id, enabled):
        FakeConnector.calls.append(("enabled", external_id, enabled))
        return True

    async def delete_user(self, external_id):
        if external_id in FakeConnector.fail_delete:
            raise GraphError("PROVIDER_UNAVAILABLE", "directory down", 503)
        FakeConnector.calls.append(("deleted", external_id))
        return True

    async def remove_group_member(self, group_external_id, user_external_id):
        return True


@pytest.fixture(autouse=True)
def fake_connectors(monkeypatch):
    FakeConnector.calls, FakeConnector.fail_delete = [], set()
    for module in ("accounts", "lifecycle", "privileged_accounts", "leaver_followup"):
        monkeypatch.setattr(f"app.services.{module}._connector", lambda provider: FakeConnector())


async def _seed(session, *, with_manager=True, delete_after_days=None):
    entra = IdentityProvider(name="Entra", type="ENTRA", status="CONNECTED", tenant_id="t1")
    okta = IdentityProvider(name="Okta", type="OKTA", status="CONNECTED", tenant_id="t2")
    session.add_all([entra, okta])
    await session.flush()
    boss = User(provider_id=entra.id, external_id="boss", email="boss@x.com", display_name="Boss", status="ACTIVE")
    owner = User(provider_id=entra.id, external_id="owner", email="owner@x.com", display_name="Owner", status="ACTIVE")
    admin = User(provider_id=entra.id, external_id="admin-oid", email="admin@x.com", display_name="Admin Person", status="ACTIVE")
    person = User(provider_id=entra.id, external_id="ent-1", email="lee@x.com", display_name="Lee Leaver", status="ACTIVE", department="Finance")
    session.add_all([boss, owner, admin, person])
    await session.flush()
    if with_manager:
        person.manager_id = boss.id
    session.add_all([
        IdentityAccount(user_id=person.id, provider_id=entra.id, external_id="ent-1", username="lee@x.com", status="ACTIVE", provisioned_by="SYNC"),
        IdentityAccount(user_id=person.id, provider_id=okta.id, external_id="okta-1", username="lee@okta.x.com", status="ACTIVE", provisioned_by="JOINER"),
        LifecycleSettings(mover_review_enabled=True, review_due_days=14, lifecycle_owner_ids=[str(owner.id)], revoke_on_directory_disable=True),
        LeaverPolicy(name="Default leaver policy", priority=1000, scope_type="ALL", notify_days_before=[7, 1], is_default=True, delete_after_days=delete_after_days),
    ])
    await session.commit()
    return {"person": person.id, "boss": boss.id, "owner": owner.id, "admin": admin.id}


class _Ok:
    status_code = 200


async def _leave(db, person_id):
    """Runs the leaver process to completion (the approval flow has its own tests below)."""
    from app.services.lifecycle import run_leaver
    async with db.factory() as session:
        await run_leaver(session, person_id, "MANUAL", "system:test", "t-leave")
    return _Ok()


@pytest.mark.asyncio
async def test_enabling_after_the_leaver_process_needs_a_request(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        before = await client.post(f"/api/v1/users/{ids['person']}/accounts/enable-all")      # not a leaver yet: allowed
        assert before.status_code == 200
        assert (await _leave(db_override, ids["person"])).status_code == 200
        all_blocked = await client.post(f"/api/v1/users/{ids['person']}/accounts/enable-all")
        accounts = (await client.get(f"/api/v1/users/{ids['person']}/accounts")).json()
        one_blocked = await client.post(f"/api/v1/users/{ids['person']}/accounts/{accounts[0]['id']}/enabled", json={"enabled": True})
        disabling_ok = await client.post(f"/api/v1/users/{ids['person']}/accounts/disable-all")
    assert all_blocked.status_code == 409 and all_blocked.json()["error"]["code"] == "LEAVER_REENABLE_APPROVAL_REQUIRED"
    assert one_blocked.status_code == 409 and disabling_ok.status_code == 200


@pytest.mark.asyncio
async def test_request_needs_a_reason_goes_to_the_manager_and_only_the_approver_can_decide(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await _leave(db_override, ids["person"])
        short = await client.post(f"/api/v1/lifecycle/people/{ids['person']}/reenable-request", json={"reason": "short"})
        created = await client.post(f"/api/v1/lifecycle/people/{ids['person']}/reenable-request", json={"reason": "Returning from parental leave, contract extended."})
        duplicate = await client.post(f"/api/v1/lifecycle/people/{ids['person']}/reenable-request", json={"reason": "Trying a second time now."})
        request_id = created.json()["id"]
        own = await client.post(f"/api/v1/lifecycle/reenable-requests/{request_id}/decision", json={"approve": True})   # the admin who asked cannot approve
        authenticate_as("AccessPilot.User", subject="owner")                                                            # a lifecycle owner is NOT an approver while a manager exists
        stranger = await client.post(f"/api/v1/lifecycle/reenable-requests/{request_id}/decision", json={"approve": True})
        authenticate_as("AccessPilot.User", subject="boss")
        mine = (await client.get("/api/v1/lifecycle/reenable-requests/mine")).json()
        approved = await client.post(f"/api/v1/lifecycle/reenable-requests/{request_id}/decision", json={"approve": True, "note": "Confirmed with HR."})
        again = await client.post(f"/api/v1/lifecycle/reenable-requests/{request_id}/decision", json={"approve": False})
    assert short.status_code == 422 and created.status_code == 201 and duplicate.status_code == 409
    assert created.json()["approvers"] == ["Boss"]
    assert own.status_code == 403 and stranger.status_code == 403
    assert [r["id"] for r in mine] == [request_id]
    assert approved.status_code == 200 and approved.json()["status"] == "APPROVED" and again.status_code == 409
    async with db_override.factory() as session:
        person = await session.get(User, ids["person"])
        accounts = list((await session.scalars(select(IdentityAccount).where(IdentityAccount.user_id == ids["person"]))).all())
        notes = list((await session.scalars(select(Notification).where(Notification.notification_type.in_(("REENABLE_REQUESTED", "REENABLE_DECIDED"))))).all())
    assert person.status == "ACTIVE" and person.leaver_processed_at is None and {a.status for a in accounts} == {"ACTIVE"}
    assert ("enabled", "okta-1", True) in FakeConnector.calls
    assert {n.notification_type for n in notes} == {"REENABLE_REQUESTED", "REENABLE_DECIDED"}


@pytest.mark.asyncio
async def test_rejecting_keeps_the_person_disabled_and_no_manager_falls_back_to_lifecycle_owners(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session, with_manager=False)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await _leave(db_override, ids["person"])
        created = await client.post(f"/api/v1/lifecycle/people/{ids['person']}/reenable-request", json={"reason": "Needs access back for handover."})
        authenticate_as("AccessPilot.User", subject="owner")
        rejected = await client.post(f"/api/v1/lifecycle/reenable-requests/{created.json()['id']}/decision", json={"approve": False, "note": "Not needed."})
    assert created.json()["approvers"] == ["Owner"]
    assert rejected.status_code == 200 and rejected.json()["status"] == "REJECTED"
    async with db_override.factory() as session:
        person = await session.get(User, ids["person"])
    assert person.status == "DISABLED" and person.leaver_processed_at is not None


@pytest.mark.asyncio
async def test_a_leaver_date_cannot_be_edited_after_the_process_ran(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await _leave(db_override, ids["person"])
        edit = await client.patch(f"/api/v1/lifecycle/people/{ids['person']}", json={"clear_leaver_date": True})
        not_a_leaver = await client.post(f"/api/v1/lifecycle/people/{ids['boss']}/reenable-request", json={"reason": "Not actually a leaver at all."})
    assert edit.status_code == 409 and not_a_leaver.status_code == 422


@pytest.mark.asyncio
async def test_policy_delete_after_days_deletes_accounts_from_every_idp_and_labels_the_person_deleted(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session, delete_after_days=30)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await _leave(db_override, ids["person"])
        policies = (await client.get("/api/v1/lifecycle/leaver-policies")).json()
    assert policies[0]["delete_after_days"] == 30
    async with db_override.factory() as session:
        person = await session.get(User, ids["person"])
        assert person.accounts_delete_at is not None and person.accounts_deleted_at is None
        assert await sweep_account_deletions(session) == 0                     # not due yet
        person.accounts_delete_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        await session.commit()
        FakeConnector.fail_delete = {"okta-1"}
        assert await sweep_account_deletions(session) == 0                     # one directory down: nothing is marked deleted
        assert (await session.get(User, ids["person"])).accounts_deleted_at is None
        FakeConnector.fail_delete = set()
        assert await sweep_account_deletions(session) == 1                     # retried, only the remaining account is attempted
        person = await session.get(User, ids["person"])
        accounts = list((await session.scalars(select(IdentityAccount).where(IdentityAccount.user_id == ids["person"]))).all())
        audit = list((await session.scalars(select(AuditLog).where(AuditLog.action == "LEAVER_ACCOUNTS_DELETED"))).all())
    assert person.status == "DELETED" and person.accounts_deleted_at is not None and {a.status for a in accounts} == {"DELETED"}
    assert [c for c in FakeConnector.calls if c[0] == "deleted"] == [("deleted", "ent-1"), ("deleted", "okta-1")] or sorted(c[1] for c in FakeConnector.calls if c[0] == "deleted") == ["ent-1", "okta-1"]
    assert len(audit) == 1 and audit[0].metadata_json["person"]["display_name"] == "Lee Leaver"
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        blocked = await client.post(f"/api/v1/lifecycle/people/{ids['person']}/reenable-request", json={"reason": "Wants to come back later."})
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "ACCOUNTS_DELETED"


@pytest.mark.asyncio
async def test_deletion_waits_while_a_reenable_request_is_pending_and_policy_days_can_be_set_and_cleared(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session, delete_after_days=1)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await _leave(db_override, ids["person"])
        await client.post(f"/api/v1/lifecycle/people/{ids['person']}/reenable-request", json={"reason": "Contract renewed, needs account."})
        created = await client.post("/api/v1/lifecycle/leaver-policies", json={"name": "Contractors", "priority": 10, "scope_type": "EMPLOYMENT_TYPE", "scope_value": "CONTRACTOR", "delete_after_days": 90})
        cleared = await client.patch(f"/api/v1/lifecycle/leaver-policies/{created.json()['id']}", json={"clear_delete_after_days": True})
    assert created.json()["delete_after_days"] == 90 and cleared.json()["delete_after_days"] is None
    async with db_override.factory() as session:
        person = await session.get(User, ids["person"])
        person.accounts_delete_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        await session.commit()
        assert await sweep_account_deletions(session) == 0
        assert not [c for c in FakeConnector.calls if c[0] == "deleted"]


@pytest.mark.asyncio
async def test_the_jml_pdf_report_is_generated_for_admins_only(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session, delete_after_days=30)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await _leave(db_override, ids["person"])
        await client.post(f"/api/v1/lifecycle/people/{ids['person']}/reenable-request", json={"reason": "Returning after leave of absence."})
        report = await client.get("/api/v1/lifecycle/report.pdf")
        authenticate_as("AccessPilot.User", subject="boss")
        denied = await client.get("/api/v1/lifecycle/report.pdf")
    assert report.status_code == 200 and report.headers["content-type"] == "application/pdf"
    assert report.content.startswith(b"%PDF") and len(report.content) > 3000 and "attachment" in report.headers["content-disposition"]
    assert denied.status_code == 403


JUSTIFICATION = {"justification": "Employee resigned, last day agreed with HR."}


@pytest.mark.asyncio
async def test_manual_leaver_flow_justification_then_disable_then_manager_approval_then_leaver_process(db_override):
    from app.models import AccessAssignment, Group, LifecycleEvent
    async with db_override.factory() as session:
        ids = await _seed(session)
        entra = (await session.scalars(select(IdentityProvider).where(IdentityProvider.type == "ENTRA"))).one()
        group = Group(provider_id=entra.id, external_id="g-1", name="Team", status="ACTIVE", is_privileged=False)
        session.add(group)
        await session.flush()
        session.add(AccessAssignment(provider_id=entra.id, user_id=ids["person"], resource_type="GROUP", resource_id=group.id, assignment_type="PERMANENT", status="ELIGIBLE", justification="Access."))
        await session.commit()
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        started = await client.post(f"/api/v1/lifecycle/people/{ids['person']}/leave-now", json=JUSTIFICATION)
        # Step 2 happened already: accounts are disabled, access is NOT revoked yet, nothing is processed.
        assert started.status_code == 201 and started.json()["status"] == "PENDING" and started.json()["approvers"] == ["Boss"]
        assert sorted(c[1] for c in FakeConnector.calls if c[0] == "enabled" and c[2] is False) == ["ent-1", "okta-1"]
        async with db_override.factory() as session:
            mid = await session.get(User, ids["person"])
            pending_assignment = (await session.scalars(select(AccessAssignment).where(AccessAssignment.user_id == ids["person"]))).one()
        assert mid.status == "DISABLED" and mid.leaver_processed_at is None and pending_assignment.status == "ELIGIBLE"
        blocked = await client.post(f"/api/v1/users/{ids['person']}/accounts/enable-all")
        authenticate_as("AccessPilot.User", subject="owner")
        stranger = await client.post(f"/api/v1/lifecycle/leaver-requests/{started.json()['id']}/decision", json={"approve": True})
        authenticate_as("AccessPilot.User", subject="boss")
        mine = (await client.get("/api/v1/lifecycle/leaver-requests/mine")).json()
        approved = await client.post(f"/api/v1/lifecycle/leaver-requests/{started.json()['id']}/decision", json={"approve": True, "note": "Confirmed."})
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "LEAVER_PENDING_APPROVAL"
    assert stranger.status_code == 403 and [r["id"] for r in mine] == [started.json()["id"]]
    assert approved.status_code == 200 and approved.json()["status"] == "APPROVED"
    async with db_override.factory() as session:
        person = await session.get(User, ids["person"])
        assignment = (await session.scalars(select(AccessAssignment).where(AccessAssignment.user_id == ids["person"]))).one()
        events = list((await session.scalars(select(LifecycleEvent).where(LifecycleEvent.event_type == "LEAVER"))).all())
        told = list((await session.scalars(select(Notification).where(Notification.notification_type.in_(("LEAVER_APPROVAL_REQUESTED", "LEAVER_REQUEST_DECIDED"))))).all())
    assert person.leaver_processed_at is not None and person.status == "DISABLED" and assignment.status == "REVOKED"
    assert len(events) == 1 and events[0].source == "MANUAL" and events[0].revoked_count == 1
    assert {n.notification_type for n in told} == {"LEAVER_APPROVAL_REQUESTED", "LEAVER_REQUEST_DECIDED"}


@pytest.mark.asyncio
async def test_a_denied_leaver_request_enables_the_accounts_again_and_notifies_the_admin_who_started_it(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
        # Okta account was ALREADY disabled before the request: a denial must not switch it on.
        okta = (await session.scalars(select(IdentityAccount).where(IdentityAccount.external_id == "okta-1"))).one()
        okta.status = "DISABLED"
        await session.commit()
    authenticate_as("AccessPilot.Admin")                      # subject admin-oid IS a user row, so they can be notified
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        started = await client.post(f"/api/v1/lifecycle/people/{ids['person']}/leave-now", json=JUSTIFICATION)
        own = await client.post(f"/api/v1/lifecycle/leaver-requests/{started.json()['id']}/decision", json={"approve": True})   # the initiator cannot decide
        authenticate_as("AccessPilot.User", subject="boss")
        denied = await client.post(f"/api/v1/lifecycle/leaver-requests/{started.json()['id']}/decision", json={"approve": False, "note": "Still needed until March."})
        authenticate_as("AccessPilot.Admin")
        retry = await client.post(f"/api/v1/lifecycle/people/{ids['person']}/leave-now", json=JUSTIFICATION)   # a new request is possible after a denial
    assert started.status_code == 201 and own.status_code == 403
    assert denied.status_code == 200 and denied.json()["status"] == "DENIED" and "1 account(s) enabled again" in denied.json()["outcome"]
    assert retry.status_code == 201
    assert ("enabled", "ent-1", True) in FakeConnector.calls and ("enabled", "okta-1", True) not in FakeConnector.calls
    async with db_override.factory() as session:
        person = await session.get(User, ids["person"])
        note = (await session.scalars(select(Notification).where(Notification.notification_type == "LEAVER_REQUEST_DECIDED"))).one()
    assert person.leaver_processed_at is None and note.user_id == ids["admin"] and "denied" in note.message and "enabled again" in note.message


@pytest.mark.asyncio
async def test_no_request_is_created_when_no_directory_can_disable_the_account(db_override, monkeypatch):
    from app.models import LeaverRequest

    class Down:
        async def set_user_enabled(self, external_id, enabled):
            raise GraphError("PROVIDER_UNAVAILABLE", "directory down", 503)

    monkeypatch.setattr("app.services.accounts._connector", lambda provider: Down())
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        started = await client.post(f"/api/v1/lifecycle/people/{ids['person']}/leave-now", json=JUSTIFICATION)
    assert started.status_code == 502
    async with db_override.factory() as session:
        assert (await session.scalars(select(LeaverRequest))).first() is None


@pytest.mark.asyncio
async def test_sync_alerts_when_a_processed_leaver_is_enabled_directly_in_the_directory(db_override):
    from app.services.lifecycle import handle_leaver_reactivated, run_leaver
    async with db_override.factory() as session:
        ids = await _seed(session)
        await run_leaver(session, ids["person"], "MANUAL", "system:test", "t")
        change = {"status": {"from": "DISABLED", "to": "ACTIVE"}}
        assert await handle_leaver_reactivated(session, ids["person"], change, "t") is True
        assert await handle_leaver_reactivated(session, ids["person"], {"status": {"from": "ACTIVE", "to": "DISABLED"}}, "t") is False
        assert await handle_leaver_reactivated(session, ids["boss"], change, "t") is False        # never a leaver
        alerts = list((await session.scalars(select(Notification).where(Notification.notification_type == "LIFECYCLE_LEAVER_REACTIVATED"))).all())
        audit = list((await session.scalars(select(AuditLog).where(AuditLog.action == "LEAVER_ACCOUNT_REACTIVATED_EXTERNALLY"))).all())
    assert {a.user_id for a in alerts} == {ids["boss"], ids["owner"]} and len(audit) == 1


@pytest.mark.asyncio
async def test_a_reactivated_leaver_can_be_sent_through_the_process_again(db_override):
    """Reproduces the reported bug: leaver process ran once, then the account was re-enabled directly in the
    directory (bypassing AccessPilot) so status is ACTIVE again but leaver_processed_at is still set from before.
    The manual Start-leaver button, and editing the leaver date, must both work again instead of being stuck."""
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await _leave(db_override, ids["person"])
    async with db_override.factory() as session:
        person = await session.get(User, ids["person"])
        assert person.leaver_processed_at is not None and person.status == "DISABLED"
        person.status = "ACTIVE"        # simulates directory sync mirroring a direct re-enable in Entra
        for account in (await session.scalars(select(IdentityAccount).where(IdentityAccount.user_id == ids["person"]))).all():
            account.status = "ACTIVE"
        await session.commit()
    FakeConnector.calls = []
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        edit = await client.patch(f"/api/v1/lifecycle/people/{ids['person']}", json={"clear_leaver_date": True})
        started = await client.post(f"/api/v1/lifecycle/people/{ids['person']}/leave-now", json=JUSTIFICATION)
    assert edit.status_code == 200
    assert started.status_code == 201 and started.json()["status"] == "PENDING" and started.json()["approvers"] == ["Boss"]
    assert sorted(c[1] for c in FakeConnector.calls if c[0] == "enabled" and c[2] is False) == ["ent-1", "okta-1"]


@pytest.mark.asyncio
async def test_deletion_sweep_never_deletes_a_person_who_is_active_again(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session, delete_after_days=1)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await _leave(db_override, ids["person"])
    async with db_override.factory() as session:
        person = await session.get(User, ids["person"])
        person.status = "ACTIVE"                                            # reactivated outside AccessPilot
        person.accounts_delete_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        await session.commit()
        assert await sweep_account_deletions(session) == 0
        person = await session.get(User, ids["person"])
    assert person.accounts_delete_at is None and person.accounts_deleted_at is None and person.status == "ACTIVE"
    assert not [c for c in FakeConnector.calls if c[0] == "deleted"]


@pytest.mark.asyncio
async def test_leaver_overview_shows_the_pending_request_so_a_second_click_never_looks_like_nothing_happened(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        before = await client.get(f"/api/v1/lifecycle/people/{ids['person']}/leaver-overview")
        started = await client.post(f"/api/v1/lifecycle/people/{ids['person']}/leave-now", json=JUSTIFICATION)
        during = await client.get(f"/api/v1/lifecycle/people/{ids['person']}/leaver-overview")
        again = await client.post(f"/api/v1/lifecycle/people/{ids['person']}/leave-now", json=JUSTIFICATION)          # would otherwise look like a silent no-op
        blocked_enable = await client.post(f"/api/v1/users/{ids['person']}/accounts/enable-all")
        authenticate_as("AccessPilot.User", subject="boss")
        approved = await client.post(f"/api/v1/lifecycle/leaver-requests/{started.json()['id']}/decision", json={"approve": True})
        authenticate_as("AccessPilot.Admin")
        after = await client.get(f"/api/v1/lifecycle/people/{ids['person']}/leaver-overview")
    assert before.json()["pending_leaver_request"] is None and before.json()["recent_events"] == []
    assert during.json()["pending_leaver_request"]["id"] == started.json()["id"] and during.json()["pending_leaver_request"]["justification"] == JUSTIFICATION["justification"]
    assert again.status_code == 409 and again.json()["error"]["code"] == "REQUEST_ALREADY_EXISTS"
    assert blocked_enable.status_code == 409 and blocked_enable.json()["error"]["code"] == "LEAVER_PENDING_APPROVAL"
    assert approved.status_code == 200
    assert after.json()["pending_leaver_request"] is None and len(after.json()["recent_events"]) == 1 and after.json()["recent_events"][0]["source"] == "MANUAL"
    assert after.json()["status"] == "DISABLED"


@pytest.mark.asyncio
async def test_reenable_request_can_be_scoped_to_a_single_account_leaving_the_rest_untouched(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await _leave(db_override, ids["person"])
        accounts = (await client.get(f"/api/v1/users/{ids['person']}/accounts")).json()
        okta_id = next(a["id"] for a in accounts if a["provider_name"] == "Okta")
        entra_id = next(a["id"] for a in accounts if a["provider_name"] == "Entra")
        scoped = await client.post(f"/api/v1/lifecycle/people/{ids['person']}/reenable-request", json={"reason": "Needs Okta back only for now.", "account_ids": [okta_id]})
        authenticate_as("AccessPilot.User", subject="boss")
        approved = await client.post(f"/api/v1/lifecycle/reenable-requests/{scoped.json()['id']}/decision", json={"approve": True})
    assert scoped.status_code == 201 and scoped.json()["scope_label"] == "Okta"
    assert approved.status_code == 200 and approved.json()["accounts_note"] == "Okta: enabled"
    async with db_override.factory() as session:
        okta = await session.get(IdentityAccount, UUID(okta_id))
        entra = await session.get(IdentityAccount, UUID(entra_id))
        person = await session.get(User, ids["person"])
    assert okta.status == "ACTIVE" and entra.status == "DISABLED"                     # only Okta came back
    assert person.leaver_processed_at is not None and person.status == "DISABLED"     # not fully "returned" yet
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        second = await client.post(f"/api/v1/lifecycle/people/{ids['person']}/reenable-request", json={"reason": "Now needs Entra back too.", "account_ids": [entra_id]})
        authenticate_as("AccessPilot.User", subject="boss")
        approved2 = await client.post(f"/api/v1/lifecycle/reenable-requests/{second.json()['id']}/decision", json={"approve": True})
    assert second.json()["scope_label"] == "Entra"
    async with db_override.factory() as session:
        person = await session.get(User, ids["person"])
    assert person.leaver_processed_at is None and person.status == "ACTIVE"           # everything back: fully returned


@pytest.mark.asyncio
async def test_enable_in_all_idps_creates_an_unscoped_all_accounts_request(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await _leave(db_override, ids["person"])
        created = await client.post(f"/api/v1/lifecycle/people/{ids['person']}/reenable-request", json={"reason": "Whole account needs to come back."})
        authenticate_as("AccessPilot.User", subject="boss")
        approved = await client.post(f"/api/v1/lifecycle/reenable-requests/{created.json()['id']}/decision", json={"approve": True})
    assert created.json()["scope_label"] == "All accounts"
    assert approved.status_code == 200
    async with db_override.factory() as session:
        person = await session.get(User, ids["person"])
        accounts = list((await session.scalars(select(IdentityAccount).where(IdentityAccount.user_id == ids["person"]))).all())
    assert person.leaver_processed_at is None and person.status == "ACTIVE" and {a.status for a in accounts} == {"ACTIVE"}


@pytest.mark.asyncio
async def test_admin_can_decide_their_own_request_when_no_manager_or_other_owner_exists(db_override):
    """Reproduces the real stuck case: no manager, and the only configured lifecycle owner is the same admin who
    started the request. Self-approval is blocked when there IS a named approver, but not when the fallback is
    just 'Admins' with nobody else identifiable — otherwise every such request is permanently stuck."""
    async with db_override.factory() as session:
        ids = await _seed(session, with_manager=False)
        settings = (await session.scalars(select(LifecycleSettings))).one()
        settings.lifecycle_owner_ids = [str(ids["admin"])]   # the requester IS the only configured owner
        await session.commit()
    authenticate_as("AccessPilot.Admin", subject="admin-oid")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        started = await client.post(f"/api/v1/lifecycle/people/{ids['person']}/leave-now", json=JUSTIFICATION)
        assert started.json()["approvers"] == []
        listed = (await client.get("/api/v1/lifecycle/leaver-requests")).json()
        assert listed[0]["id"] == started.json()["id"] and listed[0]["can_decide"] is True
        approved = await client.post(f"/api/v1/lifecycle/leaver-requests/{started.json()['id']}/decision", json={"approve": True})
    assert approved.status_code == 200 and approved.json()["status"] == "APPROVED"
    async with db_override.factory() as session:
        person = await session.get(User, ids["person"])
    assert person.leaver_processed_at is not None


@pytest.mark.asyncio
async def test_self_approval_stays_blocked_once_a_named_approver_exists(db_override):
    """Regression: as soon as there IS a real approver (a manager here), the requester still cannot decide their
    own request even if they are an admin — the relaxation above only applies to the no-approver fallback."""
    async with db_override.factory() as session:
        ids = await _seed(session)   # with_manager=True by default
    authenticate_as("AccessPilot.Admin", subject="admin-oid")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        started = await client.post(f"/api/v1/lifecycle/people/{ids['person']}/leave-now", json=JUSTIFICATION)
        assert started.json()["approvers"] == ["Boss"]
        own_attempt = await client.post(f"/api/v1/lifecycle/leaver-requests/{started.json()['id']}/decision", json={"approve": True})
    assert own_attempt.status_code == 403


@pytest.mark.asyncio
async def test_manager_present_or_not_an_admin_can_always_decide_someone_elses_request(db_override):
    """Confirms the existing, unchanged behaviour: an admin who is NOT the requester can decide a request whether
    or not a manager is assigned — this was already true and must stay true."""
    async with db_override.factory() as session:
        ids = await _seed(session)   # has a manager (Boss)
    authenticate_as("AccessPilot.Admin", subject="admin-oid")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        started = await client.post(f"/api/v1/lifecycle/people/{ids['person']}/leave-now", json=JUSTIFICATION)
        authenticate_as("AccessPilot.User", subject="boss")   # Boss is the actual approver here (the manager), not an admin
        decided = await client.post(f"/api/v1/lifecycle/leaver-requests/{started.json()['id']}/decision", json={"approve": True})
    assert decided.status_code == 200


@pytest.mark.asyncio
async def test_reenable_request_same_self_decide_rule_no_approver_vs_named_approver(db_override):
    """Mirrors the leaver-request fix for the re-enable path: self-decide allowed only when nobody else could be
    identified as approver."""
    async with db_override.factory() as session:
        ids = await _seed(session, with_manager=False)
        settings = (await session.scalars(select(LifecycleSettings))).one()
        settings.lifecycle_owner_ids = [str(ids["admin"])]
        await session.commit()
    authenticate_as("AccessPilot.Admin", subject="admin-oid")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await _leave(db_override, ids["person"])
        created = await client.post(f"/api/v1/lifecycle/people/{ids['person']}/reenable-request", json={"reason": "No manager, only owner is me."})
        assert created.json()["approvers"] == []
        listed = (await client.get("/api/v1/lifecycle/reenable-requests")).json()
        assert listed[0]["can_decide"] is True
        approved = await client.post(f"/api/v1/lifecycle/reenable-requests/{created.json()['id']}/decision", json={"approve": True})
    assert approved.status_code == 200 and approved.json()["status"] == "APPROVED"
