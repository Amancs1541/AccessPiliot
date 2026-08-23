import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import IdentityProvider
from app.security.auth import AuthenticatedUser
from app.security.credential_encryption import CredentialEncryptionError, decrypt_credential, encrypt_credential
from app.api.v1.providers import provider_manage


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


def user(role: str):
    async def dependency():
        return AuthenticatedUser("actor-1", "Admin", "admin@example.com", "tenant", (role,), {})
    return dependency


@pytest.fixture(autouse=True)
def credential_key(monkeypatch):
    from app.core.config import get_settings
    from cryptography.fernet import Fernet
    monkeypatch.setenv("PROVIDER_CREDENTIAL_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_encrypt_decrypt_round_trip():
    ciphertext = encrypt_credential("super-secret-value")
    assert ciphertext != "super-secret-value"
    assert decrypt_credential(ciphertext) == "super-secret-value"


def test_decrypt_fails_without_correct_key(monkeypatch):
    from app.core.config import get_settings
    from cryptography.fernet import Fernet
    ciphertext = encrypt_credential("value-a")
    monkeypatch.setenv("PROVIDER_CREDENTIAL_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    with pytest.raises(CredentialEncryptionError):
        decrypt_credential(ciphertext)


@pytest.mark.asyncio
async def test_update_credentials_never_returns_plaintext_and_marks_configured(db_override):
    async with db_override.factory() as session:
        provider = IdentityProvider(name="Entra", type="ENTRA", status="CONNECTED", tenant_id="tenant-1", client_id="client-1", authority="https://login.microsoftonline.com/tenant-1")
        session.add(provider)
        await session.commit()
        provider_id = provider.id

    app.dependency_overrides[provider_manage] = user("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch(f"/api/v1/providers/{provider_id}/credentials", json={"graph_client_id": "graph-client-1", "graph_client_secret": "super-secret-value"})
    body = response.json()
    assert response.status_code == 200
    assert "graph_client_secret" not in body
    assert "super-secret-value" not in str(body)
    assert body["graph_client_id"] == "graph-client-1"
    assert body["credential_configured"] is True
    assert body["status"] == "CONFIGURED"

    async with db_override.factory() as session:
        stored = await session.get(IdentityProvider, provider_id)
        assert stored.graph_client_secret_encrypted != "super-secret-value"
        assert decrypt_credential(stored.graph_client_secret_encrypted) == "super-secret-value"


@pytest.mark.asyncio
async def test_get_provider_never_includes_secret_field(db_override):
    async with db_override.factory() as session:
        provider = IdentityProvider(name="Entra", type="ENTRA", status="CONFIGURED", tenant_id="tenant-1", graph_client_secret_encrypted=encrypt_credential("value"))
        session.add(provider)
        await session.commit()
        provider_id = provider.id

    app.dependency_overrides[provider_manage] = user("AccessPilot.Admin")
    from app.api.v1.providers import provider_read
    app.dependency_overrides[provider_read] = user("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(f"/api/v1/providers/{provider_id}")
    body = response.json()
    assert response.status_code == 200
    assert body["credential_configured"] is True
    assert "graph_client_secret_encrypted" not in body
    assert "value" not in str(body)


@pytest.mark.asyncio
async def test_update_credentials_denied_for_non_admin(db_override):
    async with db_override.factory() as session:
        provider = IdentityProvider(name="Entra", type="ENTRA", status="CONFIGURED", tenant_id="tenant-1")
        session.add(provider)
        await session.commit()
        provider_id = provider.id
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch(f"/api/v1/providers/{provider_id}/credentials", json={"graph_client_secret": "x"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_update_credentials_fails_safely_without_encryption_key(db_override, monkeypatch):
    from app.core.config import Settings
    blank_settings = Settings(database_url="sqlite+aiosqlite:///:memory:", provider_credential_key=None)
    monkeypatch.setattr("app.security.credential_encryption.get_settings", lambda: blank_settings)

    async with db_override.factory() as session:
        provider = IdentityProvider(name="Entra", type="ENTRA", status="CONFIGURED", tenant_id="tenant-1")
        session.add(provider)
        await session.commit()
        provider_id = provider.id

    app.dependency_overrides[provider_manage] = user("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.patch(f"/api/v1/providers/{provider_id}/credentials", json={"graph_client_secret": "x"})
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_entra_provider_decrypts_stored_credential():
    from types import SimpleNamespace
    from app.providers.entra import EntraProvider

    provider = EntraProvider(SimpleNamespace(tenant_id="t", client_id="c", graph_client_id="graph-c", authority="https://login.microsoftonline.com/t", configuration_ref="DATABASE_ENCRYPTED", graph_client_secret_encrypted=encrypt_credential("db-secret")))
    credentials = provider._credentials()
    assert credentials.client_secret == "db-secret"
    assert credentials.client_id == "graph-c"
