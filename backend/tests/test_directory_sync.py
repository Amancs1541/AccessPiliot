from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.models import AccessAssignment, AuditLog, Group, IdentityProvider, Role, SyncError, SyncRun, User, UserGroup
from app.providers.base import IdentityProvider as IdentityProviderProtocol
from app.providers.base import NormalizedGroup, NormalizedRole, NormalizedUser
from app.services.directory_sync import run_sync


class FakeConnector(IdentityProviderProtocol):
    def __init__(self, users, groups, members, roles, fail_members_for: set[str] | None = None):
        self._users, self._groups, self._members, self._roles = users, groups, members, roles
        self._fail_members_for = fail_members_for or set()

    async def test_connection(self): return True
    async def get_users(self, query=None): return self._users
    async def get_user(self, external_id): return next((u for u in self._users if u.external_id == external_id), None)
    async def update_user(self, external_id, *, department, job_title): raise NotImplementedError
    async def set_user_enabled(self, external_id, enabled): return True
    async def get_groups(self, query=None): return self._groups
    async def get_group(self, external_id): return next((g for g in self._groups if g.external_id == external_id), None)
    async def get_group_members(self, external_id):
        if external_id in self._fail_members_for:
            from app.providers.graph_client import GraphError
            raise GraphError("PROVIDER_UNAVAILABLE", "boom", 503)
        return self._members.get(external_id, [])
    async def add_group_member(self, group_external_id, user_external_id): return True
    async def remove_group_member(self, group_external_id, user_external_id): return True
    async def get_roles(self, query=None): return self._roles
    async def get_role(self, external_id): return next((r for r in self._roles if r.external_id == external_id), None)
    async def get_role_assignments(self, external_role_id): return []
    async def get_applications(self, query=None): return []
    async def set_application_enabled(self, external_id, enabled): return True
    async def get_application_permissions(self, external_id): return []
    async def activate_assignment(self, request): return True
    async def revoke_assignment(self, assignment): return True
    async def extend_assignment(self, assignment, duration_minutes): return True
    async def sync(self): return {}
    async def create_user(self, request): raise NotImplementedError
    async def create_group(self, request): raise NotImplementedError
    async def get_domains(self): return []


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        provider = IdentityProvider(name="Entra", type="ENTRA", status="CONNECTED", tenant_id="tenant-1")
        db.add(provider)
        await db.commit()
        await db.refresh(provider)
        yield db, provider
    await engine.dispose()


def connector_fixture():
    users = [NormalizedUser("u1", "u1@x.com", "User One"), NormalizedUser("u2", "u2@x.com", "User Two")]
    groups = [NormalizedGroup("g1", "Group One")]
    members = {"g1": [users[0]]}
    roles = [NormalizedRole("r1", "Global Administrator", is_privileged=True)]
    return users, groups, members, roles


@pytest.mark.asyncio
async def test_sync_populates_users_groups_roles_and_memberships(session, monkeypatch):
    db, provider = session
    users, groups, members, roles = connector_fixture()
    monkeypatch.setattr("app.services.directory_sync._connector", lambda p: FakeConnector(users, groups, members, roles))

    result = await run_sync(db, provider, "req-1")

    assert result.status == "COMPLETED"
    assert result.users_processed == 2 and result.groups_processed == 1 and result.roles_processed == 1
    db_users = (await db.scalars(select(User))).all()
    db_groups = (await db.scalars(select(Group))).all()
    db_roles = (await db.scalars(select(Role))).all()
    memberships = (await db.scalars(select(UserGroup))).all()
    assert len(db_users) == 2 and len(db_groups) == 1 and len(db_roles) == 1
    assert len(memberships) == 1
    audit_actions = {a.action for a in (await db.scalars(select(AuditLog))).all()}
    assert {"SYNC_STARTED", "SYNC_COMPLETED", "USER_SYNCED", "GROUP_SYNCED", "GROUP_MEMBERSHIP_SYNCED", "ROLE_SYNCED"} <= audit_actions


@pytest.mark.asyncio
async def test_sync_is_idempotent_when_run_twice(session, monkeypatch):
    db, provider = session
    users, groups, members, roles = connector_fixture()
    monkeypatch.setattr("app.services.directory_sync._connector", lambda p: FakeConnector(users, groups, members, roles))

    await run_sync(db, provider, "req-1")
    await run_sync(db, provider, "req-2")

    db_users = (await db.scalars(select(User))).all()
    db_groups = (await db.scalars(select(Group))).all()
    memberships = (await db.scalars(select(UserGroup))).all()
    assert len(db_users) == 2
    assert len(db_groups) == 1
    assert len(memberships) == 1


@pytest.mark.asyncio
async def test_a_department_change_picked_up_by_sync_triggers_birthright_reconciliation(session, monkeypatch):
    """The Entra/Okta -> AccessPilot direction: nobody called any birthright endpoint — a plain directory sync
    noticing u1's department changed is what's supposed to trigger the mover grant on its own."""
    from app.models import BirthrightPolicy

    db, provider = session
    users, groups, members, roles = connector_fixture()
    monkeypatch.setattr("app.services.directory_sync._connector", lambda p: FakeConnector(users, groups, members, roles))
    await run_sync(db, provider, "req-1")  # establishes u1 (no department yet) and g1

    group_row = (await db.execute(select(Group).where(Group.external_id == "g1"))).scalar_one()
    db.add(BirthrightPolicy(name="Finance -> Group One", match_field="department", match_value="Finance", resource_type="GROUP", resource_id=group_row.id))
    await db.commit()

    users_with_department = [NormalizedUser("u1", "u1@x.com", "User One", department="Finance"), users[1]]
    monkeypatch.setattr("app.services.directory_sync._connector", lambda p: FakeConnector(users_with_department, groups, members, roles))
    await run_sync(db, provider, "req-2")

    user_row = (await db.execute(select(User).where(User.external_id == "u1"))).scalar_one()
    assert user_row.department == "Finance"
    assignments = (await db.execute(select(AccessAssignment).where(AccessAssignment.user_id == user_row.id))).scalars().all()
    assert len(assignments) == 1
    assert assignments[0].resource_id == group_row.id
    assert assignments[0].status == "ELIGIBLE"
    assert assignments[0].birthright_policy_id is not None


@pytest.mark.asyncio
async def test_joining_and_leaving_a_mapped_group_via_sync_triggers_group_role_mapping_reconciliation(session, monkeypatch):
    """The Entra/Okta -> AccessPilot direction for group-triggered mappings (see
    app.services.group_role_mapping): nobody called any mapping endpoint — a plain directory sync noticing u2
    joined (then later left) a mapped group is what's supposed to trigger the grant/revoke on its own."""
    from app.models import GroupRoleMapping, Role

    db, provider = session
    users, groups, members, roles = connector_fixture()
    monkeypatch.setattr("app.services.directory_sync._connector", lambda p: FakeConnector(users, groups, members, roles))
    await run_sync(db, provider, "req-1")  # establishes g1 with only u1 as a member

    group_row = (await db.execute(select(Group).where(Group.external_id == "g1"))).scalar_one()
    role_row = (await db.execute(select(Role).where(Role.external_id == "r1"))).scalar_one()
    db.add(GroupRoleMapping(source_group_id=group_row.id, resource_type="ROLE", resource_id=role_row.id))
    await db.commit()

    members_with_u2_joined = {"g1": [users[0], users[1]]}
    monkeypatch.setattr("app.services.directory_sync._connector", lambda p: FakeConnector(users, groups, members_with_u2_joined, roles))
    await run_sync(db, provider, "req-2")

    u2_row = (await db.execute(select(User).where(User.external_id == "u2"))).scalar_one()
    assignments = (await db.execute(select(AccessAssignment).where(AccessAssignment.user_id == u2_row.id))).scalars().all()
    assert len(assignments) == 1
    assert assignments[0].resource_id == role_row.id
    assert assignments[0].status == "ELIGIBLE"
    assert assignments[0].group_role_mapping_id is not None

    monkeypatch.setattr("app.services.directory_sync._connector", lambda p: FakeConnector(users, groups, members, roles))  # u2 leaves g1 again
    await run_sync(db, provider, "req-3")

    assignments_after_leaving = (await db.execute(select(AccessAssignment).where(AccessAssignment.user_id == u2_row.id))).scalars().all()
    assert len(assignments_after_leaving) == 1
    assert assignments_after_leaving[0].status == "REVOKED"


@pytest.mark.asyncio
async def test_sync_removes_stale_membership_when_member_leaves(session, monkeypatch):
    db, provider = session
    users, groups, members, roles = connector_fixture()
    monkeypatch.setattr("app.services.directory_sync._connector", lambda p: FakeConnector(users, groups, members, roles))
    await run_sync(db, provider, "req-1")

    members_after_removal = {"g1": []}
    monkeypatch.setattr("app.services.directory_sync._connector", lambda p: FakeConnector(users, groups, members_after_removal, roles))
    await run_sync(db, provider, "req-2")

    memberships = (await db.scalars(select(UserGroup))).all()
    assert len(memberships) == 0


@pytest.mark.asyncio
async def test_sync_revokes_active_assignment_when_member_removed_directly_in_entra(session, monkeypatch):
    """Regression: if AccessPilot granted a user ACTIVE group access, and they're later removed from that group
    directly in Entra (bypassing AccessPilot entirely), the next sync must correct AccessPilot's own record —
    otherwise the assignment stays falsely "ACTIVE" forever, disconnected from the real membership."""
    db, provider = session
    users, groups, members, roles = connector_fixture()
    monkeypatch.setattr("app.services.directory_sync._connector", lambda p: FakeConnector(users, groups, members, roles))
    await run_sync(db, provider, "req-1")

    group_row = (await db.scalars(select(Group))).first()
    member_row = (await db.scalars(select(User).where(User.external_id == "u1"))).first()
    assignment = AccessAssignment(provider_id=provider.id, user_id=member_row.id, resource_type="GROUP", resource_id=group_row.id, assignment_type="PERMANENT", status="ACTIVE")
    db.add(assignment)
    await db.commit()
    assignment_id = assignment.id

    members_after_removal = {"g1": []}
    monkeypatch.setattr("app.services.directory_sync._connector", lambda p: FakeConnector(users, groups, members_after_removal, roles))
    await run_sync(db, provider, "req-2")

    revoked = await db.get(AccessAssignment, assignment_id)
    assert revoked.status == "REVOKED"
    assert revoked.revoked_at is not None
    audit_entry = next(a for a in (await db.scalars(select(AuditLog))).all() if a.action == "ASSIGNMENT_REVOKED")
    assert audit_entry.metadata_json["reason"] == "MEMBERSHIP_REMOVED_OUTSIDE_ACCESSPILOT"


@pytest.mark.asyncio
async def test_sync_records_group_member_error_without_failing_whole_run(session, monkeypatch):
    db, provider = session
    users, groups, members, roles = connector_fixture()
    monkeypatch.setattr("app.services.directory_sync._connector", lambda p: FakeConnector(users, groups, members, roles, fail_members_for={"g1"}))

    result = await run_sync(db, provider, "req-1")

    assert result.status == "COMPLETED"
    assert result.errors_count == 1
    errors = (await db.scalars(select(SyncError))).all()
    assert len(errors) == 1 and errors[0].resource_type == "GROUP_MEMBER"


@pytest.mark.asyncio
async def test_sync_failure_marks_run_failed_and_audits(session, monkeypatch):
    db, provider = session

    class FailingConnector(FakeConnector):
        async def get_users(self, query=None):
            from app.providers.graph_client import GraphError
            raise GraphError("PROVIDER_AUTHENTICATION_FAILED", "no secret", 502)

    monkeypatch.setattr("app.services.directory_sync._connector", lambda p: FailingConnector([], [], {}, []))

    from app.core.errors import AccessPilotError
    with pytest.raises(AccessPilotError) as error:
        await run_sync(db, provider, "req-1")
    assert error.value.code == "PROVIDER_AUTHENTICATION_FAILED"

    runs = (await db.scalars(select(SyncRun))).all()
    assert runs[0].status == "FAILED"
    audit_actions = {a.action for a in (await db.scalars(select(AuditLog))).all()}
    assert "SYNC_FAILED" in audit_actions


@pytest.mark.asyncio
async def test_a_status_or_email_change_now_triggers_reconcile_but_a_name_change_does_not(session, monkeypatch):
    """Rules can read status/email, so a change to either must re-run the reconcile; cosmetic changes must not."""
    from app.models import BirthrightPolicy

    db, provider = session
    users, groups, members, roles = connector_fixture()
    monkeypatch.setattr("app.services.directory_sync._connector", lambda p: FakeConnector(users, groups, members, roles))
    await run_sync(db, provider, "req-1")
    group_row = (await db.execute(select(Group).where(Group.external_id == "g1"))).scalar_one()
    user_row = (await db.execute(select(User).where(User.external_id == "u1"))).scalar_one()
    user_row.department = "Finance"
    db.add(BirthrightPolicy(name="Active finance staff", match_field="department", match_value="Finance", conditions_json=[{"field": "department", "operator": "EQUALS", "value": "Finance"}, {"field": "employmentStatus", "operator": "EQUALS", "value": "ACTIVE"}], conditions_operator="AND", actions_json=[{"resource_type": "GROUP", "resource_id": str(group_row.id), "assignment_type": "PERMANENT"}], resource_type="GROUP", resource_id=group_row.id))
    await db.commit()

    def sync_with(**changes):
        base = {"external_id": "u1", "email": "u1@x.com", "display_name": "User One", "department": "Finance"}
        base.update(changes)
        monkeypatch.setattr("app.services.directory_sync._connector", lambda p: FakeConnector([NormalizedUser(**base), users[1]], groups, members, roles))

    async def held():
        return (await db.execute(select(AccessAssignment).where(AccessAssignment.user_id == user_row.id))).scalars().all()

    # Cosmetic change (display name) — no reconcile, so nothing is granted even though the rule matches.
    sync_with(display_name="User One Renamed")
    await run_sync(db, provider, "req-2")
    assert await held() == []

    # Email change — reconcile runs and the matching rule grants.
    sync_with(email="new.address@x.com")
    await run_sync(db, provider, "req-3")
    rows = await held()
    assert len(rows) == 1 and rows[0].status == "ELIGIBLE"

    # Status flips to DISABLED — the "ACTIVE only" rule stops matching, so its access is revoked, and a disabled
    # account is never handed fresh birthright grants.
    sync_with(email="new.address@x.com", status="DISABLED")
    await run_sync(db, provider, "req-4")
    rows = await held()
    assert len(rows) == 1 and rows[0].status == "REVOKED"


@pytest.mark.asyncio
async def test_a_department_move_seen_by_sync_records_an_event_and_starts_the_leftover_review(session, monkeypatch):
    from app.models import AccessReviewCampaign, LifecycleEvent

    db, provider = session
    users, groups, members, roles = connector_fixture()
    users_sales = [NormalizedUser("u1", "u1@x.com", "User One", department="Sales"), users[1]]
    monkeypatch.setattr("app.services.directory_sync._connector", lambda p: FakeConnector(users_sales, groups, members, roles))
    await run_sync(db, provider, "req-1")

    group_row = (await db.execute(select(Group).where(Group.external_id == "g1"))).scalar_one()
    user_row = (await db.execute(select(User).where(User.external_id == "u1"))).scalar_one()
    boss = User(provider_id=provider.id, external_id="boss", email="boss@x.com", display_name="Boss", status="ACTIVE")
    db.add(boss)
    await db.flush()
    user_row.manager_id = boss.id
    db.add(AccessAssignment(provider_id=provider.id, user_id=user_row.id, resource_type="GROUP", resource_id=group_row.id, assignment_type="PERMANENT", status="ELIGIBLE", justification="Manual grant."))
    await db.commit()

    users_finance = [NormalizedUser("u1", "u1@x.com", "User One", department="Finance"), users[1]]
    monkeypatch.setattr("app.services.directory_sync._connector", lambda p: FakeConnector(users_finance, groups, members, roles))
    await run_sync(db, provider, "req-2")

    event = (await db.execute(select(LifecycleEvent).where(LifecycleEvent.event_type == "MOVER"))).scalars().one()
    assert event.event_type == "MOVER" and event.source == "SYNC" and event.changes["department"] == {"from": "Sales", "to": "Finance"}
    campaign = await db.get(AccessReviewCampaign, event.review_campaign_id)
    assert campaign.scope_type == "MOVER" and campaign.reviewer_id == boss.id


async def _leaver_setup(db, provider, monkeypatch, revoke_on_disable=True):
    from app.models import LifecycleSettings

    users, groups, members, roles = connector_fixture()
    monkeypatch.setattr("app.services.directory_sync._connector", lambda p: FakeConnector(users, groups, members, roles))
    await run_sync(db, provider, "req-1")
    group_row = (await db.execute(select(Group).where(Group.external_id == "g1"))).scalar_one()
    user_row = (await db.execute(select(User).where(User.external_id == "u1"))).scalar_one()
    boss = User(provider_id=provider.id, external_id="boss", email="boss@x.com", display_name="Boss", status="ACTIVE")
    db.add(boss)
    await db.flush()
    user_row.manager_id = boss.id
    db.add(AccessAssignment(provider_id=provider.id, user_id=user_row.id, resource_type="GROUP", resource_id=group_row.id, assignment_type="PERMANENT", status="ELIGIBLE", justification="Manual grant."))
    settings = (await db.execute(select(LifecycleSettings))).scalars().first() or LifecycleSettings(lifecycle_owner_ids=[])
    settings.revoke_on_directory_disable = revoke_on_disable
    db.add(settings)
    await db.commit()
    disabled = [NormalizedUser("u1", "u1@x.com", "User One", status="DISABLED"), users[1]]
    monkeypatch.setattr("app.services.directory_sync._connector", lambda p: FakeConnector(disabled, groups, members, roles))
    await run_sync(db, provider, "req-2")
    return user_row, boss


@pytest.mark.asyncio
async def test_a_person_disabled_in_the_directory_is_treated_as_a_leaver(session, monkeypatch):
    from app.models import LifecycleEvent, Notification

    db, provider = session
    user_row, boss = await _leaver_setup(db, provider, monkeypatch)
    rows = (await db.execute(select(AccessAssignment).where(AccessAssignment.user_id == user_row.id))).scalars().all()
    event = (await db.execute(select(LifecycleEvent).where(LifecycleEvent.event_type == "LEAVER"))).scalars().one()
    notes = (await db.execute(select(Notification).where(Notification.notification_type == "LIFECYCLE_LEAVER"))).scalars().all()
    assert [r.status for r in rows] == ["REVOKED"]
    assert event.source == "SYNC" and event.revoked_count == 1 and event.review_note is None
    assert [n.user_id for n in notes] == [boss.id]


@pytest.mark.asyncio
async def test_with_directory_revocation_switched_off_the_leaver_is_recorded_but_keeps_access(session, monkeypatch):
    from app.models import LifecycleEvent

    db, provider = session
    user_row, _ = await _leaver_setup(db, provider, monkeypatch, revoke_on_disable=False)
    rows = (await db.execute(select(AccessAssignment).where(AccessAssignment.user_id == user_row.id))).scalars().all()
    event = (await db.execute(select(LifecycleEvent).where(LifecycleEvent.event_type == "LEAVER"))).scalars().one()
    assert [r.status for r in rows] == ["ELIGIBLE"] and event.review_note == "AUTO_REVOKE_OFF" and event.revoked_count == 0


@pytest.mark.asyncio
async def test_people_first_seen_by_sync_are_recorded_as_joiners(session, monkeypatch):
    from app.models import LifecycleEvent

    db, provider = session
    users, groups, members, roles = connector_fixture()
    monkeypatch.setattr("app.services.directory_sync._connector", lambda p: FakeConnector(users, groups, members, roles))
    await run_sync(db, provider, "req-1")
    await run_sync(db, provider, "req-2")  # nobody new the second time
    joiners = (await db.execute(select(LifecycleEvent).where(LifecycleEvent.event_type == "JOINER"))).scalars().all()
    assert len(joiners) == len(users) and all(j.source == "SYNC" for j in joiners)
