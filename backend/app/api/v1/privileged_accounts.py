from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AccessPilotError, request_id as get_request_id
from app.db.session import get_db
from app.models import User
from app.schemas.audit import AuditLogResponse
from app.schemas.privileged_accounts import PrivilegedAccountActivitySummary, PrivilegedAccountDecision, PrivilegedAccountPolicyResponse, PrivilegedAccountPolicyUpdate, PrivilegedAccountRequestCreate, PrivilegedAccountRequestResponse
from app.security.auth import AuthenticatedUser, require_authenticated_user, require_permission
from app.services import privileged_accounts as service
from app.services.assignments import _resolve_internal_user_id

router = APIRouter(prefix="/privileged-accounts", tags=["privileged-accounts"])
policy_read = require_permission("POLICY_READ")
policy_update = require_permission("POLICY_UPDATE")


async def _hydrate_policy(db: AsyncSession, policy) -> PrivilegedAccountPolicyResponse:
    approver = await db.get(User, policy.default_approver_id) if policy.default_approver_id else None
    return PrivilegedAccountPolicyResponse(account_type=policy.account_type, default_approver_id=policy.default_approver_id, default_approver_display_name=approver.display_name if approver else None, approval_required=policy.default_approver_id is not None)


async def _hydrate_request(db: AsyncSession, request_row) -> PrivilegedAccountRequestResponse:
    requester = await db.get(User, request_row.requester_id)
    approver = await db.get(User, request_row.approver_id) if request_row.approver_id else None
    provisioned = await db.get(User, request_row.provisioned_user_id) if request_row.provisioned_user_id else None
    return PrivilegedAccountRequestResponse(
        id=request_row.id, requester_id=request_row.requester_id, requester_display_name=requester.display_name if requester else None,
        account_type=request_row.account_type, status=request_row.status, approver_id=request_row.approver_id,
        approver_display_name=approver.display_name if approver else None, justification=request_row.justification,
        provisioned_user_id=request_row.provisioned_user_id, provisioned_user_display_name=provisioned.display_name if provisioned else None,
        failure_reason=request_row.failure_reason, created_at=request_row.created_at, decided_at=request_row.decided_at,
    )


@router.get("/policy/{account_type}", response_model=PrivilegedAccountPolicyResponse)
async def get_policy(account_type: str, _: AuthenticatedUser = Depends(policy_read), db: AsyncSession = Depends(get_db)):
    policy = await service.get_or_create_policy(db, account_type)
    return await _hydrate_policy(db, policy)


@router.patch("/policy/{account_type}", response_model=PrivilegedAccountPolicyResponse)
async def update_policy(account_type: str, data: PrivilegedAccountPolicyUpdate, request: Request, _: AuthenticatedUser = Depends(policy_update), db: AsyncSession = Depends(get_db)):
    policy = await service.update_policy(db, account_type, data.default_approver_id, get_request_id(request))
    return await _hydrate_policy(db, policy)


@router.post("/requests", response_model=PrivilegedAccountRequestResponse, status_code=201)
async def create_request(data: PrivilegedAccountRequestCreate, request: Request, actor: AuthenticatedUser = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    requester_id = await _resolve_internal_user_id(db, actor.directory_object_id)
    if requester_id is None:
        raise AccessPilotError("USER_NOT_FOUND", "Your own directory record could not be found.", 404)
    request_row = await service.create_request(db, requester_id, data.account_type, data.justification, get_request_id(request))
    return await _hydrate_request(db, request_row)


@router.get("/requests/mine", response_model=list[PrivilegedAccountRequestResponse])
async def list_my_requests(actor: AuthenticatedUser = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    requester_id = await _resolve_internal_user_id(db, actor.directory_object_id)
    if requester_id is None:
        return []
    rows = await service.list_requests(db, requester_id)
    return [await _hydrate_request(db, row) for row in rows]


@router.get("/requests", response_model=list[PrivilegedAccountRequestResponse])
async def list_all_requests(_: AuthenticatedUser = Depends(policy_read), db: AsyncSession = Depends(get_db)):
    rows = await service.list_requests(db)
    return [await _hydrate_request(db, row) for row in rows]


@router.post("/requests/{request_id}/approve", response_model=PrivilegedAccountRequestResponse)
async def approve_request(request_id: UUID, request: Request, actor: AuthenticatedUser = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    request_row = await service.approve_request(db, request_id, actor.directory_object_id, actor.roles, get_request_id(request))
    return await _hydrate_request(db, request_row)


@router.post("/requests/{request_id}/reject", response_model=PrivilegedAccountRequestResponse)
async def reject_request(request_id: UUID, data: PrivilegedAccountDecision, request: Request, actor: AuthenticatedUser = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    request_row = await service.reject_request(db, request_id, actor.directory_object_id, actor.roles, data.justification, get_request_id(request))
    return await _hydrate_request(db, request_row)


@router.get("/activity", response_model=list[PrivilegedAccountActivitySummary])
async def list_activity(_: AuthenticatedUser = Depends(policy_read), db: AsyncSession = Depends(get_db)):
    """Admin monitoring tab: one row per PU/TU account — who it's linked to, when created, its last real Entra
    sign-in (best-effort, see PrivilegedAccountActivitySummary.sign_in_data_available), and how much AccessPilot
    itself has recorded about it. POLICY_READ-gated, same as every other privileged-account admin view."""
    return await service.list_privileged_account_activity(db)


@router.get("/{account_id}/timeline", response_model=list[AuditLogResponse])
async def account_timeline(account_id: UUID, _: AuthenticatedUser = Depends(policy_read), db: AsyncSession = Depends(get_db)):
    """The full, chronological AccessPilot-recorded history for one PU/TU account — creation, every enable/
    disable, and any manual access grant/activation/revocation (see get_privileged_account_timeline)."""
    entries = await service.get_privileged_account_timeline(db, account_id)
    return [
        AuditLogResponse(
            id=entry.id, timestamp=entry.timestamp, actor_user_id=entry.actor_user_id, actor_display_name=hydrated["actor_display_name"],
            action=entry.action, target_type=entry.target_type, target_id=entry.target_id, provider_id=entry.provider_id,
            provider_name=hydrated["provider_name"], request_id=entry.request_id, result=entry.result, metadata=entry.metadata_json,
            target_user_display_name=hydrated["target_user_display_name"], target_user_email=hydrated["target_user_email"],
        )
        for entry, hydrated in entries
    ]
