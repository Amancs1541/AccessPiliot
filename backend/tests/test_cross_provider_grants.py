"""Regression + new-behavior coverage for the cross-provider account-mapping fix: a grant/revoke must resolve
the person's account in the RESOURCE's own provider, not blindly use their primary account's external_id — and,
when they have no account there yet, auto-provision one (app.services.accounts.ensure_account_in_provider),
rather than silently using the wrong id or failing."""
from uuid import UUID

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import AccessAssignment, Group, IdentityAccount, IdentityProvider, User
from app.providers.base import CreatedUser, NewUserRequest, NormalizedUser
from app.providers.mock import MockProvider
from app.security.auth import AuthenticatedUser, require_authenticated_user


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


def authenticate_as_admin() -> None:
    async def dependency():
        return AuthenticatedUser("admin-oid", "Admin", "admin@example.com", "tenant", ("AccessPilot.Admin",), {})
    app.dependency_overrides[require_authenticated_user] = dependency


async def _seed(factory, *, mock_provision_joiners: bool = True):
    """A person whose PRIMARY account is in a plain ENTRA-typed provider; a Group that lives in a SEPARATE
    MOCK-typed provider — simulating two genuinely different real directories (e.g. Entra + Active Directory)."""
    async with factory() as session:
        primary_provider = IdentityProvider(name="Primary", type="ENTRA", status="CONNECTED", tenant_id="t1")
        other_provider = IdentityProvider(name="Other", type="MOCK", status="CONNECTED", tenant_id="t2", provision_joiners=mock_provision_joiners)
        session.add_all([primary_provider, other_provider])
        await session.flush()
        person = User(provider_id=primary_provider.id, external_id="primary-ext-1", email="pat@x.com", display_name="Pat Person", given_name="Pat", surname="Person", department="IT", status="ACTIVE")
        group = Group(provider_id=other_provider.id, external_id="other-group-1", name="Other-Provider Group", status="ACTIVE", is_privileged=False)
        session.add_all([person, group])
        await session.commit()
        return {"primary_provider_id": primary_provider.id, "other_provider_id": other_provider.id, "user_id": person.id, "group_id": group.id}


async def _create_and_activate(client, ids):
    created = await client.post("/api/v1/assignments", json={"user_id": str(ids["user_id"]), "resource_type": "GROUP", "resource_id": str(ids["group_id"]), "assignment_type": "PERMANENT", "justification": "Test justification."})
    assert created.status_code == 201
    assignment_id = created.json()["id"]
    activated = await client.post(f"/api/v1/assignments/{assignment_id}/activate", json={"duration_hours": 2, "justification": "Test justification."})
    return assignment_id, activated


@pytest.mark.asyncio
async def test_grant_uses_the_persons_account_in_the_groups_own_provider_not_their_primary_account(db_override, monkeypatch):
    """The actual bug: before this fix, every grant used the person's PRIMARY account's external_id regardless of
    which provider the group lives in. Here the group lives in `other_provider`, and the person already has a
    second real account there — the grant must use THAT account's external_id, not their primary one."""
    ids = await _seed(db_override.factory)
    async with db_override.factory() as session:
        session.add(IdentityAccount(user_id=ids["user_id"], provider_id=ids["other_provider_id"], external_id="other-acct-1", username="pat@other.x.com", status="ACTIVE", provisioned_by="JOINER"))
        await session.commit()

    captured = {}
    original = MockProvider.activate_assignment
    async def capturing_activate(self, request):
        captured.update(request)
        return await original(self, request)
    monkeypatch.setattr(MockProvider, "activate_assignment", capturing_activate)

    authenticate_as_admin()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assignment_id, activated = await _create_and_activate(client, ids)
    assert activated.status_code == 200
    assert activated.json()["status"] == "ACTIVE"
    assert captured["user_external_id"] == "other-acct-1"
    assert captured["user_external_id"] != "primary-ext-1"

    async with db_override.factory() as session:
        accounts = (await session.scalars(select(IdentityAccount).where(IdentityAccount.user_id == ids["user_id"]))).all()
        assert len(accounts) == 1  # the existing account was reused, nothing new provisioned


@pytest.mark.asyncio
async def test_grant_auto_provisions_an_account_when_the_person_has_none_in_that_provider(db_override, monkeypatch):
    """No pre-existing account in `other_provider` — the grant must create one on the spot and use it, rather
    than failing or using the wrong (primary) external_id."""
    ids = await _seed(db_override.factory)

    create_calls = []
    async def capturing_create_user(self, request: NewUserRequest) -> CreatedUser:
        create_calls.append(request)
        return CreatedUser(user=NormalizedUser("other-new-1", request.user_principal_name, request.display_name, department=request.department, job_title=request.job_title), temporary_password=None)
    monkeypatch.setattr(MockProvider, "create_user", capturing_create_user)

    captured = {}
    original = MockProvider.activate_assignment
    async def capturing_activate(self, request):
        captured.update(request)
        return await original(self, request)
    monkeypatch.setattr(MockProvider, "activate_assignment", capturing_activate)

    authenticate_as_admin()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assignment_id, activated = await _create_and_activate(client, ids)
    assert activated.status_code == 200
    assert activated.json()["status"] == "ACTIVE"
    assert len(create_calls) == 1
    assert create_calls[0].department == "IT"  # built from the person's own real attributes
    assert captured["user_external_id"] == "other-new-1"

    async with db_override.factory() as session:
        account = (await session.scalars(select(IdentityAccount).where(IdentityAccount.user_id == ids["user_id"], IdentityAccount.provider_id == ids["other_provider_id"]))).first()
        assert account is not None
        assert account.external_id == "other-new-1"
        assert account.provisioned_by == "ASSIGNMENT"


@pytest.mark.asyncio
async def test_grant_refuses_to_auto_provision_when_the_provider_has_provisioning_switched_off(db_override):
    ids = await _seed(db_override.factory, mock_provision_joiners=False)
    authenticate_as_admin()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assignment_id, activated = await _create_and_activate(client, ids)
    assert activated.status_code == 422
    assert activated.json()["error"]["code"] == "PROVIDER_PROVISIONING_DISABLED"

    async with db_override.factory() as session:
        assignment = await session.get(AccessAssignment, UUID(assignment_id))
        assert assignment.status == "ELIGIBLE"  # never falsely marked ACTIVE
        accounts = (await session.scalars(select(IdentityAccount).where(IdentityAccount.user_id == ids["user_id"]))).all()
        assert accounts == []  # nothing provisioned


@pytest.mark.asyncio
async def test_revoke_is_a_clean_no_op_when_the_person_never_had_an_account_in_that_provider(db_override, monkeypatch):
    """Revoke must never auto-provision (there is nothing real to remove for an account that never existed) — and
    must not even call the connector in that case."""
    ids = await _seed(db_override.factory)
    async with db_override.factory() as session:
        from datetime import datetime, timezone
        assignment = AccessAssignment(provider_id=ids["other_provider_id"], user_id=ids["user_id"], resource_type="GROUP", resource_id=ids["group_id"], assignment_type="PERMANENT", status="ACTIVE", start_time=datetime.now(timezone.utc), activated_at=datetime.now(timezone.utc))
        session.add(assignment)
        await session.commit()
        assignment_id = assignment.id

    async def must_not_be_called(self, request):
        raise AssertionError("revoke_assignment should never be called when the person has no account in this provider")
    monkeypatch.setattr(MockProvider, "revoke_assignment", must_not_be_called)

    authenticate_as_admin()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/api/v1/assignments/{assignment_id}/revoke", json={"justification": "Test justification."})
    assert response.status_code == 200
    assert response.json()["status"] == "REVOKED"
