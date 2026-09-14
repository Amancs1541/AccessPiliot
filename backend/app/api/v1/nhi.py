from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.audit import AuditLogResponse
from app.schemas.nhi import NhiOwnerAssignRequest, NhiPermissionResponse, NhiRiskExceptionCreateRequest, NhiRiskExceptionResponse, NhiSummaryResponse, NhiTypeUpdateRequest, NonHumanIdentityResponse
from app.security.auth import AuthenticatedUser, require_permission
from app.services import nhi as nhi_service
from app.services.assignments import _resolve_internal_user_id

router = APIRouter(prefix="/nhi", tags=["nhi"])
nhi_read = require_permission("NHI_READ")
nhi_manage = require_permission("NHI_MANAGE")


@router.get("", response_model=list[NonHumanIdentityResponse])
async def list_nhi(_: AuthenticatedUser = Depends(nhi_read), db: AsyncSession = Depends(get_db)):
    return await nhi_service.list_non_human_identities(db)


@router.get("/summary", response_model=NhiSummaryResponse)
async def get_summary(_: AuthenticatedUser = Depends(nhi_read), db: AsyncSession = Depends(get_db)):
    return await nhi_service.get_summary(db)


@router.get("/{application_id}", response_model=NonHumanIdentityResponse)
async def get_nhi(application_id: UUID, _: AuthenticatedUser = Depends(nhi_read), db: AsyncSession = Depends(get_db)):
    return await nhi_service.get_non_human_identity(db, application_id)


@router.get("/{application_id}/risk-exceptions", response_model=list[NhiRiskExceptionResponse])
async def get_risk_exceptions(application_id: UUID, _: AuthenticatedUser = Depends(nhi_read), db: AsyncSession = Depends(get_db)):
    return await nhi_service.list_risk_exceptions(db, application_id)


@router.patch("/{application_id}/type", response_model=NonHumanIdentityResponse)
async def update_type(application_id: UUID, data: NhiTypeUpdateRequest, request: Request, actor: AuthenticatedUser = Depends(nhi_manage), db: AsyncSession = Depends(get_db)):
    actor_id = await _resolve_internal_user_id(db, actor.directory_object_id)
    await nhi_service.set_nhi_type(db, application_id, data.nhi_type, actor_id, request.state.request_id)
    return await nhi_service.get_non_human_identity(db, application_id)


@router.get("/{application_id}/activity", response_model=list[AuditLogResponse])
async def get_activity(application_id: UUID, _: AuthenticatedUser = Depends(nhi_read), db: AsyncSession = Depends(get_db)):
    entries = await nhi_service.get_activity(db, application_id)
    return [
        AuditLogResponse(
            id=entry.id, timestamp=entry.timestamp, actor_user_id=entry.actor_user_id, actor_display_name=hydrated["actor_display_name"],
            action=entry.action, target_type=entry.target_type, target_id=entry.target_id,
            target_user_display_name=hydrated["target_user_display_name"], target_user_email=hydrated["target_user_email"],
            provider_id=entry.provider_id, provider_name=hydrated["provider_name"], request_id=entry.request_id, result=entry.result, metadata=entry.metadata_json,
        )
        for entry, hydrated in entries
    ]


@router.get("/{application_id}/permissions", response_model=list[NhiPermissionResponse])
async def get_permissions(application_id: UUID, _: AuthenticatedUser = Depends(nhi_read), db: AsyncSession = Depends(get_db)):
    return await nhi_service.get_permissions(db, application_id)


@router.post("/{application_id}/disable", response_model=NonHumanIdentityResponse)
async def disable_identity(application_id: UUID, request: Request, actor: AuthenticatedUser = Depends(nhi_manage), db: AsyncSession = Depends(get_db)):
    actor_id = await _resolve_internal_user_id(db, actor.directory_object_id)
    await nhi_service.disable_identity(db, application_id, actor_id, request.state.request_id)
    return await nhi_service.get_non_human_identity(db, application_id)


@router.post("/{application_id}/enable", response_model=NonHumanIdentityResponse)
async def enable_identity(application_id: UUID, request: Request, actor: AuthenticatedUser = Depends(nhi_manage), db: AsyncSession = Depends(get_db)):
    actor_id = await _resolve_internal_user_id(db, actor.directory_object_id)
    await nhi_service.enable_identity(db, application_id, actor_id, request.state.request_id)
    return await nhi_service.get_non_human_identity(db, application_id)


@router.post("/{application_id}/owners", response_model=NonHumanIdentityResponse, status_code=201)
async def add_owner(application_id: UUID, data: NhiOwnerAssignRequest, request: Request, actor: AuthenticatedUser = Depends(nhi_manage), db: AsyncSession = Depends(get_db)):
    actor_id = await _resolve_internal_user_id(db, actor.directory_object_id)
    await nhi_service.assign_owner(db, application_id, data.user_id, actor_id, request.state.request_id)
    return await nhi_service.get_non_human_identity(db, application_id)


@router.delete("/{application_id}/owners/{user_id}", response_model=NonHumanIdentityResponse)
async def remove_owner(application_id: UUID, user_id: UUID, request: Request, actor: AuthenticatedUser = Depends(nhi_manage), db: AsyncSession = Depends(get_db)):
    actor_id = await _resolve_internal_user_id(db, actor.directory_object_id)
    await nhi_service.remove_owner(db, application_id, user_id, actor_id, request.state.request_id)
    return await nhi_service.get_non_human_identity(db, application_id)


@router.post("/{application_id}/risk-exceptions", response_model=NhiRiskExceptionResponse, status_code=201)
async def create_risk_exception(application_id: UUID, data: NhiRiskExceptionCreateRequest, request: Request, actor: AuthenticatedUser = Depends(nhi_manage), db: AsyncSession = Depends(get_db)):
    actor_id = await _resolve_internal_user_id(db, actor.directory_object_id)
    return await nhi_service.create_risk_exception(db, application_id, data.risk_type, data.justification, data.expires_at, actor_id, request.state.request_id)
