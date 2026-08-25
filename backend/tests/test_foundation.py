import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.db.base import Base
from app.main import app
from app.providers.base import IdentityProvider
from app.providers.mock import MockProvider


@pytest.mark.asyncio
async def test_health_and_request_id() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/health", headers={"X-Request-ID": "test-request-1"})
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "AccessPilot"}
    assert response.headers["X-Request-ID"] == "test-request-1"


@pytest.mark.asyncio
async def test_unknown_dashboard_scope_returns_not_found() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/dashboard/not-a-problem", headers={"X-Request-ID": "validation-1"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "RESOURCE_NOT_FOUND"


@pytest.mark.asyncio
async def test_database_metadata_creates_all_documented_tables() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        names = await connection.run_sync(lambda conn: set(conn.dialect.get_table_names(conn)))
    assert names == {"identity_providers", "users", "groups", "roles", "applications", "user_groups", "role_assignments", "access_assignments", "access_requests", "approval_steps", "policies", "policy_targets", "audit_logs", "sync_runs", "sync_errors", "provider_resources", "access_packages", "access_package_items", "access_package_assignments", "access_package_eligibility"}
    await engine.dispose()


@pytest.mark.asyncio
async def test_mock_provider_normalizes_and_mutates() -> None:
    provider = MockProvider()
    assert isinstance(provider, IdentityProvider)
    assert len(await provider.get_users()) == 2
    assert len(await provider.get_groups()) == 2
    assert len(await provider.get_roles()) == 2
    assert await provider.add_group_member("group-001", "user-002")
    assert len(await provider.get_group_members("group-001")) == 2
    assert await provider.activate_assignment({"assignment_id": "a-1"})
    assert await provider.revoke_assignment({"assignment_id": "a-1"})


def test_configuration_accepts_reserved_entra_mode_for_phase_two() -> None:
    settings = Settings(database_url="sqlite+aiosqlite:///:memory:", provider_mode="entra")
    assert settings.provider_mode == "entra"
