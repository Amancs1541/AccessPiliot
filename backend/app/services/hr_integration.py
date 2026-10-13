from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AccessPilotError
from app.models import HrIntegrationSettings, HrSyncTriggerToken
from app.schemas.hr_integration import HrIntegrationSettingsResponse, HrIntegrationSettingsUpdate, HrSyncResult, HrSyncTriggerTokenSummary
from app.security.credential_encryption import CredentialEncryptionError, decrypt_credential, encrypt_credential
from app.services.audit import record_audit

logger = logging.getLogger("accesspilot.hr_integration")

HR_SYNC_ACTOR = "system:hr-integration"

# Maps a real IdentityProvider.type to the directory label the standalone HR app's push-back endpoint expects
# (see HR_APP_V2_API_INTEGRATION_PLAN.md §1.4) — CSV/OKTA/etc. have no push-back target and are skipped.
PROVIDER_TYPE_TO_DIRECTORY = {"ENTRA": "ENTRA", "ACTIVE_DIRECTORY": "AD"}


async def get_hr_integration_settings(session: AsyncSession) -> HrIntegrationSettings:
    """Singleton — created with its defaults (disabled, nothing configured) on first read."""
    settings = (await session.execute(select(HrIntegrationSettings))).scalars().first()
    if settings is None:
        settings = HrIntegrationSettings()
        session.add(settings)
        await session.commit()
        await session.refresh(settings)
    return settings


def settings_response(settings: HrIntegrationSettings) -> HrIntegrationSettingsResponse:
    return HrIntegrationSettingsResponse(
        enabled=settings.enabled, auth_method=settings.auth_method, base_url=settings.base_url, username=settings.username,
        credential_configured=bool(settings.password_encrypted), api_key_configured=bool(settings.api_key_encrypted),
        sync_interval_minutes=settings.sync_interval_minutes,
        last_sync_at=settings.last_sync_at, last_sync_status=settings.last_sync_status, last_sync_error=settings.last_sync_error,
    )


async def update_settings(session: AsyncSession, data: HrIntegrationSettingsUpdate, request_id: str) -> HrIntegrationSettingsResponse:
    settings = await get_hr_integration_settings(session)
    if data.base_url is not None:
        settings.base_url = data.base_url.strip().rstrip("/") or None
    if data.auth_method is not None:
        settings.auth_method = data.auth_method
    if data.username is not None:
        settings.username = data.username.strip() or None
    if data.password:
        settings.password_encrypted = encrypt_credential(data.password)
    if data.api_key:
        settings.api_key_encrypted = encrypt_credential(data.api_key)
    if data.clear_sync_interval_minutes:
        settings.sync_interval_minutes = None
    elif data.sync_interval_minutes is not None:
        settings.sync_interval_minutes = data.sync_interval_minutes
    if data.enabled is not None:
        if data.enabled:
            if settings.auth_method == "API_KEY" and not (settings.base_url and settings.api_key_encrypted):
                raise AccessPilotError("VALIDATION_ERROR", "Set the HR app's URL and API key before enabling the sync.", 422)
            if settings.auth_method == "SESSION" and not (settings.base_url and settings.username and settings.password_encrypted):
                raise AccessPilotError("VALIDATION_ERROR", "Set the HR app's URL, username and password before enabling the sync.", 422)
        settings.enabled = data.enabled
    await record_audit(session, action="HR_INTEGRATION_SETTINGS_UPDATED", target_type="HR_INTEGRATION_SETTINGS", target_id=settings.id, request_id=request_id, metadata={"enabled": settings.enabled, "auth_method": settings.auth_method, "base_url": settings.base_url, "sync_interval_minutes": settings.sync_interval_minutes})
    await session.commit()
    await session.refresh(settings)
    return settings_response(settings)


async def _fetch_employee_csv(settings: HrIntegrationSettings, *, http_client: httpx.AsyncClient | None = None) -> str:
    """Fetches the HR app's CSV export, authenticated one of two ways depending on `auth_method`:
    API_KEY — a single `GET .../api/v2/export.csv` with `Authorization: Bearer <key>`, no login step (see
    HR_APP_V2_API_INTEGRATION_PLAN.md); SESSION — logs in with the stored admin username/password (the original,
    still-supported method) and fetches `.../api/export.csv` using the resulting session cookie, held only for
    this one call. `http_client` lets tests inject an httpx.MockTransport-backed client, mirroring
    GraphClient/OktaClient."""
    if settings.auth_method == "API_KEY":
        if not (settings.base_url and settings.api_key_encrypted):
            raise AccessPilotError("HR_INTEGRATION_NOT_CONFIGURED", "The HR connection is not fully configured.", 422)
        try:
            api_key = decrypt_credential(settings.api_key_encrypted)
        except CredentialEncryptionError as exc:
            raise AccessPilotError("HR_INTEGRATION_CREDENTIAL_ERROR", str(exc), 500) from exc

        owns_client = http_client is None
        client = http_client or httpx.AsyncClient(timeout=20.0)
        try:
            try:
                export = await client.get(f"{settings.base_url}/api/v2/export.csv", headers={"Authorization": f"Bearer {api_key}"})
            except httpx.HTTPError as exc:
                raise AccessPilotError("HR_INTEGRATION_UNREACHABLE", f"Could not reach the HR app: {exc}", 503) from exc
            if export.status_code >= 400:
                raise AccessPilotError("HR_INTEGRATION_EXPORT_FAILED", f"The HR app's export failed (HTTP {export.status_code}).", 502)
            return export.text
        finally:
            if owns_client:
                await client.aclose()

    if not (settings.base_url and settings.username and settings.password_encrypted):
        raise AccessPilotError("HR_INTEGRATION_NOT_CONFIGURED", "The HR connection is not fully configured.", 422)
    try:
        password = decrypt_credential(settings.password_encrypted)
    except CredentialEncryptionError as exc:
        raise AccessPilotError("HR_INTEGRATION_CREDENTIAL_ERROR", str(exc), 500) from exc

    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=20.0)
    try:
        try:
            login = await client.post(f"{settings.base_url}/api/login", json={"username": settings.username, "password": password})
        except httpx.HTTPError as exc:
            raise AccessPilotError("HR_INTEGRATION_UNREACHABLE", f"Could not reach the HR app: {exc}", 503) from exc
        if login.status_code >= 400:
            raise AccessPilotError("HR_INTEGRATION_LOGIN_FAILED", f"The HR app rejected the stored login (HTTP {login.status_code}).", 502)

        try:
            export = await client.get(f"{settings.base_url}/api/export.csv")
        except httpx.HTTPError as exc:
            raise AccessPilotError("HR_INTEGRATION_UNREACHABLE", f"Could not reach the HR app: {exc}", 503) from exc
        if export.status_code >= 400:
            raise AccessPilotError("HR_INTEGRATION_EXPORT_FAILED", f"The HR app's export failed (HTTP {export.status_code}).", 502)
        return export.text
    finally:
        if owns_client:
            await client.aclose()


async def _push_provisioning_status(settings: HrIntegrationSettings, employee_id: str, directory: str, event: str, *, http_client: httpx.AsyncClient | None = None) -> None:
    """Best-effort: tells the HR app what AccessPilot just did for one employee. Never raises — a push failure
    only ever logs, since it can't affect whether the sync that triggered it actually succeeded."""
    if not (settings.base_url and settings.api_key_encrypted):
        return
    try:
        api_key = decrypt_credential(settings.api_key_encrypted)
    except CredentialEncryptionError:
        return
    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=10.0)
    try:
        await client.post(
            f"{settings.base_url}/api/v2/employees/{employee_id}/accesspilot-status",
            json={"directory": directory, "event": event, "occurred_at": datetime.now(timezone.utc).isoformat()},
            headers={"Authorization": f"Bearer {api_key}"},
        )
    except httpx.HTTPError as exc:
        logger.info("HR status push-back failed for %s (%s): %s", employee_id, event, exc)
    finally:
        if owns_client:
            await client.aclose()


async def _push_sync_outcomes(session: AsyncSession, settings: HrIntegrationSettings, onboarding_import_id, *, http_client: httpx.AsyncClient | None = None) -> None:
    """After a successful API_KEY-authenticated sync, reports back which CREATE/DISABLE rows resulted in a real
    directory account — the HR app has no status endpoint to push to under SESSION auth, so this only ever runs
    for API_KEY connections. CREATE -> ACCOUNT_CREATED, DISABLE -> LEAVER_PROCESSED (the leaver process already
    ran as part of commit_import for that row); UPDATE/NO_CHANGE carry no new provisioning event for v1."""
    from app.models import IdentityProvider, OnboardingImportRecord, User

    records = (await session.execute(select(OnboardingImportRecord).where(OnboardingImportRecord.import_id == onboarding_import_id, OnboardingImportRecord.action.in_(("CREATE", "DISABLE"))))).scalars().all()
    for record in records:
        user = (await session.execute(select(User).where(User.employee_id == record.employee_id))).scalar_one_or_none()
        if user is None:
            continue
        provider = await session.get(IdentityProvider, user.provider_id)
        directory = PROVIDER_TYPE_TO_DIRECTORY.get(provider.type) if provider else None
        if directory is None:
            continue
        event = "ACCOUNT_CREATED" if record.action == "CREATE" else "LEAVER_PROCESSED"
        await _push_provisioning_status(settings, record.employee_id, directory, event, http_client=http_client)


async def run_hr_sync(session: AsyncSession, request_id: str, *, http_client: httpx.AsyncClient | None = None) -> HrSyncResult:
    """Fetches the HR app's current CSV export and feeds it through the existing, unchanged onboarding engine
    (parse_and_validate_csv -> commit_import). Leaver is not handled specially here — a TERMINATED row (driven by
    the HR app's own leaverDate column) already runs AccessPilot's existing leaver automation, via commit_import,
    exactly like a manual CSV upload would."""
    from app.services.onboarding import commit_import, parse_and_validate_csv

    settings = await get_hr_integration_settings(session)
    try:
        csv_text = await _fetch_employee_csv(settings, http_client=http_client)
        onboarding_import = await parse_and_validate_csv(session, f"hr-sync-{datetime.now(timezone.utc).isoformat()}.csv", csv_text, HR_SYNC_ACTOR, request_id)
        if onboarding_import.status == "VALIDATED":
            onboarding_import = await commit_import(session, onboarding_import.id, HR_SYNC_ACTOR, request_id)
            result = HrSyncResult(status="SUCCESS", import_id=str(onboarding_import.id))
            if settings.auth_method == "API_KEY":
                await _push_sync_outcomes(session, settings, onboarding_import.id, http_client=http_client)
        else:
            error = (onboarding_import.error_summary or {}).get("error", "Validation failed.")
            result = HrSyncResult(status="FAILED", error=error, import_id=str(onboarding_import.id))
    except AccessPilotError as exc:
        logger.warning("HR sync failed: %s", exc)
        result = HrSyncResult(status="FAILED", error=f"{exc.code}: {exc.message}")

    settings = await get_hr_integration_settings(session)
    settings.last_sync_at = datetime.now(timezone.utc)
    settings.last_sync_status = result.status
    settings.last_sync_error = result.error
    await record_audit(session, action="HR_INTEGRATION_SYNC_RUN", target_type="HR_INTEGRATION_SETTINGS", target_id=settings.id, request_id=request_id, metadata={"status": result.status, "error": result.error, "import_id": result.import_id})
    await session.commit()
    return result


async def sweep_hr_sync(session: AsyncSession) -> bool:
    """Worker entry point: runs a sync when enabled and its interval has elapsed since the last run (or it has
    never run). Returns whether a sync ran."""
    settings = await get_hr_integration_settings(session)
    if not settings.enabled or not settings.sync_interval_minutes:
        return False
    if settings.last_sync_at is not None:
        last = settings.last_sync_at if settings.last_sync_at.tzinfo else settings.last_sync_at.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) < last + timedelta(minutes=settings.sync_interval_minutes):
            return False
    await run_hr_sync(session, f"hr-sync-sweep-{datetime.now(timezone.utc).isoformat()}")
    return True


# ---------------------------------------------------------------- the HR sync trigger token (see HrSyncTriggerToken)

def _hash_trigger_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


async def generate_sync_trigger_token(session: AsyncSession, actor_user_id: Optional[UUID], request_id: str) -> str:
    """Admin-only (gated by ONBOARDING_MANAGE on the route, same as every other action here). Creates a new
    token, revoking any previous active one — one active token at a time, mirroring the HR app's own API-key UX.
    Returned once, in plaintext; only its SHA-256 hash is ever stored."""
    existing = (await session.scalars(select(HrSyncTriggerToken).where(HrSyncTriggerToken.revoked_at.is_(None)))).all()
    for token in existing:
        token.revoked_at = datetime.now(timezone.utc)
    raw = secrets.token_urlsafe(32)
    new_token = HrSyncTriggerToken(token_hash=_hash_trigger_token(raw), created_by=actor_user_id)
    session.add(new_token)
    await session.flush()
    await record_audit(session, action="HR_SYNC_TRIGGER_TOKEN_GENERATED", target_type="HR_SYNC_TRIGGER_TOKEN", target_id=new_token.id, actor_user_id=actor_user_id, request_id=request_id)
    await session.commit()
    return raw


async def revoke_sync_trigger_token(session: AsyncSession, actor_user_id: Optional[UUID], request_id: str) -> bool:
    existing = (await session.scalars(select(HrSyncTriggerToken).where(HrSyncTriggerToken.revoked_at.is_(None)))).all()
    changed = False
    for token in existing:
        token.revoked_at = datetime.now(timezone.utc)
        changed = True
    if changed:
        await record_audit(session, action="HR_SYNC_TRIGGER_TOKEN_REVOKED", target_type="HR_SYNC_TRIGGER_TOKEN", target_id=None, actor_user_id=actor_user_id, request_id=request_id)
        await session.commit()
    return changed


async def sync_trigger_token_summary(session: AsyncSession) -> HrSyncTriggerTokenSummary:
    token = (await session.scalars(select(HrSyncTriggerToken).where(HrSyncTriggerToken.revoked_at.is_(None)))).first()
    if token is None:
        return HrSyncTriggerTokenSummary(configured=False)
    return HrSyncTriggerTokenSummary(configured=True, created_at=token.created_at, last_used_at=token.last_used_at)


async def verify_sync_trigger_token(session: AsyncSession, raw_token: str) -> bool:
    """Hash-then-lookup, the same pattern the HR app's own API key check uses — not vulnerable to a meaningful
    timing attack since the hash itself is the lookup key, not a byte-by-byte comparison target."""
    if not raw_token:
        return False
    token = (await session.scalars(select(HrSyncTriggerToken).where(HrSyncTriggerToken.token_hash == _hash_trigger_token(raw_token), HrSyncTriggerToken.revoked_at.is_(None)))).first()
    if token is None:
        return False
    token.last_used_at = datetime.now(timezone.utc)
    await session.commit()
    return True
