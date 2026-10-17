from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models import DynamicGroup, DynamicGroupTarget, Group, IdentityProvider
from app.schemas.dynamic_groups import DynamicGroupCreate, DynamicGroupResponse, DynamicGroupTargetResponse, DynamicGroupTargetSyncRequest, DynamicGroupUpdate
from app.security.auth import AuthenticatedUser, require_permission
from app.services import dynamic_groups as dynamic_groups_service
from app.services.assignments import _resolve_internal_user_id

router = APIRouter(prefix="/dynamic-groups", tags=["dynamic-groups"])
dynamic_groups_read = require_permission("GROUP_READ")
dynamic_groups_manage = require_permission("GROUP_MANAGE")


async def _hydrate_target(db: AsyncSession, target: DynamicGroupTarget) -> DynamicGroupTargetResponse:
    provider = await db.get(IdentityProvider, target.provider_id)
    group = await db.get(Group, target.group_id) if target.group_id else None
    return DynamicGroupTargetResponse(
        id=target.id, provider_id=target.provider_id, provider_name=provider.name if provider else "Unknown", provider_type=provider.type if provider else "UNKNOWN",
        sync_mode=target.sync_mode, group_id=target.group_id, group_name=group.name if group else None, group_external_id=group.external_id if group else None,
    )


async def _hydrate(db: AsyncSession, row: DynamicGroup) -> DynamicGroupResponse:
    targets = await dynamic_groups_service.list_targets(db, row.id)
    conditions = [{"field": c["field"], "operator": c["operator"], "value": c["value"]} for c in (row.conditions_json or [])]
    return DynamicGroupResponse(
        id=row.id, name=row.name, description=row.description, group_label=row.group_label,
        rule={"operator": row.conditions_operator, "conditions": conditions},
        status=row.status, created_at=row.created_at, updated_at=row.updated_at,
        targets=[await _hydrate_target(db, target) for target in targets],
    )


@router.get("", response_model=list[DynamicGroupResponse])
async def list_dynamic_groups(_: AuthenticatedUser = Depends(dynamic_groups_read), db: AsyncSession = Depends(get_db)):
    return [await _hydrate(db, row) for row in await dynamic_groups_service.list_dynamic_groups(db)]


@router.post("", response_model=DynamicGroupResponse, status_code=201)
async def create_dynamic_group(data: DynamicGroupCreate, request: Request, actor: AuthenticatedUser = Depends(dynamic_groups_manage), db: AsyncSession = Depends(get_db)):
    actor_id = await _resolve_internal_user_id(db, actor.directory_object_id)
    row = await dynamic_groups_service.create_dynamic_group(db, data.name.strip(), data.description, data.group_label, data.rule, actor_id, request.state.request_id)
    return await _hydrate(db, row)


@router.get("/{dynamic_group_id}", response_model=DynamicGroupResponse)
async def get_dynamic_group(dynamic_group_id: UUID, _: AuthenticatedUser = Depends(dynamic_groups_read), db: AsyncSession = Depends(get_db)):
    row = await dynamic_groups_service.get_dynamic_group(db, dynamic_group_id)
    return await _hydrate(db, row)


@router.patch("/{dynamic_group_id}", response_model=DynamicGroupResponse)
async def update_dynamic_group(dynamic_group_id: UUID, data: DynamicGroupUpdate, request: Request, actor: AuthenticatedUser = Depends(dynamic_groups_manage), db: AsyncSession = Depends(get_db)):
    actor_id = await _resolve_internal_user_id(db, actor.directory_object_id)
    row = await dynamic_groups_service.update_dynamic_group(
        db, dynamic_group_id, name=data.name, description=data.description, group_label=data.group_label, rule=data.rule, status=data.status,
        actor_subject=actor.directory_object_id, actor_id=actor_id, request_id=request.state.request_id,
    )
    return await _hydrate(db, row)


@router.delete("/{dynamic_group_id}", status_code=204)
async def delete_dynamic_group(dynamic_group_id: UUID, request: Request, actor: AuthenticatedUser = Depends(dynamic_groups_manage), db: AsyncSession = Depends(get_db)):
    actor_id = await _resolve_internal_user_id(db, actor.directory_object_id)
    await dynamic_groups_service.delete_dynamic_group(db, dynamic_group_id, actor_id, request.state.request_id)


@router.post("/{dynamic_group_id}/targets/{provider_id}/sync", response_model=DynamicGroupTargetResponse)
async def set_target_sync(dynamic_group_id: UUID, provider_id: UUID, data: DynamicGroupTargetSyncRequest, request: Request, actor: AuthenticatedUser = Depends(dynamic_groups_manage), db: AsyncSession = Depends(get_db)):
    target = await dynamic_groups_service.set_target_sync_mode(db, dynamic_group_id, provider_id, data.mode, actor.directory_object_id, request.state.request_id)
    return await _hydrate_target(db, target)
