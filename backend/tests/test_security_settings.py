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
    assert body == {"blur_enabled": False, "blur_after_minutes": 1, "lock_enabled": False, "lock_after_minutes": 5, "logout_enabled": False, "logout_after_minutes": 15, "timezone": "Europe/Berlin"}


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
        updated = await client.patch("/api/v1/security-settings", json={"blur_enabled": True, "blur_after_minutes": 2, "lock_enabled": True, "lock_after_minutes": 10, "logout_enabled": True, "logout_after_minutes": 20, "timezone": "Europe/Berlin"})
        assert updated.status_code == 200
        assert updated.json() == {"blur_enabled": True, "blur_after_minutes": 2, "lock_enabled": True, "lock_after_minutes": 10, "logout_enabled": True, "logout_after_minutes": 20, "timezone": "Europe/Berlin"}

        refetched = await client.get("/api/v1/security-settings")
    assert refetched.json() == {"blur_enabled": True, "blur_after_minutes": 2, "lock_enabled": True, "lock_after_minutes": 10, "logout_enabled": True, "logout_after_minutes": 20, "timezone": "Europe/Berlin"}


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
