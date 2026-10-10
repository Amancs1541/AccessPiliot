from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from uuid import UUID

from app.schemas.lifecycle import LeaverOverviewResponse, LeaverRequestResponse, LeaverStartRequest, ReenableDecision, ReenableRequestCreate, ReenableRequestResponse, LeaverPolicyCreate, LeaverPolicyResponse, LeaverPolicyUpdate, PersonLifecycleResponse, PersonLifecycleUpdate, ScheduledDeletionResponse, ScheduledLeaverResponse, LifecycleEventResponse, LifecycleSettingsResponse, LifecycleSettingsUpdate, MoveScheduleCreate, PendingMoveResponse
from app.security.auth import AuthenticatedUser, require_permission
from app.services import leaver_followup
from app.services import lifecycle as lifecycle_service
from app.security.auth import PERMISSIONS

router = APIRouter(prefix="/lifecycle", tags=["lifecycle"])
lifecycle_read = require_permission("LIFECYCLE_READ")
lifecycle_manage = require_permission("LIFECYCLE_MANAGE")
leaver_read = require_permission("LEAVER_READ")
leaver_manage = require_permission("LEAVER_MANAGE")
identity_account_manage = require_permission("IDENTITY_ACCOUNT_MANAGE")
me_read = require_permission("ME_READ")


def _is_admin(actor: AuthenticatedUser) -> bool:
    return any("LEAVER_MANAGE" in PERMISSIONS[role] for role in actor.roles)


@router.post("/people/{user_id}/reenable-request", response_model=ReenableRequestResponse, status_code=201)
async def create_reenable_request(user_id: UUID, data: ReenableRequestCreate, request: Request, actor: AuthenticatedUser = Depends(identity_account_manage), db: AsyncSession = Depends(get_db)):
    return await leaver_followup.create_request(db, user_id, data, actor.directory_object_id, request.state.request_id)


@router.get("/reenable-requests", response_model=list[ReenableRequestResponse])
async def list_reenable_requests(status: str | None = None, actor: AuthenticatedUser = Depends(leaver_read), db: AsyncSession = Depends(get_db)):
    return await leaver_followup.list_requests(db, status.upper() if status else None, actor.directory_object_id, _is_admin(actor))


@router.get("/reenable-requests/mine", response_model=list[ReenableRequestResponse])
async def my_reenable_requests(actor: AuthenticatedUser = Depends(me_read), db: AsyncSession = Depends(get_db)):
    return await leaver_followup.list_requests(db, "PENDING", actor.directory_object_id, _is_admin(actor), only_mine=True)


@router.post("/reenable-requests/{request_row_id}/decision", response_model=ReenableRequestResponse)
async def decide_reenable_request(request_row_id: UUID, data: ReenableDecision, request: Request, actor: AuthenticatedUser = Depends(me_read), db: AsyncSession = Depends(get_db)):
    return await leaver_followup.decide(db, request_row_id, data.approve, data.note, actor.directory_object_id, _is_admin(actor), request.state.request_id)


@router.get("/events", response_model=list[LifecycleEventResponse])
async def list_events(event_type: str | None = None, limit: int = 200, _: AuthenticatedUser = Depends(lifecycle_read), db: AsyncSession = Depends(get_db)):
    return await lifecycle_service.list_events(db, event_type.upper() if event_type else None, max(1, min(limit, 500)))


@router.get("/moves", response_model=list[PendingMoveResponse])
async def list_moves(status: str | None = None, _: AuthenticatedUser = Depends(lifecycle_read), db: AsyncSession = Depends(get_db)):
    return await lifecycle_service.list_moves(db, status.upper() if status else None)


@router.post("/moves", response_model=PendingMoveResponse, status_code=201)
async def schedule_move(data: MoveScheduleCreate, request: Request, actor: AuthenticatedUser = Depends(lifecycle_manage), db: AsyncSession = Depends(get_db)):
    move = await lifecycle_service.schedule_move(db, data.user_id, (data.department or "").strip() or None, (data.job_title or "").strip() or None, data.effective_at, "ADMIN_EDIT", actor.directory_object_id, request.state.request_id)
    return next(row for row in await lifecycle_service.list_moves(db) if row.id == move.id)


@router.delete("/moves/{move_id}", response_model=PendingMoveResponse)
async def cancel_move(move_id: UUID, request: Request, actor: AuthenticatedUser = Depends(lifecycle_manage), db: AsyncSession = Depends(get_db)):
    move = await lifecycle_service.cancel_move(db, move_id, actor.directory_object_id, request.state.request_id)
    return next(row for row in await lifecycle_service.list_moves(db) if row.id == move.id)


@router.get("/leaver-policies", response_model=list[LeaverPolicyResponse])
async def list_leaver_policies(_: AuthenticatedUser = Depends(leaver_read), db: AsyncSession = Depends(get_db)):
    return await lifecycle_service.list_leaver_policies(db)


@router.post("/leaver-policies", response_model=LeaverPolicyResponse, status_code=201)
async def create_leaver_policy(data: LeaverPolicyCreate, request: Request, _: AuthenticatedUser = Depends(leaver_manage), db: AsyncSession = Depends(get_db)):
    return await lifecycle_service.create_leaver_policy(db, data, request.state.request_id)


@router.patch("/leaver-policies/{policy_id}", response_model=LeaverPolicyResponse)
async def update_leaver_policy(policy_id: UUID, data: LeaverPolicyUpdate, request: Request, _: AuthenticatedUser = Depends(leaver_manage), db: AsyncSession = Depends(get_db)):
    return await lifecycle_service.update_leaver_policy(db, policy_id, data, request.state.request_id)


@router.delete("/leaver-policies/{policy_id}", status_code=204)
async def delete_leaver_policy(policy_id: UUID, request: Request, _: AuthenticatedUser = Depends(leaver_manage), db: AsyncSession = Depends(get_db)):
    await lifecycle_service.delete_leaver_policy(db, policy_id, request.state.request_id)


@router.get("/leavers/scheduled", response_model=list[ScheduledLeaverResponse])
async def scheduled_leavers(_: AuthenticatedUser = Depends(leaver_read), db: AsyncSession = Depends(get_db)):
    return await lifecycle_service.list_scheduled_leavers(db)


@router.get("/deletions/scheduled", response_model=list[ScheduledDeletionResponse])
async def scheduled_deletions(_: AuthenticatedUser = Depends(leaver_read), db: AsyncSession = Depends(get_db)):
    return await leaver_followup.list_scheduled_deletions(db)


@router.patch("/people/{user_id}", response_model=PersonLifecycleResponse)
async def update_person(user_id: UUID, data: PersonLifecycleUpdate, request: Request, actor: AuthenticatedUser = Depends(leaver_manage), db: AsyncSession = Depends(get_db)):
    return await lifecycle_service.update_person_lifecycle(db, user_id, data, actor.directory_object_id, request.state.request_id)


@router.get("/people/{user_id}/leaver-overview", response_model=LeaverOverviewResponse)
async def leaver_overview(user_id: UUID, actor: AuthenticatedUser = Depends(leaver_read), db: AsyncSession = Depends(get_db)):
    """One-stop status for the User Detail Leaver tab: any pending request (decidable right there) and recent activity."""
    return await leaver_followup.get_leaver_overview(db, user_id, actor.directory_object_id, _is_admin(actor))


@router.post("/people/{user_id}/leave-now", response_model=LeaverRequestResponse, status_code=201)
async def leave_now(user_id: UUID, data: LeaverStartRequest, request: Request, actor: AuthenticatedUser = Depends(leaver_manage), db: AsyncSession = Depends(get_db)):
    """Manual Start leaver process now: justification -> accounts disabled -> manager approval -> leaver process."""
    return await leaver_followup.start_leaver_request(db, user_id, data.justification, actor.directory_object_id, request.state.request_id)


@router.get("/leaver-requests", response_model=list[LeaverRequestResponse])
async def list_leaver_requests(status: str | None = None, actor: AuthenticatedUser = Depends(leaver_read), db: AsyncSession = Depends(get_db)):
    return await leaver_followup.list_leaver_requests(db, status.upper() if status else None, actor.directory_object_id, _is_admin(actor))


@router.get("/leaver-requests/mine", response_model=list[LeaverRequestResponse])
async def my_leaver_requests(actor: AuthenticatedUser = Depends(me_read), db: AsyncSession = Depends(get_db)):
    return await leaver_followup.list_leaver_requests(db, "PENDING", actor.directory_object_id, _is_admin(actor), only_mine=True)


@router.post("/leaver-requests/{request_row_id}/decision", response_model=LeaverRequestResponse)
async def decide_leaver_request(request_row_id: UUID, data: ReenableDecision, request: Request, actor: AuthenticatedUser = Depends(me_read), db: AsyncSession = Depends(get_db)):
    return await leaver_followup.decide_leaver_request(db, request_row_id, data.approve, data.note, actor.directory_object_id, _is_admin(actor), request.state.request_id)


@router.get("/settings", response_model=LifecycleSettingsResponse)
async def get_settings(_: AuthenticatedUser = Depends(lifecycle_read), db: AsyncSession = Depends(get_db)):
    return await lifecycle_service.settings_response(db)


@router.put("/settings", response_model=LifecycleSettingsResponse)
async def put_settings(data: LifecycleSettingsUpdate, request: Request, _: AuthenticatedUser = Depends(lifecycle_manage), db: AsyncSession = Depends(get_db)):
    return await lifecycle_service.update_settings(db, data, request.state.request_id)


@router.get("/report.pdf")
async def jml_report_pdf(_: AuthenticatedUser = Depends(lifecycle_read), db: AsyncSession = Depends(get_db)):
    """One PDF of every joiner / mover / leaver process and the items each touched."""
    from datetime import datetime, timezone

    from fastapi.responses import Response

    from app.services.jml_report import build_report

    pdf = await build_report(db)
    name = f"jml-report-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}.pdf"
    return Response(content=pdf, media_type="application/pdf", headers={"Content-Disposition": f'attachment; filename="{name}"'})
