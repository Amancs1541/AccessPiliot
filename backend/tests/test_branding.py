import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.security.auth import AuthenticatedUser, require_authenticated_user

TINY_PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="


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
async def test_defaults_are_all_null_and_readable_with_no_auth_at_all(db_override):
    """The sign-in screen needs this before anyone has logged in — must work with zero Authorization header."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/branding")
    assert response.status_code == 200
    assert response.json() == {"sign_in_logo": None, "internal_logo": None, "powered_by_text": None}


@pytest.mark.asyncio
async def test_a_normal_user_cannot_update_branding(db_override):
    authenticate_as("AccessPilot.User")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch("/api/v1/branding", json={"powered_by_text": "Someone Else"})
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_admin_can_update_branding_and_it_persists_and_is_publicly_readable(db_override):
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        updated = await client.patch("/api/v1/branding", json={"sign_in_logo": TINY_PNG, "internal_logo": TINY_PNG, "powered_by_text": "Clover-X"})
    assert updated.status_code == 200
    assert updated.json() == {"sign_in_logo": TINY_PNG, "internal_logo": TINY_PNG, "powered_by_text": "Clover-X"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        refetched = await client.get("/api/v1/branding")
    assert refetched.json()["powered_by_text"] == "Clover-X"


@pytest.mark.asyncio
async def test_non_image_data_uri_is_rejected(db_override):
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch("/api/v1/branding", json={"sign_in_logo": "data:text/html;base64,PHNjcmlwdD4="})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_svg_data_uri_is_rejected(db_override):
    """Deliberately excluded — an SVG can carry embedded script content."""
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch("/api/v1/branding", json={"sign_in_logo": "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4="})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_oversized_logo_is_rejected(db_override):
    authenticate_as("AccessPilot.Admin")
    huge = "data:image/png;base64," + ("A" * 3_000_000)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch("/api/v1/branding", json={"sign_in_logo": huge})
    assert response.status_code == 422
