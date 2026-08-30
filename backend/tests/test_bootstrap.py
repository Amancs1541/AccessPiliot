import jwt
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import BootstrapCredential, PortalAuthConfig
from app.security.credential_hashing import hash_password, verify_password
from app.services.bootstrap import decode_setup_session, ensure_bootstrap_credential, portal_setup_is_needed, verify_bootstrap_login


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


@pytest_asyncio.fixture
async def db():
    database = TestSession()
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with database.factory() as session:
        yield session
    await database.engine.dispose()


@pytest.fixture(autouse=True)
def clear_env(monkeypatch):
    """These tests need entra_tenant_id/entra_authority to read as falsy, regardless of whatever real .env this
    machine has loaded (this repo's own dev backend has real Entra configured in backend/.env). Settings reads
    that file directly (env_file=".env"), so monkeypatch.delenv alone would NOT unset it — pydantic-settings
    still falls back to the .env file's value once the explicit env var is gone. Setting real env vars to empty
    strings does take precedence over the .env file, and an empty string reads falsy in portal_setup_is_needed's
    `if settings.entra_tenant_id and settings.entra_authority` check."""
    monkeypatch.setenv("ENTRA_TENANT_ID", "")
    monkeypatch.setenv("ENTRA_AUTHORITY", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_password_hash_roundtrip():
    hashed = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", hashed)
    assert not verify_password("wrong password", hashed)


def test_password_hash_is_salted_differently_each_time():
    assert hash_password("same password") != hash_password("same password")


@pytest.mark.asyncio
async def test_setup_is_needed_with_nothing_configured(db: AsyncSession):
    assert await portal_setup_is_needed(db) is True


@pytest.mark.asyncio
async def test_setup_is_not_needed_when_env_entra_is_configured(db: AsyncSession, monkeypatch):
    monkeypatch.setenv("ENTRA_TENANT_ID", "tenant-1")
    monkeypatch.setenv("ENTRA_AUTHORITY", "https://login.microsoftonline.com/tenant-1")
    get_settings.cache_clear()
    assert await portal_setup_is_needed(db) is False


@pytest.mark.asyncio
async def test_setup_is_not_needed_when_a_portal_auth_config_is_active(db: AsyncSession):
    db.add(PortalAuthConfig(idp_type="ENTRA", is_active=True))
    await db.commit()
    assert await portal_setup_is_needed(db) is False


@pytest.mark.asyncio
async def test_setup_is_still_needed_when_a_portal_auth_config_exists_but_is_inactive(db: AsyncSession):
    db.add(PortalAuthConfig(idp_type="ENTRA", is_active=False))
    await db.commit()
    assert await portal_setup_is_needed(db) is True


@pytest.mark.asyncio
async def test_ensure_bootstrap_credential_generates_once_and_is_idempotent(db: AsyncSession):
    first_password = await ensure_bootstrap_credential(db)
    assert first_password is not None
    second_password = await ensure_bootstrap_credential(db)
    assert second_password is None  # already exists — never regenerated / never invalidates the first one

    rows = (await db.execute(select(BootstrapCredential))).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_ensure_bootstrap_credential_does_nothing_when_setup_is_not_needed(db: AsyncSession, monkeypatch):
    monkeypatch.setenv("ENTRA_TENANT_ID", "tenant-1")
    monkeypatch.setenv("ENTRA_AUTHORITY", "https://login.microsoftonline.com/tenant-1")
    get_settings.cache_clear()
    assert await ensure_bootstrap_credential(db) is None
    rows = (await db.execute(select(BootstrapCredential))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_bootstrap_login_succeeds_and_issues_a_valid_setup_session(db: AsyncSession):
    password = await ensure_bootstrap_credential(db)
    token = await verify_bootstrap_login(db, "admin", password)
    credential = await decode_setup_session(db, token)
    assert credential.username == "admin"


@pytest.mark.asyncio
async def test_bootstrap_login_fails_with_wrong_password(db: AsyncSession):
    await ensure_bootstrap_credential(db)
    with pytest.raises(Exception):
        await verify_bootstrap_login(db, "admin", "totally-wrong")


@pytest.mark.asyncio
async def test_setup_session_becomes_invalid_after_setup_completes(db: AsyncSession):
    """Once the bootstrap credential row is deleted (setup completed), every previously-issued setup-session
    token must instantly and permanently fail — this is the self-expiry property the design relies on."""
    password = await ensure_bootstrap_credential(db)
    token = await verify_bootstrap_login(db, "admin", password)

    await db.execute(delete(BootstrapCredential))
    await db.commit()

    with pytest.raises(Exception):
        await decode_setup_session(db, token)


@pytest.mark.asyncio
async def test_a_setup_session_token_cannot_be_forged_with_a_different_secret(db: AsyncSession):
    await ensure_bootstrap_credential(db)
    forged = jwt.encode({"purpose": "setup", "sub": "not-the-real-id"}, "guessed-secret", algorithm="HS256")
    with pytest.raises(Exception):
        await decode_setup_session(db, forged)


@pytest.mark.asyncio
async def test_status_endpoint_reflects_whether_setup_is_needed(db_override):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/setup/status")
    assert response.status_code == 200
    assert response.json()["needs_setup"] is True


@pytest.mark.asyncio
async def test_bootstrap_login_endpoint_end_to_end(db_override):
    async with db_override.factory() as session:
        password = await ensure_bootstrap_credential(session)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        wrong = await client.post("/api/v1/setup/bootstrap-login", json={"username": "admin", "password": "nope"})
        assert wrong.status_code == 401
        right = await client.post("/api/v1/setup/bootstrap-login", json={"username": "admin", "password": password})
    assert right.status_code == 200
    assert right.json()["setup_token"]


@pytest.mark.asyncio
async def test_a_setup_session_token_is_rejected_by_a_real_authenticated_endpoint(db_override):
    """Critical isolation test: a setup-session token (HS256, locally signed) must never be usable as a stand-in
    for a real Entra-issued access token (RS256, JWKS-validated) on any normal app endpoint."""
    async with db_override.factory() as session:
        password = await ensure_bootstrap_credential(session)
        setup_token = await verify_bootstrap_login(session, "admin", password)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {setup_token}"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_decode_setup_session_rejects_a_token_with_no_relation_to_any_bootstrap_credential(db: AsyncSession):
    """And the reverse direction of the isolation guarantee: nothing that isn't an HS256 token signed with a LIVE
    BootstrapCredential.session_secret can ever satisfy require_setup_session — a real IDP-issued RS256 token,
    or literally any other string, is rejected the same way."""
    await ensure_bootstrap_credential(db)
    with pytest.raises(Exception):
        await decode_setup_session(db, "not-a-jwt-at-all")
