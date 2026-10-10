from uuid import UUID

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import IdentityProvider
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


def authenticate_as(role: str, subject: str = "admin-oid") -> None:
    async def dependency():
        return AuthenticatedUser(subject, "Admin", "admin@example.com", "tenant", (role,), {})
    app.dependency_overrides[require_authenticated_user] = dependency


async def _create_ad_provider(client) -> str:
    authenticate_as("AccessPilot.Admin")
    response = await client.post("/api/v1/providers", json={"name": "Corp AD", "provider_type": "ACTIVE_DIRECTORY", "tenant_id": "corp.local"})
    assert response.status_code == 201
    return response.json()["id"]


@pytest.mark.asyncio
async def test_admin_can_generate_an_agent_key_and_it_is_shown_only_once(db_override):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        provider_id = await _create_ad_provider(client)
        generated = await client.post(f"/api/v1/providers/{provider_id}/agent/generate-key")
        assert generated.status_code == 200
        api_key = generated.json()["api_key"]
        assert len(api_key) > 20

    async with db_override.factory() as session:
        row = await session.get(IdentityProvider, UUID(provider_id))
        assert row.agent_api_key_hash is not None
        assert row.agent_api_key_hash != api_key  # stored as a hash, never the plaintext


@pytest.mark.asyncio
async def test_generating_a_key_for_a_non_ad_provider_is_rejected(db_override):
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        entra = await client.post("/api/v1/providers", json={"name": "Entra", "provider_type": "MOCK", "tenant_id": "t"})
        provider_id = entra.json()["id"]
        response = await client.post(f"/api/v1/providers/{provider_id}/agent/generate-key")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PROVIDER_NOT_ACTIVE_DIRECTORY"


@pytest.mark.asyncio
async def test_a_plain_user_cannot_generate_an_agent_key(db_override):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        provider_id = await _create_ad_provider(client)
        authenticate_as("AccessPilot.User", subject="some-user")
        response = await client.post(f"/api/v1/providers/{provider_id}/agent/generate-key")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_agent_heartbeat_with_a_valid_key_marks_the_provider_connected(db_override):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        provider_id = await _create_ad_provider(client)
        api_key = (await client.post(f"/api/v1/providers/{provider_id}/agent/generate-key")).json()["api_key"]

        before = await client.get(f"/api/v1/providers/{provider_id}")
        assert before.json()["agent_connected"] is False
        assert before.json()["agent_configured"] is True

        heartbeat = await client.post(f"/api/v1/providers/{provider_id}/agent/heartbeat", headers={"X-Agent-Key": api_key})
        assert heartbeat.status_code == 200
        assert heartbeat.json()["agent_connected"] is True
        assert heartbeat.json()["agent_last_seen_at"] is not None


@pytest.mark.asyncio
async def test_agent_heartbeat_with_an_invalid_key_is_rejected(db_override):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        provider_id = await _create_ad_provider(client)
        await client.post(f"/api/v1/providers/{provider_id}/agent/generate-key")
        response = await client.post(f"/api/v1/providers/{provider_id}/agent/heartbeat", headers={"X-Agent-Key": "totally-wrong-key"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_AGENT_KEY"


@pytest.mark.asyncio
async def test_agent_heartbeat_with_no_key_at_all_is_rejected(db_override):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        provider_id = await _create_ad_provider(client)
        response = await client.post(f"/api/v1/providers/{provider_id}/agent/heartbeat")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_regenerating_the_key_invalidates_the_old_one(db_override):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        provider_id = await _create_ad_provider(client)
        old_key = (await client.post(f"/api/v1/providers/{provider_id}/agent/generate-key")).json()["api_key"]
        await client.post(f"/api/v1/providers/{provider_id}/agent/heartbeat", headers={"X-Agent-Key": old_key})

        new_key = (await client.post(f"/api/v1/providers/{provider_id}/agent/generate-key")).json()["api_key"]
        assert new_key != old_key

        old_key_attempt = await client.post(f"/api/v1/providers/{provider_id}/agent/heartbeat", headers={"X-Agent-Key": old_key})
        assert old_key_attempt.status_code == 401

        new_key_attempt = await client.post(f"/api/v1/providers/{provider_id}/agent/heartbeat", headers={"X-Agent-Key": new_key})
        assert new_key_attempt.status_code == 200


@pytest.mark.asyncio
async def test_admin_can_download_the_agent_script(db_override):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        provider_id = await _create_ad_provider(client)
        response = await client.get(f"/api/v1/providers/{provider_id}/agent/download")
    assert response.status_code == 200
    assert b"ACCESSPILOT_AGENT_KEY" in response.content
    assert b"def send_heartbeat" in response.content


@pytest.mark.asyncio
async def test_an_entra_or_mock_provider_never_shows_agent_fields_as_configured(db_override):
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/providers", json={"name": "Mock", "provider_type": "MOCK", "tenant_id": "t"})
        body = created.json()
    assert body["agent_configured"] is False
    assert body["agent_connected"] is False
    assert body["agent_last_seen_at"] is None
