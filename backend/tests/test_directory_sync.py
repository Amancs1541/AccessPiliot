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
    async def activate_assignment(self, request): return True
    async def revoke_assignment(self, assignment): return True
    async def extend_assignment(self, assignment, duration_minutes): return True
    async def sync(self): return {}
    async def create_user(self, request): raise NotImplementedError
    async def create_group(self, request): raise NotImplementedError


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
