from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import AccessAssignment, AccessReviewCampaign, AccessReviewItem, BirthrightPolicy, Group, IdentityProvider, LifecycleEvent, User
from app.security.auth import AuthenticatedUser, require_authenticated_user
from app.services.lifecycle import build_changes, get_lifecycle_settings, record_mover


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


MOVE = {"department": {"from": "Sales", "to": "Finance"}}


async def _seed(session, *, manager=True, owners=0, manual=True, policy_granted=True):
    provider = IdentityProvider(name="Directory", type="MOCK", status="CONNECTED", tenant_id="t")
    session.add(provider)
    await session.flush()
    boss = User(provider_id=provider.id, external_id="boss", email="boss@x.com", display_name="Boss Person", status="ACTIVE")
    mover = User(provider_id=provider.id, external_id="mover", email="mover@x.com", display_name="Mo Ver", status="ACTIVE", department="Finance")
    owner_users = [User(provider_id=provider.id, external_id=f"owner{i}", email=f"owner{i}@x.com", display_name=f"Owner {i}", status="ACTIVE") for i in range(owners)]
    session.add_all([boss, mover, *owner_users])
    await session.flush()
    if manager:
        mover.manager_id = boss.id
    g_manual = Group(provider_id=provider.id, external_id="g-manual", name="Manual Group", status="ACTIVE", is_privileged=False)
    g_policy = Group(provider_id=provider.id, external_id="g-policy", name="Policy Group", status="ACTIVE", is_privileged=False)
    session.add_all([g_manual, g_policy])
    await session.flush()
    policy = BirthrightPolicy(name="P", match_field="department", match_value="Finance", resource_type="GROUP", resource_id=g_policy.id)
    session.add(policy)
    await session.flush()
    if manual:
        session.add(AccessAssignment(provider_id=provider.id, user_id=mover.id, resource_type="GROUP", resource_id=g_manual.id, assignment_type="PERMANENT", status="ELIGIBLE", justification="Manual."))
    if policy_granted:
        session.add(AccessAssignment(provider_id=provider.id, user_id=mover.id, resource_type="GROUP", resource_id=g_policy.id, assignment_type="PERMANENT", status="ELIGIBLE", justification="Birthright policy: P", birthright_policy_id=policy.id))
    await session.commit()
    return {"boss": boss.id, "mover": mover.id, "owners": [o.id for o in owner_users], "provider": provider.id, "manual_group": g_manual.id}


@pytest.mark.asyncio
async def test_a_mover_gets_a_review_of_only_their_non_policy_access_sent_to_their_manager(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
        event = await record_mover(session, ids["mover"], MOVE, "SYNC", {"revoked": [uuid4()], "granted": []}, "req-1")
        assert event is not None and event.review_note is None and event.revoked_count == 1
        campaign = await session.get(AccessReviewCampaign, event.review_campaign_id)
        items = (await session.scalars(select(AccessReviewItem).where(AccessReviewItem.campaign_id == campaign.id))).all()
    assert campaign.scope_type == "MOVER" and campaign.reviewer_id == ids["boss"] and campaign.status == "ACTIVE"
    assert len(items) == 1  # the manual grant only; the birthright-policy grant is excluded
    assert "Sales → Finance" in campaign.name


@pytest.mark.asyncio
async def test_without_a_manager_the_first_lifecycle_owner_reviews_and_the_next_is_fallback(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session, manager=False, owners=2)
        settings = await get_lifecycle_settings(session)
        settings.lifecycle_owner_ids = [str(o) for o in ids["owners"]]
        await session.commit()
        event = await record_mover(session, ids["mover"], MOVE, "CSV", None, "req-2")
        campaign = await session.get(AccessReviewCampaign, event.review_campaign_id)
    assert campaign.reviewer_id == ids["owners"][0] and campaign.fallback_reviewer_id == ids["owners"][1]


@pytest.mark.asyncio
async def test_no_manager_and_no_owners_records_the_event_but_starts_no_review(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session, manager=False)
        event = await record_mover(session, ids["mover"], MOVE, "ADMIN_EDIT", None, "req-3")
        campaigns = (await session.scalars(select(AccessReviewCampaign))).all()
    assert event.review_campaign_id is None and event.review_note == "NO_REVIEWER" and campaigns == []


@pytest.mark.asyncio
async def test_nothing_left_to_review_or_review_disabled_or_already_open(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session, manual=False)
        none_left = await record_mover(session, ids["mover"], MOVE, "SYNC", None, "req-4")
        assert none_left.review_note == "NO_LEFTOVER_ACCESS" and none_left.review_campaign_id is None
        session.add(AccessAssignment(provider_id=ids["provider"], user_id=ids["mover"], resource_type="GROUP", resource_id=ids["manual_group"], assignment_type="PERMANENT", status="ELIGIBLE", justification="Manual."))
        await session.commit()
        first = await record_mover(session, ids["mover"], MOVE, "SYNC", None, "req-5")
        assert first.review_campaign_id is not None
        again = await record_mover(session, ids["mover"], {"job_title": {"from": "A", "to": "B"}}, "SYNC", None, "req-6")
        assert again.review_note == "REVIEW_ALREADY_OPEN"
        settings = await get_lifecycle_settings(session)
        settings.mover_review_enabled = False
        await session.commit()
        off = await record_mover(session, ids["mover"], MOVE, "SYNC", None, "req-7")
    assert off.review_note == "REVIEW_DISABLED" and off.review_campaign_id is None


@pytest.mark.asyncio
async def test_only_a_real_department_or_title_move_counts_as_a_mover(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
        assert await record_mover(session, ids["mover"], {"department": {"from": None, "to": "Finance"}}, "SYNC", None, "r") is None  # first-time fill
        assert await record_mover(session, ids["mover"], {"email": {"from": "a@x.com", "to": "b@x.com"}}, "SYNC", None, "r") is None
        assert await record_mover(session, ids["mover"], {}, "SYNC", None, "r") is None
        user = await session.get(User, ids["mover"])
        user.status = "DISABLED"
        await session.commit()
        assert await record_mover(session, ids["mover"], MOVE, "SYNC", None, "r") is None  # disabled account
        assert (await session.scalars(select(LifecycleEvent))).all() == []
    assert build_changes({"department": "A", "email": "X@x.com"}, {"department": "A", "email": "x@x.com"}) == {}


@pytest.mark.asyncio
async def test_lifecycle_settings_endpoints_are_admin_only_and_validate(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session, owners=1)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        authenticate_as("AccessPilot.Admin")
        default = await client.get("/api/v1/lifecycle/settings")
        updated = await client.put("/api/v1/lifecycle/settings", json={"review_due_days": 30, "lifecycle_owner_ids": [str(ids["owners"][0])]})
        bad = await client.put("/api/v1/lifecycle/settings", json={"lifecycle_owner_ids": [str(uuid4())]})
        authenticate_as("AccessPilot.User", subject="regular")
        denied = await client.get("/api/v1/lifecycle/settings")
    assert default.json()["review_due_days"] == 14 and default.json()["mover_review_enabled"] is True
    assert default.json()["revoke_on_directory_disable"] is True
    assert updated.json()["review_due_days"] == 30 and updated.json()["lifecycle_owners"][0]["display_name"] == "Owner 0"
    assert bad.status_code == 404
    assert denied.status_code == 403


@pytest.mark.asyncio
async def test_manager_and_lifecycle_owners_are_notified_once_and_the_mover_never_is(db_override):
    from app.models import Notification

    async with db_override.factory() as session:
        ids = await _seed(session, owners=2)
        settings = await get_lifecycle_settings(session)
        # The manager is also listed as an owner (must be de-duplicated) and so is the mover (must be excluded).
        settings.lifecycle_owner_ids = [str(ids["boss"]), str(ids["owners"][0]), str(ids["mover"])]
        await session.commit()
        event = await record_mover(session, ids["mover"], MOVE, "SYNC", {"revoked": [uuid4(), uuid4()], "granted": [uuid4()]}, "req-n")
        notes = (await session.scalars(select(Notification).where(Notification.notification_type == "LIFECYCLE_MOVER"))).all()
    assert sorted(str(n.user_id) for n in notes) == sorted([str(ids["boss"]), str(ids["owners"][0])])
    assert set(event.notified_user_ids) == {str(ids["boss"]), str(ids["owners"][0])}
    assert "2 access removed, 1 new eligible" in notes[0].message and notes[0].link == "/admin/movers"


@pytest.mark.asyncio
async def test_movers_report_lists_events_with_review_progress_and_is_permission_gated(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
        await record_mover(session, ids["mover"], MOVE, "CSV", {"revoked": [uuid4()], "granted": []}, "req-r")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        authenticate_as("AccessPilot.Admin")
        report = await client.get("/api/v1/lifecycle/events?event_type=MOVER")
        other = await client.get("/api/v1/lifecycle/events?event_type=LEAVER")
        authenticate_as("AccessPilot.User", subject="regular")
        denied = await client.get("/api/v1/lifecycle/events")
    row = report.json()[0]
    assert row["user_display_name"] == "Mo Ver" and row["source"] == "CSV" and row["revoked_count"] == 1
    assert row["changes"]["department"] == {"from": "Sales", "to": "Finance"}
    assert row["review_status"] == "ACTIVE" and row["review_item_count"] == 1 and row["review_decided_count"] == 0
    assert row["review_reviewer_name"] == "Boss Person" and row["notified"] == ["Boss Person"]
    assert other.json() == []
    assert denied.status_code == 403


@pytest.mark.asyncio
async def test_a_movers_linked_privileged_and_test_accounts_are_flagged_into_the_review_but_never_disabled(db_override):
    from app.models import Notification

    async with db_override.factory() as session:
        ids = await _seed(session, manual=False)  # the mover has NO leftover access of their own
        pu = User(provider_id=ids["provider"], external_id="pu-mover", email="pu@x.com", display_name="PU_Mo Ver", status="ACTIVE", account_type="PU", linked_user_id=ids["mover"])
        tu = User(provider_id=ids["provider"], external_id="tu-mover", email="tu@x.com", display_name="TU_Mo Ver", status="ACTIVE", account_type="TU", linked_user_id=ids["mover"])
        stranger = User(provider_id=ids["provider"], external_id="pu-other", email="pu2@x.com", display_name="PU_Someone Else", status="ACTIVE", account_type="PU", linked_user_id=ids["boss"])
        session.add_all([pu, tu, stranger])
        await session.flush()
        for account in (pu, tu, stranger):
            session.add(AccessAssignment(provider_id=ids["provider"], user_id=account.id, resource_type="GROUP", resource_id=ids["manual_group"], assignment_type="PERMANENT", status="ACTIVE", justification="Privileged grant."))
        await session.commit()
        event = await record_mover(session, ids["mover"], MOVE, "SYNC", None, "req-pu")
        items = (await session.scalars(select(AccessReviewItem).where(AccessReviewItem.campaign_id == event.review_campaign_id))).all()
        notes = (await session.scalars(select(Notification).where(Notification.notification_type == "LIFECYCLE_MOVER"))).all()
        await session.refresh(pu)
        await session.refresh(tu)
    assert event.review_note is None and event.privileged_flagged_count == 2
    assert {item.user_id for item in items} == {pu.id, tu.id}  # not the stranger's PU account
    assert pu.status == "ACTIVE" and tu.status == "ACTIVE"  # flagged, not disabled
    assert any("linked privileged/test accounts" in n.message for n in notes)


def _future_iso(days=3):
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


@pytest.mark.asyncio
async def test_scheduling_a_move_validates_replaces_and_can_be_cancelled(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
        pu = User(provider_id=ids["provider"], external_id="pu-x", email="pux@x.com", display_name="PU_X", status="ACTIVE", account_type="PU", linked_user_id=ids["mover"])
        session.add(pu)
        await session.commit()
        pu_id = pu.id
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        authenticate_as("AccessPilot.Admin")
        first = await client.post("/api/v1/lifecycle/moves", json={"user_id": str(ids["mover"]), "department": "Legal", "effective_at": _future_iso(3)})
        second = await client.post("/api/v1/lifecycle/moves", json={"user_id": str(ids["mover"]), "department": "Marketing", "job_title": "Lead", "effective_at": _future_iso(5)})
        past = await client.post("/api/v1/lifecycle/moves", json={"user_id": str(ids["mover"]), "department": "Legal", "effective_at": "2020-01-01T00:00:00+00:00"})
        empty = await client.post("/api/v1/lifecycle/moves", json={"user_id": str(ids["mover"]), "effective_at": _future_iso(3)})
        privileged = await client.post("/api/v1/lifecycle/moves", json={"user_id": str(pu_id), "department": "Legal", "effective_at": _future_iso(3)})
        listed = (await client.get("/api/v1/lifecycle/moves")).json()
        cancelled = await client.delete(f"/api/v1/lifecycle/moves/{second.json()['id']}")
        again = await client.delete(f"/api/v1/lifecycle/moves/{second.json()['id']}")
        authenticate_as("AccessPilot.User", subject="regular")
        denied = await client.post("/api/v1/lifecycle/moves", json={"user_id": str(ids["mover"]), "department": "Legal", "effective_at": _future_iso(3)})
    assert first.status_code == 201 and second.status_code == 201
    assert past.status_code == 422 and empty.status_code == 422 and privileged.status_code == 422
    assert {m["id"]: m["status"] for m in listed}[first.json()["id"]] == "CANCELLED"  # superseded by the newer schedule
    assert {m["id"]: m["status"] for m in listed}[second.json()["id"]] == "SCHEDULED"
    assert cancelled.json()["status"] == "CANCELLED" and again.status_code == 409 and denied.status_code == 403


@pytest.mark.asyncio
async def test_a_due_scheduled_move_is_applied_as_one_change_but_a_future_one_is_left_alone(db_override, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from app.models import PendingMove
    from app.services.lifecycle import sweep_pending_moves

    pushed = []

    class FakeConnector:
        async def update_user(self, external_id, *, department, job_title):
            pushed.append((external_id, department, job_title))

    monkeypatch.setattr("app.services.lifecycle._connector", lambda provider: FakeConnector())
    async with db_override.factory() as session:
        ids = await _seed(session)
        due = PendingMove(user_id=ids["mover"], new_department="Marketing", new_job_title=None, effective_at=datetime.now(timezone.utc) - timedelta(minutes=1), source="CSV")
        later = PendingMove(user_id=ids["boss"], new_department="Legal", effective_at=datetime.now(timezone.utc) + timedelta(days=2), source="ADMIN_EDIT")
        session.add_all([due, later])
        await session.commit()
        assert await sweep_pending_moves(session) == 1
        mover = await session.get(User, ids["mover"])
        boss = await session.get(User, ids["boss"])
        await session.refresh(due)
        await session.refresh(later)
        event = (await session.scalars(select(LifecycleEvent))).one()
    assert mover.department == "Marketing" and boss.department is None      # only the due one changed
    assert pushed == [("mover", "Marketing", None)]                         # pushed to the provider first
    assert due.status == "APPLIED" and due.lifecycle_event_id == event.id and later.status == "SCHEDULED"
    assert event.source == "CSV" and event.changes["department"] == {"from": "Finance", "to": "Marketing"}
    assert event.review_campaign_id is not None                             # the leftover-access review started too


@pytest.mark.asyncio
async def test_a_provider_failure_marks_the_move_failed_and_changes_nothing(db_override, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from app.models import PendingMove
    from app.providers.graph_client import GraphError
    from app.services.lifecycle import sweep_pending_moves

    class Broken:
        async def update_user(self, *args, **kwargs):
            raise GraphError("PROVIDER_UNAVAILABLE", "boom", 503)

    monkeypatch.setattr("app.services.lifecycle._connector", lambda provider: Broken())
    async with db_override.factory() as session:
        ids = await _seed(session)
        move = PendingMove(user_id=ids["mover"], new_department="Marketing", effective_at=datetime.now(timezone.utc) - timedelta(minutes=1), source="ADMIN_EDIT")
        session.add(move)
        await session.commit()
        assert await sweep_pending_moves(session) == 0
        await session.refresh(move)
        mover = await session.get(User, ids["mover"])
    assert move.status == "FAILED" and "PROVIDER_UNAVAILABLE" in move.failure_reason and mover.department == "Finance"
