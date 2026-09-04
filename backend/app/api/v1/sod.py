from __future__ import annotations

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AccessPilotError
from app.db.session import get_db
from app.schemas.audit import AuditLogResponse
from app.schemas.sod import SodCheckRequest, SodCheckResponse, SodExceptionCreate, SodExceptionRequestCreate, SodExceptionRequestDeny, SodExceptionRequestGrant, SodExceptionRequestResponse, SodExceptionResponse, SodNotificationResponse, SodNotificationSettingsResponse, SodNotificationSettingsUpdateRequest, SodPolicyCreate, SodPolicyResponse, SodPolicyUpdate, SodViolation
from app.security.auth import AuthenticatedUser, require_authenticated_user, require_permission
from app.services import sod as sod_service
from app.services.assignments import _resolve_internal_user_id

router = APIRouter(prefix="/sod", tags=["sod"])
sod_read = require_permission("SOD_READ")
sod_manage = require_permission("SOD_MANAGE")
assignment_manage = require_permission("ASSIGNMENT_CREATE")


@router.get("/policies", response_model=list[SodPolicyResponse])
async def list_policies(_: AuthenticatedUser = Depends(sod_read), db: AsyncSession = Depends(get_db)):
    return [await sod_service.to_policy_response(db, policy) for policy in await sod_service.list_sod_policies(db)]


@router.post("/policies", response_model=SodPolicyResponse, status_code=201)
async def create_policy(data: SodPolicyCreate, request: Request, actor: AuthenticatedUser = Depends(sod_manage), db: AsyncSession = Depends(get_db)):
    actor_id = await _resolve_internal_user_id(db, actor.directory_object_id)
    policy = await sod_service.create_sod_policy(db, data, actor_id, request.state.request_id)
    return await sod_service.to_policy_response(db, policy)


@router.patch("/policies/{policy_id}", response_model=SodPolicyResponse)
async def update_policy(policy_id: UUID, data: SodPolicyUpdate, request: Request, actor: AuthenticatedUser = Depends(sod_manage), db: AsyncSession = Depends(get_db)):
    actor_id = await _resolve_internal_user_id(db, actor.directory_object_id)
    policy = await sod_service.update_sod_policy(db, policy_id, data, actor_id, request.state.request_id)
    return await sod_service.to_policy_response(db, policy)


@router.delete("/policies/{policy_id}")
async def delete_policy(policy_id: UUID, request: Request, actor: AuthenticatedUser = Depends(sod_manage), db: AsyncSession = Depends(get_db)):
    """Deletes the policy if it has no real history; otherwise disables it instead (kept for exception/
    notification audit history) and returns the disabled policy — mirrors DELETE /packages/{id}'s identical
    "delete if never used, else archive" shape. A true delete returns a simple confirmation instead."""
    actor_id = await _resolve_internal_user_id(db, actor.directory_object_id)
    result = await sod_service.delete_sod_policy(db, policy_id, actor_id, request.state.request_id)
    return await sod_service.to_policy_response(db, result) if result is not None else {"deleted": True, "id": str(policy_id)}


@router.get("/violations", response_model=list[SodViolation])
async def list_violations(policy_id: Optional[UUID] = None, _: AuthenticatedUser = Depends(sod_read), db: AsyncSession = Depends(get_db)):
    """Live-computed on every call — never stored/materialized, so it can never drift out of sync with real
    access state."""
    return await sod_service.get_sod_violations(db, policy_id)


@router.post("/check", response_model=SodCheckResponse)
async def check_conflicts(data: SodCheckRequest, actor: AuthenticatedUser = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    """UX-only pre-submit warning — the real, unbypassable gate is server-side inside
    create_assignment/activate_assignment/the activation worker, not this endpoint. Checking a user_id other
    than your own is Admin-only (mirrors _authorize_activation's own "target user or Admin" rule elsewhere in
    this app) — otherwise any authenticated user could probe another user's real entitlements/conflicts by
    guessing resource ids, which this endpoint would otherwise happily confirm or deny."""
    caller_id = await _resolve_internal_user_id(db, actor.directory_object_id)
    if data.user_id is not None and data.user_id != caller_id and "AccessPilot.Admin" not in actor.roles:
        raise AccessPilotError("ACCESS_DENIED", "Only an administrator can check another user's Separation-of-Duties conflicts.", 403)
    user_id = data.user_id or caller_id
    if user_id is None:
        return SodCheckResponse(conflicts=[])
    conflicts = await sod_service.check_sod_conflicts(db, user_id, data.resource_type, data.resource_id, data.app_role_external_id)
    return SodCheckResponse(conflicts=[await sod_service.to_policy_response(db, policy) for policy in conflicts])


@router.get("/activity", response_model=list[AuditLogResponse])
async def list_activity(_: AuthenticatedUser = Depends(sod_read), db: AsyncSession = Depends(get_db)):
    """SoD-relevant audit history — rule changes, roster changes, and blocked/overridden grants — gated by
    SOD_READ (not AUDIT_READ, which SoDAdmin doesn't hold) so a plain SoDAdmin can see this without needing
    the general Admin-only Audit Logs page."""
    return [
        AuditLogResponse(
            id=entry.id, timestamp=entry.timestamp, actor_user_id=entry.actor_user_id, actor_display_name=hydrated["actor_display_name"],
            action=entry.action, target_type=entry.target_type, target_id=entry.target_id, provider_id=entry.provider_id,
            provider_name=None, request_id=entry.request_id, result=entry.result, metadata=entry.metadata_json,
            target_user_display_name=hydrated["target_user_display_name"], target_user_email=hydrated["target_user_email"],
        )
        for entry, hydrated in await sod_service.get_sod_activity(db)
    ]


@router.get("/exceptions", response_model=list[SodExceptionResponse])
async def list_exceptions(_: AuthenticatedUser = Depends(sod_read), db: AsyncSession = Depends(get_db)):
    return await sod_service.list_sod_exceptions(db)


@router.post("/exceptions", response_model=SodExceptionResponse, status_code=201)
async def create_exception(data: SodExceptionCreate, request: Request, actor: AuthenticatedUser = Depends(sod_manage), db: AsyncSession = Depends(get_db)):
    """SOD_MANAGE-gated (SoDAdmin only) — same reasoning as editing a rule directly: an Admin granting exceptions
    would be an equally effective way to defeat the engine as an Admin editing the rule itself, which is already
    forbidden."""
    actor_id = await _resolve_internal_user_id(db, actor.directory_object_id)
    exception = await sod_service.create_sod_exception(db, data, actor_id, request.state.request_id)
    return await sod_service.hydrate_sod_exception(db, exception)


@router.delete("/exceptions/{exception_id}", status_code=204)
async def revoke_exception(exception_id: UUID, request: Request, actor: AuthenticatedUser = Depends(sod_manage), db: AsyncSession = Depends(get_db)):
    actor_id = await _resolve_internal_user_id(db, actor.directory_object_id)
    await sod_service.revoke_sod_exception(db, exception_id, actor_id, request.state.request_id)
    return None


@router.get("/notification-settings", response_model=SodNotificationSettingsResponse)
async def get_notification_settings(_: AuthenticatedUser = Depends(sod_read), db: AsyncSession = Depends(get_db)):
    return await sod_service.get_sod_notification_settings(db)


@router.patch("/notification-settings", response_model=SodNotificationSettingsResponse)
async def update_notification_settings(data: SodNotificationSettingsUpdateRequest, _: AuthenticatedUser = Depends(sod_manage), db: AsyncSession = Depends(get_db)):
    """SOD_MANAGE-gated (SoDAdmin), same as every other lever that changes how/whether the engine surfaces a
    conflict — an Admin muting their own violation notifications would be the same class of self-dealing this
    engine's whole permission split exists to prevent."""
    return await sod_service.update_sod_notification_settings(db, data)


@router.get("/notifications", response_model=list[SodNotificationResponse])
async def list_notifications(_: AuthenticatedUser = Depends(sod_read), db: AsyncSession = Depends(get_db)):
    """Reconciles against current reality on every call (see reconcile_sod_notifications) — this is the one
    place that happens, deliberately not on every violations/Dashboard read, to avoid doubling the cost of the
    per-user Graph scan for ROLE/APPLICATION rules."""
    return await sod_service.list_sod_notifications(db)


@router.post("/notifications/{notification_id}/read", status_code=204)
async def mark_notification_read(notification_id: UUID, _: AuthenticatedUser = Depends(sod_read), db: AsyncSession = Depends(get_db)):
    await sod_service.mark_sod_notification_read(db, notification_id)
    return None


@router.post("/notifications/read-all", status_code=204)
async def mark_all_notifications_read(_: AuthenticatedUser = Depends(sod_read), db: AsyncSession = Depends(get_db)):
    await sod_service.mark_all_sod_notifications_read(db)
    return None


@router.get("/exception-requests", response_model=list[SodExceptionRequestResponse])
async def list_exception_requests(_: AuthenticatedUser = Depends(sod_read), db: AsyncSession = Depends(get_db)):
    return await sod_service.list_sod_exception_requests(db)


@router.post("/exception-requests", response_model=SodExceptionRequestResponse, status_code=201)
async def create_exception_request(data: SodExceptionRequestCreate, request: Request, actor: AuthenticatedUser = Depends(assignment_manage), db: AsyncSession = Depends(get_db)):
    """ASSIGNMENT_CREATE-gated: only someone who can create assignments in the first place has a reason to
    request an exception for one that got blocked. Notifies the SoDAdmin roster; does not touch the original
    blocked assignment attempt — the admin must retry it manually once (if) an exception is granted."""
    actor_id = await _resolve_internal_user_id(db, actor.directory_object_id)
    exception_request = await sod_service.create_sod_exception_request(db, data, actor_id, request.state.request_id)
    return await sod_service.hydrate_sod_exception_request(db, exception_request)


@router.post("/exception-requests/{exception_request_id}/grant", response_model=SodExceptionRequestResponse)
async def grant_exception_request(exception_request_id: UUID, data: SodExceptionRequestGrant, request: Request, actor: AuthenticatedUser = Depends(sod_manage), db: AsyncSession = Depends(get_db)):
    actor_id = await _resolve_internal_user_id(db, actor.directory_object_id)
    exception_request = await sod_service.grant_sod_exception_request(db, exception_request_id, data, actor_id, request.state.request_id)
    return await sod_service.hydrate_sod_exception_request(db, exception_request)


@router.post("/exception-requests/{exception_request_id}/deny", response_model=SodExceptionRequestResponse)
async def deny_exception_request(exception_request_id: UUID, data: SodExceptionRequestDeny, request: Request, actor: AuthenticatedUser = Depends(sod_manage), db: AsyncSession = Depends(get_db)):
    actor_id = await _resolve_internal_user_id(db, actor.directory_object_id)
    exception_request = await sod_service.deny_sod_exception_request(db, exception_request_id, data, actor_id, request.state.request_id)
    return await sod_service.hydrate_sod_exception_request(db, exception_request)


