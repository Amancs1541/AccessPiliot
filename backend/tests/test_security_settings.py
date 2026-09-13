import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.security.auth import AuthenticatedUser, require_authenticated_user


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


def authenticate_as(role: str, subject: str = "user-oid") -> None:
    async def dependency():
        return AuthenticatedUser(subject, "Test User", "user@example.com", "tenant", (role,), {})
    app.dependency_overrides[require_authenticated_user] = dependency


@pytest.mark.asyncio
async def test_defaults_are_both_disabled_and_readable_by_a_normal_user(db_override):
    authenticate_as("AccessPilot.User")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/security-settings")
    assert response.status_code == 200
    body = response.json()
    assert body == {"blur_enabled": False, "blur_after_minutes": 1, "lock_enabled": False, "lock_after_minutes": 5, "logout_enabled": False, "logout_after_minutes": 15, "timezone": "Europe/Berlin", "support_contact_email": None}


@pytest.mark.asyncio
async def test_a_normal_user_cannot_update_settings(db_override):
    authenticate_as("AccessPilot.User")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch("/api/v1/security-settings", json={"blur_enabled": True, "blur_after_minutes": 2, "lock_enabled": True, "lock_after_minutes": 10, "logout_enabled": True, "logout_after_minutes": 20, "timezone": "Europe/Berlin"})
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_admin_can_update_settings_and_it_persists(db_override):
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        updated = await client.patch("/api/v1/security-settings", json={"blur_enabled": True, "blur_after_minutes": 2, "lock_enabled": True, "lock_after_minutes": 10, "logout_enabled": True, "logout_after_minutes": 20, "timezone": "Europe/Berlin", "support_contact_email": "helpdesk@example.com"})
        assert updated.status_code == 200
        assert updated.json() == {"blur_enabled": True, "blur_after_minutes": 2, "lock_enabled": True, "lock_after_minutes": 10, "logout_enabled": True, "logout_after_minutes": 20, "timezone": "Europe/Berlin", "support_contact_email": "helpdesk@example.com"}

        refetched = await client.get("/api/v1/security-settings")
    assert refetched.json() == {"blur_enabled": True, "blur_after_minutes": 2, "lock_enabled": True, "lock_after_minutes": 10, "logout_enabled": True, "logout_after_minutes": 20, "timezone": "Europe/Berlin", "support_contact_email": "helpdesk@example.com"}


@pytest.mark.asyncio
async def test_out_of_range_minutes_are_rejected(db_override):
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch("/api/v1/security-settings", json={"blur_enabled": True, "blur_after_minutes": 0, "lock_enabled": False, "lock_after_minutes": 5, "logout_enabled": False, "logout_after_minutes": 15, "timezone": "Europe/Berlin"})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_out_of_range_logout_minutes_are_rejected(db_override):
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch("/api/v1/security-settings", json={"blur_enabled": False, "blur_after_minutes": 1, "lock_enabled": False, "lock_after_minutes": 5, "logout_enabled": True, "logout_after_minutes": 0, "timezone": "Europe/Berlin"})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_an_unrecognized_timezone_is_rejected(db_override):
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch("/api/v1/security-settings", json={"blur_enabled": False, "blur_after_minutes": 1, "lock_enabled": False, "lock_after_minutes": 5, "logout_enabled": False, "logout_after_minutes": 15, "timezone": "Not/A_Real_Zone"})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_a_valid_non_default_timezone_is_accepted_and_persists(db_override):
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        updated = await client.patch("/api/v1/security-settings", json={"blur_enabled": False, "blur_after_minutes": 1, "lock_enabled": False, "lock_after_minutes": 5, "logout_enabled": False, "logout_after_minutes": 15, "timezone": "America/New_York"})
        assert updated.status_code == 200
        assert updated.json()["timezone"] == "America/New_York"

        refetched = await client.get("/api/v1/security-settings")
    assert refetched.json()["timezone"] == "America/New_York"


@pytest.mark.asyncio
async def test_an_invalid_support_contact_email_is_rejected(db_override):
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch("/api/v1/security-settings", json={"blur_enabled": False, "blur_after_minutes": 1, "lock_enabled": False, "lock_after_minutes": 5, "logout_enabled": False, "logout_after_minutes": 15, "timezone": "Europe/Berlin", "support_contact_email": "not-an-email"})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_a_blank_support_contact_email_is_treated_as_unset(db_override):
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch("/api/v1/security-settings", json={"blur_enabled": False, "blur_after_minutes": 1, "lock_enabled": False, "lock_after_minutes": 5, "logout_enabled": False, "logout_after_minutes": 15, "timezone": "Europe/Berlin", "support_contact_email": "   "})
    assert response.status_code == 200
    assert response.json()["support_contact_email"] is None


@pytest.mark.asyncio
async def test_support_contact_is_readable_with_no_authentication_at_all(db_override):
    """The one scenario this endpoint exists for: a user who can't sign in at all because the IDP itself is
    unreachable — it must never require require_authenticated_user, exactly like GET /branding. No
    authenticate_as() call anywhere in this test — require_authenticated_user is never even overridden, so this
    genuinely exercises the "no auth dependency at all" real route, not a mocked-through one."""
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        set_response = await client.patch("/api/v1/security-settings", json={"blur_enabled": False, "blur_after_minutes": 1, "lock_enabled": False, "lock_after_minutes": 5, "logout_enabled": False, "logout_after_minutes": 15, "timezone": "Europe/Berlin", "support_contact_email": "helpdesk@example.com"})
        assert set_response.status_code == 200
    app.dependency_overrides.pop(require_authenticated_user, None)  # get_db's override (from db_override) stays

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        public_response = await client.get("/api/v1/security-settings/support-contact")
    assert public_response.status_code == 200
    assert public_response.json() == {"support_contact_email": "helpdesk@example.com"}


@pytest.mark.asyncio
async def test_support_contact_is_null_when_never_configured(db_override):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/security-settings/support-contact")
    assert response.status_code == 200
    assert response.json() == {"support_contact_email": None}
