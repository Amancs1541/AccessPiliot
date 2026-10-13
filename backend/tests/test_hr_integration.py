from __future__ import annotations

import httpx
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
from app.models import HrIntegrationSettings, IdentityProvider, OnboardingImport, OnboardingImportRecord, User
from app.schemas.hr_integration import HrIntegrationSettingsUpdate
from app.services import hr_integration as hr_integration_service

VALID_CSV = (
    "employeeId,firstName,lastName,email,department,status\n"
    "EMP2001,Alicia,Stone,alicia.stone@company.com,Finance,ACTIVE\n"
)

PARTIAL_ERROR_CSV = (
    "employeeId,firstName,lastName,email,department,status\n"
    "EMP2002,Daniel,Meyer,daniel.meyer@company.com,Engineering,ACTIVE\n"
    "EMP2003,,Shah,priya.shah@company.com,Engineering,ACTIVE\n"
)


class TestSession:
    def __init__(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def db():
    database = TestSession()
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield database
    await database.engine.dispose()


def mock_client(login_status: int = 200, csv_body: str = VALID_CSV) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/api/login"):
            return httpx.Response(login_status, json={"ok": login_status < 400})
        if request.url.path.endswith("/api/export.csv"):
            return httpx.Response(200, text=csv_body)
        return httpx.Response(404)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_password_is_encrypted_and_never_returned(db, monkeypatch):
    monkeypatch.setenv("PROVIDER_CREDENTIAL_KEY", Fernet.generate_key().decode())
    async with db.factory() as session:
        response = await hr_integration_service.update_settings(
            session,
            HrIntegrationSettingsUpdate(base_url="http://localhost:3000", username="admin", password="admin123"),
            "req-1",
        )
        assert response.credential_configured is True
        assert not hasattr(response, "password")
        row = (await session.execute(select(HrIntegrationSettings))).scalars().first()
        assert row.password_encrypted is not None
        assert row.password_encrypted != "admin123"


@pytest.mark.asyncio
async def test_enabling_without_full_configuration_is_rejected(db, monkeypatch):
    monkeypatch.setenv("PROVIDER_CREDENTIAL_KEY", Fernet.generate_key().decode())
    from app.core.errors import AccessPilotError

    async with db.factory() as session:
        with pytest.raises(AccessPilotError):
            await hr_integration_service.update_settings(session, HrIntegrationSettingsUpdate(enabled=True), "req-1")


@pytest.mark.asyncio
async def test_successful_sync_commits_via_the_existing_onboarding_engine(db, monkeypatch):
    monkeypatch.setenv("PROVIDER_CREDENTIAL_KEY", Fernet.generate_key().decode())
    async with db.factory() as session:
        await hr_integration_service.update_settings(session, HrIntegrationSettingsUpdate(base_url="http://localhost:3000", username="admin", password="admin123"), "req-1")
        async with mock_client() as client:
            result = await hr_integration_service.run_hr_sync(session, "req-2", http_client=client)
        assert result.status == "SUCCESS"

        users = (await session.execute(select(User).where(User.employee_id == "EMP2001"))).scalars().all()
        assert len(users) == 1
        assert users[0].display_name == "Alicia Stone"

        settings = (await session.execute(select(HrIntegrationSettings))).scalars().first()
        assert settings.last_sync_status == "SUCCESS"
        assert settings.last_sync_at is not None


@pytest.mark.asyncio
async def test_login_failure_is_recorded_without_crashing(db, monkeypatch):
    monkeypatch.setenv("PROVIDER_CREDENTIAL_KEY", Fernet.generate_key().decode())
    async with db.factory() as session:
        await hr_integration_service.update_settings(session, HrIntegrationSettingsUpdate(base_url="http://localhost:3000", username="admin", password="wrong"), "req-1")
        async with mock_client(login_status=401) as client:
            result = await hr_integration_service.run_hr_sync(session, "req-2", http_client=client)
        assert result.status == "FAILED"
        assert "HR_INTEGRATION_LOGIN_FAILED" in result.error

        settings = (await session.execute(select(HrIntegrationSettings))).scalars().first()
        assert settings.last_sync_status == "FAILED"
        assert settings.last_sync_error is not None


@pytest.mark.asyncio
async def test_a_partial_row_error_csv_still_commits_the_good_rows(db, monkeypatch):
    monkeypatch.setenv("PROVIDER_CREDENTIAL_KEY", Fernet.generate_key().decode())
    async with db.factory() as session:
        await hr_integration_service.update_settings(session, HrIntegrationSettingsUpdate(base_url="http://localhost:3000", username="admin", password="admin123"), "req-1")
        async with mock_client(csv_body=PARTIAL_ERROR_CSV) as client:
            result = await hr_integration_service.run_hr_sync(session, "req-2", http_client=client)
        assert result.status == "SUCCESS"

        good = (await session.execute(select(User).where(User.employee_id == "EMP2002"))).scalars().first()
        bad = (await session.execute(select(User).where(User.employee_id == "EMP2003"))).scalars().first()
        assert good is not None
        assert bad is None


@pytest.mark.asyncio
async def test_sweep_does_nothing_when_disabled_or_no_interval_set(db, monkeypatch):
    monkeypatch.setenv("PROVIDER_CREDENTIAL_KEY", Fernet.generate_key().decode())
    async with db.factory() as session:
        await hr_integration_service.update_settings(session, HrIntegrationSettingsUpdate(base_url="http://localhost:3000", username="admin", password="admin123"), "req-1")
        ran = await hr_integration_service.sweep_hr_sync(session)
        assert ran is False


def mock_api_key_client(export_status: int = 200, csv_body: str = VALID_CSV, push_calls: list | None = None) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("Authorization") == "Bearer the-real-key"
        if request.url.path.endswith("/api/login"):
            raise AssertionError("API_KEY auth must never call the session login endpoint")
        if request.url.path.endswith("/export.csv"):
            return httpx.Response(export_status, text=csv_body)
        if "accesspilot-status" in request.url.path:
            if push_calls is not None:
                import json
                push_calls.append((request.url.path, json.loads(request.content)))
            return httpx.Response(201, json={"ok": True})
        return httpx.Response(404)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_enabling_api_key_method_without_a_key_is_rejected(db, monkeypatch):
    monkeypatch.setenv("PROVIDER_CREDENTIAL_KEY", Fernet.generate_key().decode())
    from app.core.errors import AccessPilotError

    async with db.factory() as session:
        await hr_integration_service.update_settings(session, HrIntegrationSettingsUpdate(base_url="http://localhost:4000", auth_method="API_KEY"), "req-1")
        with pytest.raises(AccessPilotError):
            await hr_integration_service.update_settings(session, HrIntegrationSettingsUpdate(enabled=True), "req-2")


@pytest.mark.asyncio
async def test_api_key_sync_fetches_v2_export_and_never_logs_in(db, monkeypatch):
    monkeypatch.setenv("PROVIDER_CREDENTIAL_KEY", Fernet.generate_key().decode())
    async with db.factory() as session:
        await hr_integration_service.update_settings(session, HrIntegrationSettingsUpdate(base_url="http://localhost:4000", auth_method="API_KEY", api_key="the-real-key"), "req-1")
        async with mock_api_key_client() as client:
            result = await hr_integration_service.run_hr_sync(session, "req-2", http_client=client)
        assert result.status == "SUCCESS"

        settings = (await session.execute(select(HrIntegrationSettings))).scalars().first()
        assert settings.auth_method == "API_KEY"
        assert settings.api_key_encrypted is not None


@pytest.mark.asyncio
async def test_api_key_credential_is_never_returned(db, monkeypatch):
    monkeypatch.setenv("PROVIDER_CREDENTIAL_KEY", Fernet.generate_key().decode())
    async with db.factory() as session:
        response = await hr_integration_service.update_settings(session, HrIntegrationSettingsUpdate(base_url="http://localhost:4000", auth_method="API_KEY", api_key="the-real-key"), "req-1")
        assert response.api_key_configured is True
        assert not hasattr(response, "api_key")


@pytest.mark.asyncio
async def test_a_created_real_account_pushes_status_back_to_the_hr_app(db, monkeypatch):
    """CREATE rows that land on a real directory account (ENTRA/ACTIVE_DIRECTORY, not the CSV fallback provider)
    get an ACCOUNT_CREATED push after a successful API_KEY sync."""
    monkeypatch.setenv("PROVIDER_CREDENTIAL_KEY", Fernet.generate_key().decode())
    async with db.factory() as session:
        provider = IdentityProvider(name="Entra DEV", type="ENTRA", status="CONNECTED", tenant_id="t")
        session.add(provider)
        await session.flush()
        user = User(provider_id=provider.id, external_id="ext-1", email="alicia.stone@company.com", display_name="Alicia Stone", status="ACTIVE", employee_id="EMP2001")
        session.add(user)
        onboarding_import = OnboardingImport(provider_id=provider.id, filename="hr-sync.csv", status="COMMITTED", total_records=1, created_count=1)
        session.add(onboarding_import)
        await session.flush()
        session.add(OnboardingImportRecord(import_id=onboarding_import.id, row_number=1, employee_id="EMP2001", action="CREATE"))
        await session.commit()

        settings = await hr_integration_service.get_hr_integration_settings(session)
        await hr_integration_service.update_settings(session, HrIntegrationSettingsUpdate(base_url="http://localhost:4000", auth_method="API_KEY", api_key="the-real-key"), "req-1")
        settings = await hr_integration_service.get_hr_integration_settings(session)

        push_calls: list = []
        async with mock_api_key_client(push_calls=push_calls) as client:
            await hr_integration_service._push_sync_outcomes(session, settings, onboarding_import.id, http_client=client)
        assert len(push_calls) == 1
        path, body = push_calls[0]
        assert path.endswith("/api/v2/employees/EMP2001/accesspilot-status")
        assert body["directory"] == "ENTRA"
        assert body["event"] == "ACCOUNT_CREATED"


@pytest.mark.asyncio
async def test_a_push_back_failure_never_raises(db, monkeypatch):
    """The push-back is best-effort: the HR app being unreachable for this call must never propagate as an
    exception, since it can't affect whether the sync that triggered it actually succeeded."""
    monkeypatch.setenv("PROVIDER_CREDENTIAL_KEY", Fernet.generate_key().decode())

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("HR app unreachable for the status push-back")

    async with db.factory() as session:
        provider = IdentityProvider(name="Entra DEV", type="ENTRA", status="CONNECTED", tenant_id="t")
        session.add(provider)
        await session.flush()
        user = User(provider_id=provider.id, external_id="ext-1", email="alicia.stone@company.com", display_name="Alicia Stone", status="ACTIVE", employee_id="EMP2001")
        session.add(user)
        onboarding_import = OnboardingImport(provider_id=provider.id, filename="hr-sync.csv", status="COMMITTED", total_records=1, created_count=1)
        session.add(onboarding_import)
        await session.flush()
        session.add(OnboardingImportRecord(import_id=onboarding_import.id, row_number=1, employee_id="EMP2001", action="CREATE"))
        await session.commit()

        await hr_integration_service.update_settings(session, HrIntegrationSettingsUpdate(base_url="http://localhost:4000", auth_method="API_KEY", api_key="the-real-key"), "req-1")
        settings = await hr_integration_service.get_hr_integration_settings(session)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await hr_integration_service._push_sync_outcomes(session, settings, onboarding_import.id, http_client=client)
        # No assertion needed beyond "did not raise" — the whole point of this test.


# ---------------------------------------------------------------- HR sync trigger token

@pytest.mark.asyncio
async def test_generated_trigger_token_verifies_and_is_never_stored_in_plaintext(db, monkeypatch):
    monkeypatch.setenv("PROVIDER_CREDENTIAL_KEY", Fernet.generate_key().decode())
    async with db.factory() as session:
        raw = await hr_integration_service.generate_sync_trigger_token(session, None, "req-1")
        assert raw

        from app.models import HrSyncTriggerToken
        row = (await session.execute(select(HrSyncTriggerToken))).scalars().first()
        assert row.token_hash != raw

        assert await hr_integration_service.verify_sync_trigger_token(session, raw) is True
        assert await hr_integration_service.verify_sync_trigger_token(session, "not-the-real-token") is False
        assert await hr_integration_service.verify_sync_trigger_token(session, "") is False


@pytest.mark.asyncio
async def test_generating_a_new_trigger_token_revokes_the_previous_one(db, monkeypatch):
    monkeypatch.setenv("PROVIDER_CREDENTIAL_KEY", Fernet.generate_key().decode())
    async with db.factory() as session:
        first = await hr_integration_service.generate_sync_trigger_token(session, None, "req-1")
        second = await hr_integration_service.generate_sync_trigger_token(session, None, "req-2")
        assert await hr_integration_service.verify_sync_trigger_token(session, first) is False
        assert await hr_integration_service.verify_sync_trigger_token(session, second) is True


@pytest.mark.asyncio
async def test_revoking_the_trigger_token_invalidates_it(db, monkeypatch):
    monkeypatch.setenv("PROVIDER_CREDENTIAL_KEY", Fernet.generate_key().decode())
    async with db.factory() as session:
        raw = await hr_integration_service.generate_sync_trigger_token(session, None, "req-1")
        revoked = await hr_integration_service.revoke_sync_trigger_token(session, None, "req-2")
        assert revoked is True
        assert await hr_integration_service.verify_sync_trigger_token(session, raw) is False
        assert await hr_integration_service.revoke_sync_trigger_token(session, None, "req-3") is False


@pytest.mark.asyncio
async def test_trigger_token_summary_reflects_configured_state(db, monkeypatch):
    monkeypatch.setenv("PROVIDER_CREDENTIAL_KEY", Fernet.generate_key().decode())
    async with db.factory() as session:
        before = await hr_integration_service.sync_trigger_token_summary(session)
        assert before.configured is False

        raw = await hr_integration_service.generate_sync_trigger_token(session, None, "req-1")
        after = await hr_integration_service.sync_trigger_token_summary(session)
        assert after.configured is True
        assert after.created_at is not None
        assert after.last_used_at is None

        await hr_integration_service.verify_sync_trigger_token(session, raw)
        used = await hr_integration_service.sync_trigger_token_summary(session)
        assert used.last_used_at is not None

