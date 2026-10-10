from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.providers.active_directory import ActiveDirectoryProvider
from app.security.auth import AuthenticatedUser, require_authenticated_user
from app.security.credential_encryption import encrypt_credential
from app.services.provider_configuration import _connector


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


def _fake_provider(**overrides):
    base = {"organization_url": None, "tenant_id": None, "graph_client_id": None, "graph_client_secret_encrypted": None}
    base.update(overrides)
    return SimpleNamespace(**base)


# ---- Pure config-validation logic (no network) ----

def test_missing_url_is_rejected():
    connector = ActiveDirectoryProvider(_fake_provider())
    with pytest.raises(ValueError, match="LDAP"):
        connector._server_args()


def test_url_must_be_ldap_or_ldaps_scheme():
    connector = ActiveDirectoryProvider(_fake_provider(organization_url="https://10.0.0.5:636"))
    with pytest.raises(ValueError, match="ldap"):
        connector._server_args()


def test_ldaps_url_defaults_to_port_636():
    connector = ActiveDirectoryProvider(_fake_provider(organization_url="ldaps://10.0.0.5"))
    host, port, use_ssl = connector._server_args()
    assert (host, port, use_ssl) == ("10.0.0.5", 636, True)


def test_plain_ldap_url_defaults_to_port_389_no_tls():
    connector = ActiveDirectoryProvider(_fake_provider(organization_url="ldap://10.0.0.5"))
    host, port, use_ssl = connector._server_args()
    assert (host, port, use_ssl) == ("10.0.0.5", 389, False)


def test_explicit_port_is_respected():
    connector = ActiveDirectoryProvider(_fake_provider(organization_url="ldaps://10.0.0.5:10636"))
    _, port, _ = connector._server_args()
    assert port == 10636


def test_missing_base_dn_is_rejected():
    connector = ActiveDirectoryProvider(_fake_provider(organization_url="ldaps://10.0.0.5"))
    with pytest.raises(ValueError, match="base DN"):
        connector._base_dn()


def test_missing_bind_dn_is_rejected():
    connector = ActiveDirectoryProvider(_fake_provider())
    with pytest.raises(ValueError, match="username"):
        connector._bind_dn()


def test_missing_bind_password_is_rejected():
    connector = ActiveDirectoryProvider(_fake_provider())
    with pytest.raises(ValueError, match="password"):
        connector._bind_password()


def test_bind_password_is_decrypted_from_the_same_fernet_encryption_entra_uses():
    encrypted = encrypt_credential("super-secret-password")
    connector = ActiveDirectoryProvider(_fake_provider(graph_client_secret_encrypted=encrypted))
    assert connector._bind_password() == "super-secret-password"


def test_connector_dispatch_routes_active_directory_type_correctly():
    connector = _connector(_fake_provider(type="ACTIVE_DIRECTORY"))
    assert isinstance(connector, ActiveDirectoryProvider)


# ---- Dynamic config through the real admin API (URL/base DN/username/password, no LDAP network call) ----

@pytest.mark.asyncio
async def test_admin_can_set_ldap_url_and_base_dn_via_the_generic_provider_update(db_override):
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/providers", json={"name": "Corp AD", "provider_type": "ACTIVE_DIRECTORY", "tenant_id": "DC=example,DC=local"})
        provider_id = created.json()["id"]
        updated = await client.patch(f"/api/v1/providers/{provider_id}", json={"organization_url": "ldaps://192.168.71.4:636", "tenant_id": "DC=TeamDEV,DC=local"})
    assert updated.status_code == 200
    body = updated.json()
    assert body["organization_url"] == "ldaps://192.168.71.4:636"
    assert body["tenant_id"] == "DC=TeamDEV,DC=local"


@pytest.mark.asyncio
async def test_admin_can_set_bind_username_and_password_via_the_generic_credentials_endpoint(db_override):
    authenticate_as("AccessPilot.Admin")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        created = await client.post("/api/v1/providers", json={"name": "Corp AD", "provider_type": "ACTIVE_DIRECTORY", "tenant_id": "DC=example,DC=local"})
        provider_id = created.json()["id"]
        updated = await client.patch(f"/api/v1/providers/{provider_id}/credentials", json={"graph_client_id": "AP.mock@TeamDEV.local", "graph_client_secret": "Aman@12@"})
    assert updated.status_code == 200
    body = updated.json()
    assert body["graph_client_id"] == "AP.mock@TeamDEV.local"
    assert body["credential_configured"] is True  # the password itself is never echoed back
