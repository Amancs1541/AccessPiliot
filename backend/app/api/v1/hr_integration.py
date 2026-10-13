from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AccessPilotError, request_id as get_request_id
from app.db.session import get_db
from app.schemas.hr_integration import HrIntegrationSettingsResponse, HrIntegrationSettingsUpdate, HrSyncResult, HrSyncTriggerTokenResponse, HrSyncTriggerTokenSummary
from app.security.auth import AuthenticatedUser, bearer, require_authenticated_user, require_permission
from app.services import hr_integration as hr_integration_service

router = APIRouter(prefix="/hr-integration", tags=["hr-integration"])
hr_integration_read = require_permission("ONBOARDING_READ")
hr_integration_manage = require_permission("ONBOARDING_MANAGE")


@router.get("/settings", response_model=HrIntegrationSettingsResponse)
async def get_settings(_: AuthenticatedUser = Depends(hr_integration_read), db: AsyncSession = Depends(get_db)):
    settings = await hr_integration_service.get_hr_integration_settings(db)
    return hr_integration_service.settings_response(settings)


@router.put("/settings", response_model=HrIntegrationSettingsResponse)
async def update_settings(data: HrIntegrationSettingsUpdate, request: Request, _: AuthenticatedUser = Depends(hr_integration_manage), db: AsyncSession = Depends(get_db)):
    return await hr_integration_service.update_settings(db, data, get_request_id(request))


async def sync_trigger_auth(request: Request, credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer), db: AsyncSession = Depends(get_db)) -> str:
    """Who may call POST /sync-now: a real ONBOARDING_MANAGE admin — today's unchanged, primary path, tried first
    exactly as everywhere else in the app — OR a narrowly-scoped HrSyncTriggerToken (see
    app.services.hr_integration.generate_sync_trigger_token), whose ONLY capability anywhere in AccessPilot is
    this one action. Mirrors require_authenticated_user's own additive-fallback shape for PortalAuthConfig/
    break-glass: the trigger token is tried ONLY once the primary admin path has already failed, and that path's
    own error is re-raised if the trigger token doesn't match either — never a new/different failure mode."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AccessPilotError("AUTHENTICATION_REQUIRED", "Authentication is required.", 401)
    try:
        user = await require_authenticated_user(request, credentials, db)
        await hr_integration_manage(user=user)
        return user.directory_object_id
    except AccessPilotError as primary_error:
        if not await hr_integration_service.verify_sync_trigger_token(db, credentials.credentials):
            raise primary_error
        return "system:hr-sync-trigger"


@router.post("/sync-now", response_model=HrSyncResult)
async def sync_now(request: Request, _: str = Depends(sync_trigger_auth), db: AsyncSession = Depends(get_db)):
    return await hr_integration_service.run_hr_sync(db, get_request_id(request))


@router.get("/sync-trigger-token", response_model=HrSyncTriggerTokenSummary)
async def get_sync_trigger_token(_: AuthenticatedUser = Depends(hr_integration_read), db: AsyncSession = Depends(get_db)):
    return await hr_integration_service.sync_trigger_token_summary(db)


@router.post("/sync-trigger-token", response_model=HrSyncTriggerTokenResponse)
async def create_sync_trigger_token(request: Request, actor: AuthenticatedUser = Depends(hr_integration_manage), db: AsyncSession = Depends(get_db)):
    """Generates (replacing any previous one) the token the HR app's own 'Sync' button presents to POST
    /sync-now in place of a normal admin login — returned once, never stored in plaintext."""
    from app.services.assignments import _resolve_internal_user_id

    actor_id = await _resolve_internal_user_id(db, actor.directory_object_id)
    token = await hr_integration_service.generate_sync_trigger_token(db, actor_id, get_request_id(request))
    return HrSyncTriggerTokenResponse(token=token)


@router.delete("/sync-trigger-token")
async def delete_sync_trigger_token(request: Request, actor: AuthenticatedUser = Depends(hr_integration_manage), db: AsyncSession = Depends(get_db)):
    from app.services.assignments import _resolve_internal_user_id

    actor_id = await _resolve_internal_user_id(db, actor.directory_object_id)
    await hr_integration_service.revoke_sync_trigger_token(db, actor_id, get_request_id(request))
    return {"ok": True}
