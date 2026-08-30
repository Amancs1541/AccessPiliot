from datetime import datetime, timedelta, timezone

import jwt
import pytest
import pytest_asyncio
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import BootstrapCredential, BreakGlassAccount, PortalAuthConfig
from app.schemas.portal_auth import PortalAuthConfigureRequest
from app.services.bootstrap import ensure_bootstrap_credential, verify_bootstrap_login
from app.services.portal_auth import activate_portal_auth_config, create_pending_setup, decode_breakglass_token, elevate_breakglass_session, get_pending_config, update_active_portal_auth_config, validate_token_against_config, verify_breakglass_login, verify_emergency_path_token

key = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeJWKClient:
    def __init__(self, url: str):
        self.url = url

    def get_signing_key_from_jwt(self, token: str):
        return type("SigningKey", (), {"key": key.public_key()})()


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
    monkeypatch.setenv("ENTRA_TENANT_ID", "")
    monkeypatch.setenv("ENTRA_AUTHORITY", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def entra_configure_request(**overrides) -> PortalAuthConfigureRequest:
    data = {
        "idp_type": "ENTRA",
        "tenant_id": "tenant-1",
        "client_id": "client-1",
        "authority": "https://login.microsoftonline.com/tenant-1",
        "audience": "api://client-1",
        "breakglass_username": "recovery-admin",
        "breakglass_password": "a-strong-password-123",
    }
    data.update(overrides)
    return PortalAuthConfigureRequest(**data)


def real_token(**claim_overrides) -> str:
    claims = {"sub": "user-1", "tid": "tenant-1", "aud": "api://client-1", "iss": "https://login.microsoftonline.com/tenant-1/v2.0", "exp": datetime.now(timezone.utc) + timedelta(minutes=5)}
    claims.update(claim_overrides)
    return jwt.encode(claims, key, algorithm="RS256")


EMERGENCY_TOKEN = "test-emergency-token-1234567890"


async def _set_emergency_token(db: AsyncSession, username: str = "recovery-admin", token: str = EMERGENCY_TOKEN) -> None:
    """The CLI is the only real path that generates this token — tests set it directly on the account to exercise
    the login/verify code paths that check it, without depending on the CLI module."""
    account = (await db.execute(select(BreakGlassAccount).where(BreakGlassAccount.username == username))).scalars().first()
    account.emergency_path_token = token
    await db.commit()


@pytest.mark.asyncio
async def test_create_pending_setup_stores_a_pending_config_and_breakglass_account(db: AsyncSession):
    config = await create_pending_setup(db, entra_configure_request())
    assert config.is_active is False
    breakglass = (await db.execute(select(BreakGlassAccount))).scalars().first()
    assert breakglass is not None
    assert breakglass.is_active is False
    assert breakglass.username == "recovery-admin"


@pytest.mark.asyncio
async def test_create_pending_setup_cleans_up_a_previous_abandoned_attempt(db: AsyncSession):
    await create_pending_setup(db, entra_configure_request(tenant_id="first-attempt"))
    await create_pending_setup(db, entra_configure_request(tenant_id="second-attempt"))
    configs = (await db.execute(select(PortalAuthConfig))).scalars().all()
    breakglass_accounts = (await db.execute(select(BreakGlassAccount))).scalars().all()
    assert len(configs) == 1 and configs[0].tenant_id == "second-attempt"
    assert len(breakglass_accounts) == 1


@pytest.mark.asyncio
async def test_get_pending_config_rejects_an_already_active_config(db: AsyncSession):
    config = await create_pending_setup(db, entra_configure_request())
    config.is_active = True
    await db.commit()
    with pytest.raises(Exception):
        await get_pending_config(db, config.id)


@pytest.mark.asyncio
async def test_validate_rejects_an_entra_config_missing_authority(db: AsyncSession):
    config = await create_pending_setup(db, entra_configure_request(authority=None))
    with pytest.raises(Exception):
        await validate_token_against_config("irrelevant", config)


@pytest.mark.asyncio
async def test_validate_rejects_an_okta_config_missing_issuer(db: AsyncSession):
    config = await create_pending_setup(db, entra_configure_request(idp_type="OKTA", authority=None, issuer=None))
    with pytest.raises(Exception):
        await validate_token_against_config("irrelevant", config)


@pytest.mark.asyncio
async def test_validate_succeeds_against_a_real_looking_token_and_matching_config(db: AsyncSession, monkeypatch):
    monkeypatch.setattr("app.services.portal_auth.PyJWKClient", FakeJWKClient)
    config = await create_pending_setup(db, entra_configure_request())
    claims = await validate_token_against_config(real_token(), config)
    assert claims["tid"] == "tenant-1"


@pytest.mark.asyncio
async def test_validate_rejects_a_token_from_the_wrong_tenant(db: AsyncSession, monkeypatch):
    monkeypatch.setattr("app.services.portal_auth.PyJWKClient", FakeJWKClient)
    config = await create_pending_setup(db, entra_configure_request())
    with pytest.raises(Exception):
        await validate_token_against_config(real_token(tid="some-other-tenant"), config)


@pytest.mark.asyncio
async def test_activation_activates_config_and_breakglass_and_deletes_bootstrap_credential(db: AsyncSession):
    await ensure_bootstrap_credential(db)
    config = await create_pending_setup(db, entra_configure_request())

    activated = await activate_portal_auth_config(db, config.id, "req-1")
    assert activated.is_active is True

    breakglass = (await db.execute(select(BreakGlassAccount))).scalars().first()
    assert breakglass.is_active is True

    remaining_bootstrap = (await db.execute(select(BootstrapCredential))).scalars().all()
    assert remaining_bootstrap == []


@pytest.mark.asyncio
async def test_activation_deactivates_any_previously_active_config(db: AsyncSession):
    old = PortalAuthConfig(idp_type="ENTRA", is_active=True)
    db.add(old)
    await db.commit()
    config = await create_pending_setup(db, entra_configure_request())

    await activate_portal_auth_config(db, config.id, "req-2")

    await db.refresh(old)
    assert old.is_active is False


@pytest.mark.asyncio
async def test_breakglass_login_roundtrip(db: AsyncSession):
    config = await create_pending_setup(db, entra_configure_request())
    await activate_portal_auth_config(db, config.id, "req-3")
    await _set_emergency_token(db)

    token = await verify_breakglass_login(db, "recovery-admin", "a-strong-password-123", EMERGENCY_TOKEN, "req-4")
    result = await decode_breakglass_token(db, token)
    assert result is not None
    account, elevated = result
    assert account.username == "recovery-admin"
    assert elevated is False


@pytest.mark.asyncio
async def test_breakglass_login_fails_with_wrong_password(db: AsyncSession):
    config = await create_pending_setup(db, entra_configure_request())
    await activate_portal_auth_config(db, config.id, "req-5")
    await _set_emergency_token(db)
    with pytest.raises(Exception):
        await verify_breakglass_login(db, "recovery-admin", "totally-wrong", EMERGENCY_TOKEN, "req-6")


@pytest.mark.asyncio
async def test_breakglass_login_fails_with_wrong_emergency_token(db: AsyncSession):
    """The emergency-URL token is a real second factor — correct username/password alone is not enough."""
    config = await create_pending_setup(db, entra_configure_request())
    await activate_portal_auth_config(db, config.id, "req-5b")
    await _set_emergency_token(db)
    with pytest.raises(Exception):
        await verify_breakglass_login(db, "recovery-admin", "a-strong-password-123", "wrong-emergency-token", "req-5c")


@pytest.mark.asyncio
async def test_breakglass_login_fails_while_still_dormant_before_activation(db: AsyncSession):
    await create_pending_setup(db, entra_configure_request())
    with pytest.raises(Exception):
        await verify_breakglass_login(db, "recovery-admin", "a-strong-password-123", EMERGENCY_TOKEN, "req-7")


@pytest.mark.asyncio
async def test_decode_breakglass_token_rejects_garbage(db: AsyncSession):
    config = await create_pending_setup(db, entra_configure_request())
    await activate_portal_auth_config(db, config.id, "req-8")
    assert await decode_breakglass_token(db, "not-a-real-token") is None


@pytest.mark.asyncio
async def test_verify_emergency_path_token(db: AsyncSession):
    config = await create_pending_setup(db, entra_configure_request())
    await activate_portal_auth_config(db, config.id, "req-8b")
    assert await verify_emergency_path_token(db, "anything") is False
    await _set_emergency_token(db)
    assert await verify_emergency_path_token(db, EMERGENCY_TOKEN) is True
    assert await verify_emergency_path_token(db, "wrong") is False


@pytest.mark.asyncio
async def test_elevate_breakglass_session_marks_token_elevated(db: AsyncSession):
    config = await create_pending_setup(db, entra_configure_request())
    await activate_portal_auth_config(db, config.id, "req-8c")
    await _set_emergency_token(db)
    base_token = await verify_breakglass_login(db, "recovery-admin", "a-strong-password-123", EMERGENCY_TOKEN, "req-8d")

    result = await decode_breakglass_token(db, base_token)
    assert result[1] is False  # not elevated yet

    elevated_token = await elevate_breakglass_session(db, base_token, "req-8e")
    result = await decode_breakglass_token(db, elevated_token)
    assert result is not None
    account, elevated = result
    assert account.username == "recovery-admin"
    assert elevated is True


@pytest.mark.asyncio
async def test_update_active_portal_auth_config_takes_effect_immediately(db: AsyncSession):
    config = await create_pending_setup(db, entra_configure_request())
    await activate_portal_auth_config(db, config.id, "req-8f")

    from app.schemas.portal_auth import PortalAuthConfigUpdateRequest
    updated = await update_active_portal_auth_config(db, PortalAuthConfigUpdateRequest(idp_type="ENTRA", tenant_id="tenant-2", client_id="client-2", authority="https://login.microsoftonline.com/tenant-2"), "req-8g")
    assert updated.tenant_id == "tenant-2"
    assert updated.is_active is True


# --- Full HTTP-layer, end-to-end tests: proving require_authenticated_user's additive fallback for real ---

@pytest.mark.asyncio
async def test_a_real_protected_endpoint_accepts_a_valid_breakglass_session(db_override):
    """A fresh Break-Glass login lands in the restricted BreakGlassAdmin role by default — NOT full Admin."""
    async with db_override.factory() as session:
        config = await create_pending_setup(session, entra_configure_request())
        await activate_portal_auth_config(session, config.id, "req-9")
        await _set_emergency_token(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        login = await client.post("/api/v1/auth/breakglass-login", json={"username": "recovery-admin", "password": "a-strong-password-123", "emergency_token": EMERGENCY_TOKEN})
        assert login.status_code == 200
        token = login.json()["access_token"]

        me = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["roles"] == ["AccessPilot.BreakGlassAdmin"]


@pytest.mark.asyncio
async def test_breakglass_login_rejected_without_a_valid_emergency_token_over_http(db_override):
    async with db_override.factory() as session:
        config = await create_pending_setup(session, entra_configure_request())
        await activate_portal_auth_config(session, config.id, "req-9b")
        await _set_emergency_token(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        login = await client.post("/api/v1/auth/breakglass-login", json={"username": "recovery-admin", "password": "a-strong-password-123", "emergency_token": "wrong-token"})
    assert login.status_code == 401


@pytest.mark.asyncio
async def test_emergency_access_verify_endpoint_is_indistinguishable_from_a_real_404(db_override):
    async with db_override.factory() as session:
        config = await create_pending_setup(session, entra_configure_request())
        await activate_portal_auth_config(session, config.id, "req-9c")
        await _set_emergency_token(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        wrong = await client.get("/api/v1/auth/emergency-access/wrong-token/verify")
        nonexistent_route = await client.get("/api/v1/this-route-does-not-exist")
        correct = await client.get(f"/api/v1/auth/emergency-access/{EMERGENCY_TOKEN}/verify")
    assert wrong.status_code == 404
    assert nonexistent_route.status_code == 404
    wrong_body, nonexistent_body = wrong.json()["error"], nonexistent_route.json()["error"]
    del wrong_body["requestId"], nonexistent_body["requestId"]
    assert wrong_body == nonexistent_body
    assert correct.status_code == 200
    assert correct.json() == {"valid": True}


@pytest.mark.asyncio
async def test_breakglassadmin_cannot_reach_a_normal_admin_route(db_override):
    """Proves the restriction is real (require_permission denies it), not just missing frontend UI."""
    async with db_override.factory() as session:
        config = await create_pending_setup(session, entra_configure_request())
        await activate_portal_auth_config(session, config.id, "req-9d")
        await _set_emergency_token(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        login = await client.post("/api/v1/auth/breakglass-login", json={"username": "recovery-admin", "password": "a-strong-password-123", "emergency_token": EMERGENCY_TOKEN})
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        denied = await client.get("/api/v1/users", headers=headers)
        assert denied.status_code == 403

        config_get = await client.get("/api/v1/auth/portal-auth-config", headers=headers)
        assert config_get.status_code == 200

        elevate = await client.post("/api/v1/auth/breakglass-elevate", headers=headers)
        assert elevate.status_code == 200
        elevated_token = elevate.json()["access_token"]

        me = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {elevated_token}"})
        assert me.json()["roles"] == ["AccessPilot.Admin"]
        now_allowed = await client.get("/api/v1/users", headers={"Authorization": f"Bearer {elevated_token}"})
        assert now_allowed.status_code == 200


@pytest.mark.asyncio
async def test_a_real_protected_endpoint_still_rejects_garbage_tokens(db_override):
    """The fallback must never turn into an open door — a token that is neither a real IDP JWT nor a valid
    break-glass session must still be rejected exactly as before this feature existed."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/me", headers={"Authorization": "Bearer complete-nonsense"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_configure_and_activate_endpoints_end_to_end(db_override, monkeypatch):
    monkeypatch.setattr("app.services.portal_auth.PyJWKClient", FakeJWKClient)
    async with db_override.factory() as session:
        password = await ensure_bootstrap_credential(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        login = await client.post("/api/v1/setup/bootstrap-login", json={"username": "admin", "password": password})
        setup_token = login.json()["setup_token"]
        headers = {"Authorization": f"Bearer {setup_token}"}

        configured = await client.post("/api/v1/setup/configure", json={
            "idp_type": "ENTRA", "tenant_id": "tenant-1", "client_id": "client-1",
            "authority": "https://login.microsoftonline.com/tenant-1", "audience": "api://client-1",
            "breakglass_username": "recovery-admin", "breakglass_password": "a-strong-password-123",
        }, headers=headers)
        assert configured.status_code == 200
        config_id = configured.json()["id"]

        activated = await client.post("/api/v1/setup/activate", json={"config_id": config_id, "test_token": real_token()}, headers=headers)
    assert activated.status_code == 200
    assert activated.json() == {"activated": True, "idp_type": "ENTRA"}

    # And the setup session is now dead — setup has completed.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        status = await client.get("/api/v1/setup/status")
        assert status.json()["needs_setup"] is False
        reused = await client.post("/api/v1/setup/configure", json={"idp_type": "ENTRA", "breakglass_username": "x", "breakglass_password": "another-strong-password"}, headers=headers)
    assert reused.status_code == 401


# --- decode_access_token / require_authenticated_user's PortalAuthConfig fallback (primary-traffic path) ---

@pytest.mark.asyncio
async def test_a_token_valid_against_an_active_portal_auth_config_authenticates_normally(db_override, monkeypatch):
    """Once a PortalAuthConfig is active (a deployment that completed the new setup wizard), a real-looking token
    matching IT — not env-var Entra, which is unset in these tests — must authenticate a normal request."""
    monkeypatch.setattr("app.services.portal_auth.PyJWKClient", FakeJWKClient)
    async with db_override.factory() as session:
        config = await create_pending_setup(session, entra_configure_request())
        await activate_portal_auth_config(session, config.id, "req-10")

    token = real_token(roles=["AccessPilot.Admin"])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["roles"] == ["AccessPilot.Admin"]


@pytest.mark.asyncio
async def test_a_portal_auth_config_token_with_no_valid_role_is_still_rejected(db_override, monkeypatch):
    """The active-config fallback must enforce the same VALID_ROLES check as the primary path — a real, otherwise-
    valid token that was never assigned an AccessPilot role must still be rejected."""
    monkeypatch.setattr("app.services.portal_auth.PyJWKClient", FakeJWKClient)
    async with db_override.factory() as session:
        config = await create_pending_setup(session, entra_configure_request())
        await activate_portal_auth_config(session, config.id, "req-11")

    token = real_token(roles=[])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_break_glass_still_works_even_with_an_active_portal_auth_config_present(db_override, monkeypatch):
    """Ordering check: PortalAuthConfig is tried before break-glass, but break-glass must still work as its own
    independent fallback when the presented token doesn't match the active config at all."""
    monkeypatch.setattr("app.services.portal_auth.PyJWKClient", FakeJWKClient)
    async with db_override.factory() as session:
        config = await create_pending_setup(session, entra_configure_request())
        await activate_portal_auth_config(session, config.id, "req-12")
        await _set_emergency_token(session)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        login = await client.post("/api/v1/auth/breakglass-login", json={"username": "recovery-admin", "password": "a-strong-password-123", "emergency_token": EMERGENCY_TOKEN})
        token = login.json()["access_token"]
        response = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["roles"] == ["AccessPilot.BreakGlassAdmin"]


@pytest.mark.asyncio
async def test_public_portal_config_endpoint_reflects_active_config_non_secret_fields_only(db_override):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        before = await client.get("/api/v1/auth/portal-config")
    assert before.json() == {"configured": False, "idp_type": None, "tenant_id": None, "client_id": None, "authority": None, "scope": None, "redirect_uri": None}

    async with db_override.factory() as session:
        config = await create_pending_setup(session, entra_configure_request())
        await activate_portal_auth_config(session, config.id, "req-13")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        after = await client.get("/api/v1/auth/portal-config")
    body = after.json()
    assert body["configured"] is True
    assert body["tenant_id"] == "tenant-1"
    assert body["client_id"] == "client-1"
    assert "issuer" not in body and "audience" not in body


@pytest.mark.asyncio
async def test_a_completely_invalid_token_still_gets_a_clean_401_with_no_active_config_or_breakglass(db_override):
    """No regression to the baseline case: with nothing new configured at all, a bad token behaves exactly as
    before this whole feature existed."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/me", headers={"Authorization": "Bearer garbage"})
    assert response.status_code == 401
