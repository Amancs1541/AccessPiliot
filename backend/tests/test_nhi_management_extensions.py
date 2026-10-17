from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import Application, ApplicationOwner, IdentityProvider, LeaverPolicy, NhiNotification, NhiReviewCampaign, NhiReviewItem, NhiRiskException, Notification, User
from app.security.auth import AuthenticatedUser, require_authenticated_user
from app.services.lifecycle import run_leaver
from app.services.nhi import RISK_NO_OWNER, register_new_identity
from app.services.nhi_notifications import reconcile_nhi_notifications
from app.services.nhi_reviews import create_campaign as create_review_campaign
from app.services.nhi_reviews import decide_item as decide_review_item
from app.schemas.nhi import NhiReviewCampaignCreate, NhiReviewItemDecide


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


def authenticate_as(role: str, subject: str = "nhi-oid") -> None:
    async def dependency():
        return AuthenticatedUser(subject, "Test User", "user@example.com", "tenant", (role,), {})
    app.dependency_overrides[require_authenticated_user] = dependency


async def _seed(factory):
    async with factory() as session:
        provider = IdentityProvider(name="Entra DEV", type="ENTRA", status="CONNECTED", tenant_id="t")
        session.add(provider)
        await session.flush()
        actor = User(provider_id=provider.id, external_id="nhi-oid", email="nhi-admin@x.com", display_name="NHI Admin", status="ACTIVE", account_type="NORMAL")
        owner = User(provider_id=provider.id, external_id="owner-1", email="owner@x.com", display_name="App Owner", status="ACTIVE", account_type="NORMAL")
        reviewer = User(provider_id=provider.id, external_id="reviewer-1", email="reviewer@x.com", display_name="Reviewer", status="ACTIVE", account_type="NORMAL")
        session.add_all([actor, owner, reviewer])
        await session.commit()
        return {"provider_id": provider.id, "actor_id": actor.id, "owner_id": owner.id, "reviewer_id": reviewer.id}


# ---------------------------------------------------------------- 1. Risk exception revoke

@pytest.mark.asyncio
async def test_revoking_a_risk_exception_un_suppresses_its_flag(db_override):
    seeded = await _seed(db_override.factory)
    async with db_override.factory() as session:
        application = Application(provider_id=seeded["provider_id"], external_id="sp-1", name="Reporting Service", status="ACTIVE", app_roles=[], nhi_type="SERVICE_PRINCIPAL")
        session.add(application)
        await session.commit()
        application_id = application.id

    authenticate_as("AccessPilot.NHIAdmin")
    future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post(f"/api/v1/nhi/{application_id}/risk-exceptions", json={"risk_type": "NO_OWNER", "justification": "Accepted pending ownership.", "expires_at": future})
        exception_id = created.json()["id"]
        suppressed = await client.get(f"/api/v1/nhi/{application_id}")
        assert suppressed.json()["risk_flags"] == []

        revoked = await client.post(f"/api/v1/nhi/{application_id}/risk-exceptions/{exception_id}/revoke")
        assert revoked.status_code == 200
        assert revoked.json()["revoked_at"] is not None

        reappeared = await client.get(f"/api/v1/nhi/{application_id}")
        assert reappeared.json()["risk_flags"] == [RISK_NO_OWNER]

        # Revoking an already-revoked exception is rejected, not silently accepted again.
        again = await client.post(f"/api/v1/nhi/{application_id}/risk-exceptions/{exception_id}/revoke")
        assert again.status_code == 409


# ---------------------------------------------------------------- 2. Leaver-safe ownership

@pytest.mark.asyncio
async def test_a_leavers_nhi_ownership_is_removed_and_no_owner_reappears(db_override):
    seeded = await _seed(db_override.factory)
    async with db_override.factory() as session:
        application = Application(provider_id=seeded["provider_id"], external_id="sp-1", name="Reporting Service", status="ACTIVE", app_roles=[], nhi_type="SERVICE_PRINCIPAL")
        session.add(application)
        await session.flush()
        session.add(ApplicationOwner(application_id=application.id, user_id=seeded["owner_id"]))
        # A no-op leaver policy scoped to this department — isolates the NHI-ownership effect from every other
        # leaver side effect (account disable, group removal), same pattern as test_leaver.py's "Legal keeps access".
        session.add(LeaverPolicy(name="No-op", priority=1, scope_type="ALL", revoke_access=False, disable_accounts=False, disable_privileged_accounts=False, remove_group_memberships=False))
        await session.commit()
        application_id = application.id
        owner_id = seeded["owner_id"]

        event = await run_leaver(session, owner_id, "MANUAL", "admin-oid", "req-leaver-1")
        assert event is not None
        assert event.changes["nhi_ownerships_removed"] == 1

        remaining = (await session.scalars(select(ApplicationOwner).where(ApplicationOwner.application_id == application_id))).all()
        assert remaining == []

    authenticate_as("AccessPilot.NHIAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        refetched = await client.get(f"/api/v1/nhi/{application_id}")
    assert refetched.json()["risk_flags"] == [RISK_NO_OWNER]


@pytest.mark.asyncio
async def test_leaving_auto_resolves_a_pending_review_item_naming_that_ownership(db_override):
    seeded = await _seed(db_override.factory)
    async with db_override.factory() as session:
        application = Application(provider_id=seeded["provider_id"], external_id="sp-1", name="Reporting Service", status="ACTIVE", app_roles=[], nhi_type="SERVICE_PRINCIPAL")
        session.add(application)
        await session.flush()
        session.add(ApplicationOwner(application_id=application.id, user_id=seeded["owner_id"]))
        session.add(LeaverPolicy(name="No-op", priority=1, scope_type="ALL", revoke_access=False, disable_accounts=False, disable_privileged_accounts=False, remove_group_memberships=False))
        await session.commit()

        campaign = await create_review_campaign(session, NhiReviewCampaignCreate(name="Q1 ownership review", reviewer_id=seeded["reviewer_id"], due_at=datetime.now(timezone.utc) + timedelta(days=14)), "nhi-oid", "req-campaign-1")
        items = list((await session.scalars(select(NhiReviewItem).where(NhiReviewItem.campaign_id == campaign.id))).all())
        assert len(items) == 1 and items[0].decision == "PENDING"

        await run_leaver(session, seeded["owner_id"], "MANUAL", "admin-oid", "req-leaver-2")

        refreshed_item = await session.get(NhiReviewItem, items[0].id)
        assert refreshed_item.decision == "OWNER_REMOVED_LEAVER"


# ---------------------------------------------------------------- 3. Proactive notifications

@pytest.mark.asyncio
async def test_reconcile_creates_one_shared_and_one_personal_notification_and_never_duplicates(db_override):
    seeded = await _seed(db_override.factory)
    async with db_override.factory() as session:
        now = datetime.now(timezone.utc)
        application = Application(provider_id=seeded["provider_id"], external_id="sp-1", name="Reporting Service", status="ACTIVE", app_roles=[], nhi_type="SERVICE_PRINCIPAL", credential_expires_at=now + timedelta(days=5))
        session.add(application)
        await session.flush()
        session.add(ApplicationOwner(application_id=application.id, user_id=seeded["owner_id"]))
        await session.commit()

        await reconcile_nhi_notifications(session)
        shared = list((await session.scalars(select(NhiNotification).where(NhiNotification.notification_type == "CREDENTIAL_EXPIRING"))).all())
        personal = list((await session.scalars(select(Notification).where(Notification.notification_type == "NHI_CREDENTIAL_EXPIRING", Notification.user_id == seeded["owner_id"]))).all())
        assert len(shared) == 1 and shared[0].resolved_at is None
        assert len(personal) == 1

        # A second reconcile pass with the SAME risk still open must not duplicate either notification.
        await reconcile_nhi_notifications(session)
        shared_again = list((await session.scalars(select(NhiNotification).where(NhiNotification.notification_type == "CREDENTIAL_EXPIRING"))).all())
        personal_again = list((await session.scalars(select(Notification).where(Notification.notification_type == "NHI_CREDENTIAL_EXPIRING", Notification.user_id == seeded["owner_id"]))).all())
        assert len(shared_again) == 1 and len(personal_again) == 1

        # Resolving the underlying risk (assigning... already owned — simulate clearing by raising expiry far out)
        application.credential_expires_at = now + timedelta(days=400)
        await session.commit()
        await reconcile_nhi_notifications(session)
        resolved = await session.get(NhiNotification, shared[0].id)
        assert resolved.resolved_at is not None


# ---------------------------------------------------------------- 4. NHI Ownership Reviews

@pytest.mark.asyncio
async def test_review_campaign_snapshot_confirm_and_owner_removed_decisions(db_override):
    seeded = await _seed(db_override.factory)
    async with db_override.factory() as session:
        application = Application(provider_id=seeded["provider_id"], external_id="sp-1", name="Reporting Service", status="ACTIVE", app_roles=[], nhi_type="SERVICE_PRINCIPAL")
        session.add(application)
        await session.flush()
        session.add(ApplicationOwner(application_id=application.id, user_id=seeded["owner_id"]))
        await session.commit()
        application_id = application.id

        campaign = await create_review_campaign(session, NhiReviewCampaignCreate(name="Q1 ownership review", reviewer_id=seeded["reviewer_id"], due_at=datetime.now(timezone.utc) + timedelta(days=14)), "nhi-oid", "req-campaign-2")
        items = list((await session.scalars(select(NhiReviewItem).where(NhiReviewItem.campaign_id == campaign.id))).all())
        assert len(items) == 1

        confirmed = await decide_review_item(session, items[0].id, NhiReviewItemDecide(decision="CONFIRMED"), "reviewer-1", ("AccessPilot.User",), "req-decide-1")
        assert confirmed.decision == "CONFIRMED"
        still_owned = (await session.scalars(select(ApplicationOwner).where(ApplicationOwner.application_id == application_id))).all()
        assert len(still_owned) == 1  # CONFIRMED never touches ownership

    async with db_override.factory() as session:
        application = Application(provider_id=seeded["provider_id"], external_id="sp-2", name="Second Service", status="ACTIVE", app_roles=[], nhi_type="SERVICE_PRINCIPAL")
        session.add(application)
        await session.flush()
        session.add(ApplicationOwner(application_id=application.id, user_id=seeded["owner_id"]))
        await session.commit()
        application_id_2 = application.id

        campaign2 = await create_review_campaign(session, NhiReviewCampaignCreate(name="Second review", reviewer_id=seeded["reviewer_id"], due_at=datetime.now(timezone.utc) + timedelta(days=14)), "nhi-oid", "req-campaign-3")
        items2 = list((await session.scalars(select(NhiReviewItem).where(NhiReviewItem.campaign_id == campaign2.id))).all())
        # Only the NEW application's item is in this campaign — the first application's ownership was already confirmed earlier, a separate campaign.
        own_item = next(i for i in items2 if i.application_id == application_id_2)
        removed = await decide_review_item(session, own_item.id, NhiReviewItemDecide(decision="OWNER_REMOVED"), "reviewer-1", ("AccessPilot.User",), "req-decide-2")
        assert removed.decision == "OWNER_REMOVED"
        remaining = (await session.scalars(select(ApplicationOwner).where(ApplicationOwner.application_id == application_id_2))).all()
        assert remaining == []


# ---------------------------------------------------------------- 5. Self-service "My Owned Identities"

@pytest.mark.asyncio
async def test_mine_returns_only_the_callers_own_owned_identities(db_override):
    seeded = await _seed(db_override.factory)
    async with db_override.factory() as session:
        mine = Application(provider_id=seeded["provider_id"], external_id="sp-mine", name="Mine", status="ACTIVE", app_roles=[], nhi_type="SERVICE_PRINCIPAL")
        someone_elses = Application(provider_id=seeded["provider_id"], external_id="sp-other", name="Not Mine", status="ACTIVE", app_roles=[], nhi_type="SERVICE_PRINCIPAL")
        session.add_all([mine, someone_elses])
        await session.flush()
        session.add(ApplicationOwner(application_id=mine.id, user_id=seeded["owner_id"]))
        session.add(ApplicationOwner(application_id=someone_elses.id, user_id=seeded["reviewer_id"]))
        await session.commit()

    authenticate_as("AccessPilot.User", subject="owner-1")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/nhi/mine")
    assert response.status_code == 200
    names = {identity["name"] for identity in response.json()}
    assert names == {"Mine"}


# ---------------------------------------------------------------- 6. Register a new NHI

@pytest.mark.asyncio
async def test_registering_a_new_identity_succeeds_against_the_mock_connector(db_override):
    seeded = await _seed(db_override.factory)
    async with db_override.factory() as session:
        provider = await session.get(IdentityProvider, seeded["provider_id"])
        provider.type = "MOCK"
        await session.commit()

        application = await register_new_identity(session, seeded["provider_id"], "New Billing Bot", "BOT", seeded["owner_id"], seeded["actor_id"], "req-register-1")
        assert application.nhi_type == "BOT"
        owners = (await session.scalars(select(ApplicationOwner).where(ApplicationOwner.application_id == application.id))).all()
        assert len(owners) == 1 and owners[0].user_id == seeded["owner_id"]


@pytest.mark.asyncio
async def test_refresh_pulls_real_credentials_the_service_principal_itself_never_reported(db_override, monkeypatch):
    """Confirmed live against a real tenant: a service principal's own credential fields can be empty while its
    matching App Registration has real ones (see test_entra_provider.py's merge-logic tests) — a manual refresh
    must pick that up immediately, not wait for the next scheduled sync."""
    from app.providers.base import NormalizedApplication, NormalizedCredential

    seeded = await _seed(db_override.factory)
    async with db_override.factory() as session:
        application = Application(provider_id=seeded["provider_id"], external_id="sp-refresh", name="Reporting Service", status="ACTIVE", app_roles=[], nhi_type="SERVICE_PRINCIPAL", credential_expires_at=None)
        session.add(application)
        await session.commit()
        application_id = application.id

    real_credential = NormalizedCredential(credential_type="PASSWORD", display_name="Portal Secret", expires_at=datetime(2027, 4, 11, tzinfo=timezone.utc))

    async def fake_get_applications(self, query=None):
        return [NormalizedApplication(external_id="sp-refresh", name="Reporting Service", credentials=(real_credential,), credential_expires_at=real_credential.expires_at)]

    monkeypatch.setattr("app.providers.entra.EntraProvider.get_applications", fake_get_applications)

    authenticate_as("AccessPilot.NHIAdmin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        refreshed = await client.post(f"/api/v1/nhi/{application_id}/refresh")
    assert refreshed.status_code == 200
    assert refreshed.json()["credentials"] == [{"credential_type": "PASSWORD", "display_name": "Portal Secret", "expires_at": "2027-04-11T00:00:00Z"}]


@pytest.mark.asyncio
async def test_registering_a_new_identity_fails_cleanly_when_the_provider_does_not_support_it(db_override):
    """Okta's create_application raises NotImplementedError today (see app.providers.okta) — this must surface
    as a clean, explicit error, never a fabricated success."""
    from app.core.errors import AccessPilotError

    seeded = await _seed(db_override.factory)
    async with db_override.factory() as session:
        provider = await session.get(IdentityProvider, seeded["provider_id"])
        provider.type = "OKTA"
        await session.commit()

        with pytest.raises(AccessPilotError) as excinfo:
            await register_new_identity(session, seeded["provider_id"], "New Billing Bot", "BOT", seeded["owner_id"], seeded["actor_id"], "req-register-2")
        assert excinfo.value.code == "PROVIDER_OPERATION_NOT_SUPPORTED"
