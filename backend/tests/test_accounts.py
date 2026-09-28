from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import IdentityAccount, IdentityProvider, User
from app.providers.graph_client import GraphError
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


def authenticate_as(role: str, subject: str = "admin-oid") -> None:
    async def dependency():
        return AuthenticatedUser(subject, "Someone", "someone@example.com", "tenant", (role,), {})
    app.dependency_overrides[require_authenticated_user] = dependency


class FakeConnector:
    """Records set_user_enabled calls; `fail_for` external ids raise like an unavailable directory."""
    calls: list = []

    def __init__(self, fail_for=()):
        self.fail_for = set(fail_for)

    async def set_user_enabled(self, external_id, enabled):
        if external_id in self.fail_for:
            raise GraphError("PROVIDER_UNAVAILABLE", "directory down", 503)
        FakeConnector.calls.append((external_id, enabled))
        return True


async def _seed(session):
    entra = IdentityProvider(name="Entra", type="ENTRA", status="CONNECTED", tenant_id="t1")
    okta = IdentityProvider(name="Okta", type="OKTA", status="CONNECTED", tenant_id="t2")
    csv = IdentityProvider(name="CSV", type="CSV", status="CONNECTED", tenant_id="csv")
    session.add_all([entra, okta, csv])
    await session.flush()
    person = User(provider_id=entra.id, external_id="ent-1", email="pat@x.com", display_name="Pat Person", status="ACTIVE")
    csv_only = User(provider_id=csv.id, external_id="EMP9", email="csv@x.com", display_name="Csv Only", status="ACTIVE")
    session.add_all([person, csv_only])
    await session.flush()
    # A second IdP account for the same person, as the joiner process will create.
    session.add(IdentityAccount(user_id=person.id, provider_id=okta.id, external_id="okta-1", username="pat@okta.x.com", status="ACTIVE", provisioned_by="JOINER"))
    await session.commit()
    return {"person": person.id, "csv": csv_only.id, "entra": entra.id, "okta": okta.id}


@pytest.mark.asyncio
async def test_listing_mirrors_the_primary_account_and_shows_every_idp(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/v1/users/{ids['person']}/accounts")
        csv_response = await client.get(f"/api/v1/users/{ids['csv']}/accounts")
    rows = response.json()
    assert [(r["provider_name"], r["is_primary"], r["provisioned_by"]) for r in rows] == [("Entra", True, "SYNC"), ("Okta", False, "JOINER")]
    assert csv_response.json() == []  # a CSV bookkeeping identity has no directory account


@pytest.mark.asyncio
async def test_disable_all_reaches_every_idp_and_enable_all_restores_them(db_override, monkeypatch):
    FakeConnector.calls = []
    monkeypatch.setattr("app.services.accounts._connector", lambda provider: FakeConnector())
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        disabled = await client.post(f"/api/v1/users/{ids['person']}/accounts/disable-all")
        again = await client.post(f"/api/v1/users/{ids['person']}/accounts/disable-all")
        enabled = await client.post(f"/api/v1/users/{ids['person']}/accounts/enable-all")
    body = disabled.json()
    assert body["user_status"] == "DISABLED" and all(r["ok"] and not r["already"] for r in body["results"])
    assert sorted(FakeConnector.calls[:2]) == [("ent-1", False), ("okta-1", False)]
    assert all(r["already"] for r in again.json()["results"])          # nothing to do the second time
    assert enabled.json()["user_status"] == "ACTIVE" and {a["status"] for a in enabled.json()["accounts"]} == {"ACTIVE"}


@pytest.mark.asyncio
async def test_one_directory_failing_is_reported_and_does_not_block_the_others(db_override, monkeypatch):
    monkeypatch.setattr("app.services.accounts._connector", lambda provider: FakeConnector(fail_for={"okta-1"}))
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/api/v1/users/{ids['person']}/accounts/disable-all")
    body = response.json()
    by_name = {r["provider_name"]: r for r in body["results"]}
    assert by_name["Entra"]["ok"] is True and by_name["Okta"]["ok"] is False and "directory down" in by_name["Okta"]["error"]
    statuses = {a["provider_name"]: a["status"] for a in body["accounts"]}
    assert statuses == {"Entra": "DISABLED", "Okta": "ACTIVE"} and body["user_status"] == "DISABLED"


@pytest.mark.asyncio
async def test_a_single_account_can_be_toggled_and_an_admin_cannot_disable_themselves(db_override, monkeypatch):
    monkeypatch.setattr("app.services.accounts._connector", lambda provider: FakeConnector())
    async with db_override.factory() as session:
        ids = await _seed(session)
        session.add(User(provider_id=ids["entra"], external_id="admin-oid", email="admin@x.com", display_name="Admin Self", status="ACTIVE"))
        await session.commit()
        okta_account = (await session.scalars(select(IdentityAccount).where(IdentityAccount.external_id == "okta-1"))).one()
        admin_id = (await session.scalars(select(User.id).where(User.external_id == "admin-oid"))).one()
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        one = await client.post(f"/api/v1/users/{ids['person']}/accounts/{okta_account.id}/enabled", json={"enabled": False})
        own = await client.post(f"/api/v1/users/{admin_id}/accounts/disable-all")
    assert one.status_code == 200 and one.json()["status"] == "DISABLED" and one.json()["provider_name"] == "Okta"
    async with db_override.factory() as session:
        person = await session.get(User, ids["person"])
    assert person.status == "ACTIVE"  # only the Okta account changed; the primary (Entra) is untouched
    assert own.status_code == 400


@pytest.mark.asyncio
async def test_account_endpoints_are_admin_only(db_override):
    async with db_override.factory() as session:
        ids = await _seed(session)
    authenticate_as("AccessPilot.User", subject="regular")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        listed = await client.get(f"/api/v1/users/{ids['person']}/accounts")
        disabled = await client.post(f"/api/v1/users/{ids['person']}/accounts/disable-all")
        missing = await client.get(f"/api/v1/users/{uuid4()}/accounts")
    assert listed.status_code == 403 and disabled.status_code == 403 and missing.status_code == 403
