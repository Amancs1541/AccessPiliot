from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import Application, IdentityProvider, User
from app.security.auth import AuthenticatedUser, require_authenticated_user
from app.services.nhi import RISK_CREDENTIAL_EXPIRED, RISK_CREDENTIAL_EXPIRING_SOON, RISK_NO_OWNER, compute_risk_flags


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


def authenticate_as(role: str, subject: str = "nhi-oid") -> None:
    async def dependency():
        return AuthenticatedUser(subject, "Test User", "user@example.com", "tenant", (role,), {})
    app.dependency_overrides[require_authenticated_user] = dependency


async def _seed(factory):
    async with factory() as session:
        provider = IdentityProvider(name="Entra DEV", type="ENTRA", status="CONNECTED", tenant_id="t")
        session.add(provider)
        await session.flush()
        actor = User(provider_id=provider.id, external_id="nhi-oid", email="nhi-admin@x.com", display_name="NHI Admin", status="ACTIVE")
        owner_candidate = User(provider_id=provider.id, external_id="owner-1", email="owner@x.com", display_name="App Owner", status="ACTIVE")
        session.add_all([actor, owner_candidate])
        await session.commit()
        return {"provider_id": provider.id, "actor_id": actor.id, "owner_id": owner_candidate.id}


def test_no_owner_and_no_credential_data_flags_only_no_owner():
    application = Application(provider_id=None, external_id="e1", name="App", status="ACTIVE", app_roles=[], nhi_type="SERVICE_PRINCIPAL", credential_expires_at=None)
    flags = compute_risk_flags(application, has_owner=False, now=datetime.now(timezone.utc))
    assert flags == [RISK_NO_OWNER]


def test_an_owned_application_with_no_credential_data_has_no_flags():
    application = Application(provider_id=None, external_id="e1", name="App", status="ACTIVE", app_roles=[], nhi_type="SERVICE_PRINCIPAL", credential_expires_at=None)
    assert compute_risk_flags(application, has_owner=True, now=datetime.now(timezone.utc)) == []


def test_an_already_expired_credential_is_flagged_expired_not_expiring_soon():
    now = datetime.now(timezone.utc)
    application = Application(provider_id=None, external_id="e1", name="App", status="ACTIVE", app_roles=[], nhi_type="SERVICE_PRINCIPAL", credential_expires_at=now - timedelta(days=1))
    assert compute_risk_flags(application, has_owner=True, now=now) == [RISK_CREDENTIAL_EXPIRED]


def test_a_credential_expiring_within_the_warning_window_is_flagged_expiring_soon():
    now = datetime.now(timezone.utc)
    application = Application(provider_id=None, external_id="e1", name="App", status="ACTIVE", app_roles=[], nhi_type="SERVICE_PRINCIPAL", credential_expires_at=now + timedelta(days=5))
    assert compute_risk_flags(application, has_owner=True, now=now) == [RISK_CREDENTIAL_EXPIRING_SOON]


def test_a_credential_expiring_far_in_the_future_is_not_flagged():
    now = datetime.now(timezone.utc)
    application = Application(provider_id=None, external_id="e1", name="App", status="ACTIVE", app_roles=[], nhi_type="SERVICE_PRINCIPAL", credential_expires_at=now + timedelta(days=365))
    assert compute_risk_flags(application, has_owner=True, now=now) == []


@pytest.mark.asyncio
async def test_a_plain_user_and_a_plain_admin_are_both_denied_every_nhi_endpoint(db_override):
    """NHIAdmin is deliberately exclusive, the same treatment SoDAdmin/SoCAdmin/ServerAdmin already get — a plain
    AccessPilot.Admin must not automatically see non-human identities either."""
    for role in ("AccessPilot.User", "AccessPilot.Admin"):
        authenticate_as(role)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            assert (await client.get("/api/v1/nhi")).status_code == 403
            assert (await client.get("/api/v1/nhi/summary")).status_code == 403


@pytest.mark.asyncio
async def test_nhi_admin_sees_a_synced_application_flagged_with_no_owner(db_override):
    seeded = await _seed(db_override.factory)
    async with db_override.factory() as session:
        session.add(Application(provider_id=seeded["provider_id"], external_id="sp-1", name="Internal Reporting Service", status="ACTIVE", app_roles=[], nhi_type="SERVICE_PRINCIPAL"))
        await session.commit()

    authenticate_as("AccessPilot.NHIAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        listed = await client.get("/api/v1/nhi")
        assert listed.status_code == 200
        assert len(listed.json()) == 1
        assert listed.json()[0]["risk_flags"] == ["NO_OWNER"]

        summary = await client.get("/api/v1/nhi/summary")
        assert summary.status_code == 200
        body = summary.json()
        assert body["total"] == 1 and body["no_owner"] == 1 and body["credential_expiring_soon"] == 0 and body["credential_expired"] == 0
        assert body["by_type"]["SERVICE_PRINCIPAL"] == 1


@pytest.mark.asyncio
async def test_assigning_an_owner_clears_the_no_owner_flag(db_override):
    seeded = await _seed(db_override.factory)
    async with db_override.factory() as session:
        application = Application(provider_id=seeded["provider_id"], external_id="sp-1", name="Internal Reporting Service", status="ACTIVE", app_roles=[], nhi_type="SERVICE_PRINCIPAL")
        session.add(application)
        await session.commit()
        application_id = application.id

    authenticate_as("AccessPilot.NHIAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assigned = await client.post(f"/api/v1/nhi/{application_id}/owners", json={"user_id": str(seeded["owner_id"])})
        assert assigned.status_code == 201
        assert assigned.json()["risk_flags"] == []
        assert assigned.json()["owners"][0]["email"] == "owner@x.com"

        removed = await client.delete(f"/api/v1/nhi/{application_id}/owners/{seeded['owner_id']}")
        assert removed.status_code == 200
        assert removed.json()["risk_flags"] == ["NO_OWNER"]


@pytest.mark.asyncio
async def test_nhi_admin_can_reclassify_an_identitys_type(db_override):
    seeded = await _seed(db_override.factory)
    async with db_override.factory() as session:
        application = Application(provider_id=seeded["provider_id"], external_id="sp-1", name="Internal Bot", status="ACTIVE", app_roles=[], nhi_type="SERVICE_PRINCIPAL")
        session.add(application)
        await session.commit()
        application_id = application.id

    authenticate_as("AccessPilot.NHIAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        reclassified = await client.patch(f"/api/v1/nhi/{application_id}/type", json={"nhi_type": "BOT"})
        assert reclassified.status_code == 200
        assert reclassified.json()["nhi_type"] == "BOT"
        assert reclassified.json()["nhi_type_overridden"] is True

        rejected = await client.patch(f"/api/v1/nhi/{application_id}/type", json={"nhi_type": "NOT_A_REAL_TYPE"})
        assert rejected.status_code == 422


@pytest.mark.asyncio
async def test_a_manual_type_override_survives_a_later_sync(db_override):
    """The whole point of nhi_type_overridden: an admin's "this is actually a bot" call must not get silently
    reverted back to SERVICE_PRINCIPAL just because a routine sync ran again."""
    from app.providers.base import NormalizedApplication
    from app.services.directory_sync import upsert_application

    seeded = await _seed(db_override.factory)
    async with db_override.factory() as session:
        application = Application(provider_id=seeded["provider_id"], external_id="sp-1", name="Internal Bot", status="ACTIVE", app_roles=[], nhi_type="BOT", nhi_type_overridden=True)
        session.add(application)
        await session.commit()

        # A sync re-fetches this same external_id and reports it as a plain SERVICE_PRINCIPAL again (the
        # connector has no idea an admin reclassified it) — the override must win.
        await upsert_application(session, seeded["provider_id"], NormalizedApplication(external_id="sp-1", name="Internal Bot", nhi_type="SERVICE_PRINCIPAL"))
        await session.commit()

        refreshed = (await session.execute(select(Application).where(Application.provider_id == seeded["provider_id"], Application.external_id == "sp-1"))).scalar_one()
        assert refreshed.nhi_type == "BOT"
        assert refreshed.nhi_type_overridden is True


@pytest.mark.asyncio
async def test_summary_reports_a_kpi_breakdown_by_type(db_override):
    seeded = await _seed(db_override.factory)
    async with db_override.factory() as session:
        session.add_all([
            Application(provider_id=seeded["provider_id"], external_id="sp-1", name="A", status="ACTIVE", app_roles=[], nhi_type="SERVICE_PRINCIPAL"),
            Application(provider_id=seeded["provider_id"], external_id="sp-2", name="B", status="ACTIVE", app_roles=[], nhi_type="BOT", nhi_type_overridden=True),
            Application(provider_id=seeded["provider_id"], external_id="sp-3", name="C", status="ACTIVE", app_roles=[], nhi_type="AI_AGENT", nhi_type_overridden=True),
        ])
        await session.commit()

    authenticate_as("AccessPilot.NHIAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        summary = await client.get("/api/v1/nhi/summary")
    assert summary.status_code == 200
    body = summary.json()
    assert body["total"] == 3
    assert body["by_type"]["SERVICE_PRINCIPAL"] == 1
    assert body["by_type"]["BOT"] == 1
    assert body["by_type"]["AI_AGENT"] == 1
    assert body["by_type"]["MANAGED_IDENTITY"] == 0


@pytest.mark.asyncio
async def test_a_risk_exception_suppresses_its_flag_until_it_is_examined_again(db_override):
    seeded = await _seed(db_override.factory)
    async with db_override.factory() as session:
        application = Application(provider_id=seeded["provider_id"], external_id="sp-1", name="Internal Reporting Service", status="ACTIVE", app_roles=[], nhi_type="SERVICE_PRINCIPAL")
        session.add(application)
        await session.commit()
        application_id = application.id

    authenticate_as("AccessPilot.NHIAdmin")
    future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(f"/api/v1/nhi/{application_id}/risk-exceptions", json={"risk_type": "NO_OWNER", "justification": "Accepted until ownership is assigned by the app team.", "expires_at": future})
        assert created.status_code == 201

        fetched = await client.get(f"/api/v1/nhi/{application_id}")
        assert fetched.status_code == 200
        assert fetched.json()["risk_flags"] == []

        exceptions = await client.get(f"/api/v1/nhi/{application_id}/risk-exceptions")
        assert exceptions.status_code == 200
        assert len(exceptions.json()) == 1
        assert exceptions.json()[0]["risk_type"] == "NO_OWNER"


@pytest.mark.asyncio
async def test_the_full_credential_list_is_returned_alongside_the_soonest_expiry(db_override):
    seeded = await _seed(db_override.factory)
    now = datetime.now(timezone.utc)
    async with db_override.factory() as session:
        application = Application(
            provider_id=seeded["provider_id"], external_id="sp-1", name="Internal Reporting Service", status="ACTIVE", app_roles=[], nhi_type="SERVICE_PRINCIPAL",
            credential_expires_at=now + timedelta(days=5),
            nhi_credentials=[{"credential_type": "PASSWORD", "display_name": "Secret 1", "expires_at": (now + timedelta(days=5)).isoformat()}, {"credential_type": "CERTIFICATE", "display_name": "Cert 1", "expires_at": (now + timedelta(days=400)).isoformat()}],
        )
        session.add(application)
        await session.commit()
        application_id = application.id

    authenticate_as("AccessPilot.NHIAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        fetched = await client.get(f"/api/v1/nhi/{application_id}")
    assert fetched.status_code == 200
    credentials = fetched.json()["credentials"]
    assert len(credentials) == 2
    assert {c["credential_type"] for c in credentials} == {"PASSWORD", "CERTIFICATE"}


@pytest.mark.asyncio
async def test_disabling_an_identity_calls_the_provider_and_updates_status(db_override, monkeypatch):
    seeded = await _seed(db_override.factory)
    async with db_override.factory() as session:
        application = Application(provider_id=seeded["provider_id"], external_id="sp-1", name="Internal Reporting Service", status="ACTIVE", app_roles=[], nhi_type="SERVICE_PRINCIPAL")
        session.add(application)
        await session.commit()
        application_id = application.id

    calls = []

    async def fake_set_application_enabled(self, external_id, enabled):
        calls.append((external_id, enabled))
        return True

    monkeypatch.setattr("app.providers.entra.EntraProvider.set_application_enabled", fake_set_application_enabled)

    authenticate_as("AccessPilot.NHIAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        disabled = await client.post(f"/api/v1/nhi/{application_id}/disable")
        assert disabled.status_code == 200
        assert disabled.json()["status"] == "DISABLED"

        enabled = await client.post(f"/api/v1/nhi/{application_id}/enable")
        assert enabled.status_code == 200
        assert enabled.json()["status"] == "ACTIVE"

    assert calls == [("sp-1", False), ("sp-1", True)]


@pytest.mark.asyncio
async def test_a_permission_denied_error_from_the_provider_surfaces_cleanly_on_disable(db_override, monkeypatch):
    """The write scope this needs (Application.ReadWrite.All) is a step beyond the read-only scope this app's
    sync has needed so far — this must fail loudly (403), never silently pretend to have worked."""
    from app.providers.graph_client import GraphError

    seeded = await _seed(db_override.factory)
    async with db_override.factory() as session:
        application = Application(provider_id=seeded["provider_id"], external_id="sp-1", name="Internal Reporting Service", status="ACTIVE", app_roles=[], nhi_type="SERVICE_PRINCIPAL")
        session.add(application)
        await session.commit()
        application_id = application.id

    async def fake_set_application_enabled(self, external_id, enabled):
        raise GraphError("PROVIDER_PERMISSION_DENIED", "Microsoft Graph request failed (403).", 502, http_status=403)

    monkeypatch.setattr("app.providers.entra.EntraProvider.set_application_enabled", fake_set_application_enabled)

    authenticate_as("AccessPilot.NHIAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(f"/api/v1/nhi/{application_id}/disable")
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "PROVIDER_PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_activity_lists_accesspilot_actions_recorded_against_this_identity(db_override):
    seeded = await _seed(db_override.factory)
    async with db_override.factory() as session:
        application = Application(provider_id=seeded["provider_id"], external_id="sp-1", name="Internal Reporting Service", status="ACTIVE", app_roles=[], nhi_type="SERVICE_PRINCIPAL")
        session.add(application)
        await session.commit()
        application_id = application.id

    authenticate_as("AccessPilot.NHIAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post(f"/api/v1/nhi/{application_id}/owners", json={"user_id": str(seeded["owner_id"])})
        activity = await client.get(f"/api/v1/nhi/{application_id}/activity")
    assert activity.status_code == 200
    actions = [entry["action"] for entry in activity.json()]
    assert "NHI_OWNER_ASSIGNED" in actions


@pytest.mark.asyncio
async def test_permissions_are_live_fetched_and_role_names_resolved_against_a_synced_resource(db_override, monkeypatch):
    """The provider returns a raw Graph appRoleId GUID; this app resolves it to a friendly name by matching
    against the resource's own already-synced app_roles, when that resource happens to be a synced Application."""
    from app.providers.base import NormalizedApplicationPermission

    seeded = await _seed(db_override.factory)
    async with db_override.factory() as session:
        application = Application(provider_id=seeded["provider_id"], external_id="sp-1", name="Internal Reporting Service", status="ACTIVE", app_roles=[], nhi_type="SERVICE_PRINCIPAL")
        resource = Application(provider_id=seeded["provider_id"], external_id="graph-sp", name="Microsoft Graph", status="ACTIVE", app_roles=[{"id": "role-guid-1", "name": "Mail.Read", "description": None}])
        session.add_all([application, resource])
        await session.commit()
        application_id = application.id

    async def fake_get_application_permissions(self, external_id):
        return [NormalizedApplicationPermission(resource_external_id="graph-sp", resource_display_name="Microsoft Graph", role_name="role-guid-1")]

    monkeypatch.setattr("app.providers.entra.EntraProvider.get_application_permissions", fake_get_application_permissions)

    authenticate_as("AccessPilot.NHIAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/v1/nhi/{application_id}/permissions")
    assert response.status_code == 200
    assert response.json() == [{"resource_display_name": "Microsoft Graph", "role_name": "Mail.Read"}]
