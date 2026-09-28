from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import request_id as get_request_id
from app.db.session import get_db
from app.schemas.departments import DepartmentCreate, DepartmentResponse
from app.schemas.policies import BirthrightEvaluationResult, BirthrightPolicyCreate, BirthrightPolicyJson, BirthrightPolicyResponse, BirthrightPolicyUpdate, BirthrightResolveActionsRequest, BirthrightResolveActionsResponse, GroupRoleMappingCreate, GroupRoleMappingEvaluationResult, GroupRoleMappingResponse, GroupRoleMappingUpdate
from app.security.auth import AuthenticatedUser, require_permission
from app.services import birthright as birthright_service
from app.services import departments as departments_service
from app.services import group_role_mapping as group_role_mapping_service

router = APIRouter(prefix="/policies", tags=["policies"])
policy_read = require_permission("POLICY_READ")
policy_create = require_permission("POLICY_CREATE")
policy_update = require_permission("POLICY_UPDATE")
policy_delete = require_permission("POLICY_DELETE")


@router.get("/birthright", response_model=list[BirthrightPolicyResponse])
async def list_birthright_policies(_: AuthenticatedUser = Depends(policy_read), db: AsyncSession = Depends(get_db)):
    return await birthright_service.list_birthright_policies(db)


@router.post("/birthright", response_model=BirthrightPolicyResponse, status_code=201)
async def create_birthright_policy(data: BirthrightPolicyCreate, request: Request, actor: AuthenticatedUser = Depends(policy_create), db: AsyncSession = Depends(get_db)):
    row = await birthright_service.create_birthright_policy(db, data, get_request_id(request))
    row.recheck = await birthright_service.recheck_users_for_policy(db, row.id, actor.directory_object_id, get_request_id(request))
    row.warnings = await birthright_service.policy_warnings(db, row)
    return row


@router.patch("/birthright/{policy_id}", response_model=BirthrightPolicyResponse)
async def update_birthright_policy(policy_id: UUID, data: BirthrightPolicyUpdate, request: Request, actor: AuthenticatedUser = Depends(policy_update), db: AsyncSession = Depends(get_db)):
    row = await birthright_service.update_birthright_policy(db, policy_id, data, get_request_id(request))
    row.recheck = await birthright_service.recheck_users_for_policy(db, row.id, actor.directory_object_id, get_request_id(request))
    row.warnings = await birthright_service.policy_warnings(db, row)
    return row


@router.delete("/birthright/{policy_id}", status_code=204)
async def delete_birthright_policy(policy_id: UUID, request: Request, _: AuthenticatedUser = Depends(policy_delete), db: AsyncSession = Depends(get_db)):
    await birthright_service.delete_birthright_policy(db, policy_id, get_request_id(request))


@router.post("/birthright/json", response_model=BirthrightPolicyJson, status_code=201)
async def create_birthright_policy_json(data: BirthrightPolicyJson, request: Request, actor: AuthenticatedUser = Depends(policy_create), db: AsyncSession = Depends(get_db)):
    """Create a birthright policy from the full multi-condition/multi-action JSON shape — the counterpart to the
    simple GUI form's POST /birthright, for policies that need more than one condition or more than one grant."""
    row = await birthright_service.create_birthright_policy_from_json(db, data, get_request_id(request))
    await birthright_service.recheck_users_for_policy(db, row.id, actor.directory_object_id, get_request_id(request))
    return await birthright_service.to_birthright_policy_json(db, row)


@router.get("/birthright/{policy_id}/json", response_model=BirthrightPolicyJson)
async def get_birthright_policy_json(policy_id: UUID, _: AuthenticatedUser = Depends(policy_read), db: AsyncSession = Depends(get_db)):
    """Represents ANY policy — however it was created — in the JSON shape, so 'view/edit as JSON' works for a
    plain GUI-created policy too, not just one already authored via JSON."""
    row = await birthright_service._get_policy(db, policy_id)
    return await birthright_service.to_birthright_policy_json(db, row)


@router.put("/birthright/{policy_id}/json", response_model=BirthrightPolicyJson)
async def update_birthright_policy_json(policy_id: UUID, data: BirthrightPolicyJson, request: Request, actor: AuthenticatedUser = Depends(policy_update), db: AsyncSession = Depends(get_db)):
    row = await birthright_service.update_birthright_policy_from_json(db, policy_id, data, get_request_id(request))
    await birthright_service.recheck_users_for_policy(db, row.id, actor.directory_object_id, get_request_id(request))
    return await birthright_service.to_birthright_policy_json(db, row)


@router.post("/birthright/resolve-actions", response_model=BirthrightResolveActionsResponse)
async def resolve_birthright_actions(data: BirthrightResolveActionsRequest, _: AuthenticatedUser = Depends(policy_create), db: AsyncSession = Depends(get_db)):
    """The JSON editor's 'Check resources' button: given an actions list straight out of the textarea (nothing
    else about the policy needs to be valid yet), resolves each `resource` name against a real target and
    reports found/not-found per action — a preview, not a save. Never fails the whole batch for one bad entry."""
    results = await birthright_service.resolve_action_resources(db, data.actions)
    return BirthrightResolveActionsResponse(results=[{"resourceType": r["resourceType"], "resource": r["resource"], "found": r["found"], "resolvedId": r.get("resolvedId"), "resolvedName": r.get("resolvedName"), "error": r.get("error")} for r in results])


@router.post("/birthright/evaluate/{user_id}", response_model=BirthrightEvaluationResult)
async def evaluate_birthright_policies(user_id: UUID, request: Request, actor: AuthenticatedUser = Depends(policy_create), db: AsyncSession = Depends(get_db)):
    created = await birthright_service.evaluate_birthright_policies(db, user_id, actor.directory_object_id, get_request_id(request))
    return BirthrightEvaluationResult(user_id=user_id, matched_policies=len(created), assignments_created=created)


@router.get("/group-role-mappings", response_model=list[GroupRoleMappingResponse])
async def list_group_role_mappings(group_id: UUID | None = Query(default=None), _: AuthenticatedUser = Depends(policy_read), db: AsyncSession = Depends(get_db)):
    return await group_role_mapping_service.list_group_role_mappings(db, group_id)


@router.post("/group-role-mappings", response_model=GroupRoleMappingResponse, status_code=201)
async def create_group_role_mapping(data: GroupRoleMappingCreate, request: Request, actor: AuthenticatedUser = Depends(policy_create), db: AsyncSession = Depends(get_db)):
    return await group_role_mapping_service.create_group_role_mapping(db, data, actor.directory_object_id, get_request_id(request))


@router.patch("/group-role-mappings/{mapping_id}", response_model=GroupRoleMappingResponse)
async def update_group_role_mapping(mapping_id: UUID, data: GroupRoleMappingUpdate, request: Request, actor: AuthenticatedUser = Depends(policy_update), db: AsyncSession = Depends(get_db)):
    return await group_role_mapping_service.update_group_role_mapping(db, mapping_id, data, actor.directory_object_id, get_request_id(request))


@router.delete("/group-role-mappings/{mapping_id}", status_code=204)
async def delete_group_role_mapping(mapping_id: UUID, request: Request, actor: AuthenticatedUser = Depends(policy_delete), db: AsyncSession = Depends(get_db)):
    await group_role_mapping_service.delete_group_role_mapping(db, mapping_id, actor.directory_object_id, get_request_id(request))


@router.post("/group-role-mappings/evaluate/{user_id}", response_model=GroupRoleMappingEvaluationResult)
async def evaluate_group_role_mappings(user_id: UUID, request: Request, actor: AuthenticatedUser = Depends(policy_create), db: AsyncSession = Depends(get_db)):
    created = await group_role_mapping_service.evaluate_group_role_mappings_for_user(db, user_id, actor.directory_object_id, get_request_id(request))
    return GroupRoleMappingEvaluationResult(user_id=user_id, matched_mappings=len(created), assignments_created=created)


@router.get("/departments", response_model=list[DepartmentResponse])
async def list_departments(_: AuthenticatedUser = Depends(policy_read), db: AsyncSession = Depends(get_db)):
    """The managed department list — populates the dropdown on the User Detail page's Department field."""
    return await departments_service.list_departments(db)


@router.post("/departments", response_model=DepartmentResponse, status_code=201)
async def create_department(data: DepartmentCreate, request: Request, _: AuthenticatedUser = Depends(policy_create), db: AsyncSession = Depends(get_db)):
    return await departments_service.create_department(db, data.name, get_request_id(request))


@router.delete("/departments/{department_id}", status_code=204)
async def delete_department(department_id: UUID, request: Request, _: AuthenticatedUser = Depends(policy_delete), db: AsyncSession = Depends(get_db)):
    await departments_service.delete_department(db, department_id, get_request_id(request))
