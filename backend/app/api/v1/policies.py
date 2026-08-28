from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import request_id as get_request_id
from app.db.session import get_db
from app.schemas.policies import BirthrightEvaluationResult, BirthrightPolicyCreate, BirthrightPolicyResponse, BirthrightPolicyUpdate
from app.security.auth import AuthenticatedUser, require_permission
from app.services import birthright as birthright_service

router = APIRouter(prefix="/policies", tags=["policies"])
policy_read = require_permission("POLICY_READ")
policy_create = require_permission("POLICY_CREATE")
policy_update = require_permission("POLICY_UPDATE")
policy_delete = require_permission("POLICY_DELETE")


@router.get("/birthright", response_model=list[BirthrightPolicyResponse])
async def list_birthright_policies(_: AuthenticatedUser = Depends(policy_read), db: AsyncSession = Depends(get_db)):
    return await birthright_service.list_birthright_policies(db)


@router.post("/birthright", response_model=BirthrightPolicyResponse, status_code=201)
async def create_birthright_policy(data: BirthrightPolicyCreate, request: Request, _: AuthenticatedUser = Depends(policy_create), db: AsyncSession = Depends(get_db)):
    return await birthright_service.create_birthright_policy(db, data, get_request_id(request))


@router.patch("/birthright/{policy_id}", response_model=BirthrightPolicyResponse)
async def update_birthright_policy(policy_id: UUID, data: BirthrightPolicyUpdate, request: Request, _: AuthenticatedUser = Depends(policy_update), db: AsyncSession = Depends(get_db)):
    return await birthright_service.update_birthright_policy(db, policy_id, data, get_request_id(request))


@router.delete("/birthright/{policy_id}", status_code=204)
async def delete_birthright_policy(policy_id: UUID, request: Request, _: AuthenticatedUser = Depends(policy_delete), db: AsyncSession = Depends(get_db)):
    await birthright_service.delete_birthright_policy(db, policy_id, get_request_id(request))


@router.post("/birthright/evaluate/{user_id}", response_model=BirthrightEvaluationResult)
async def evaluate_birthright_policies(user_id: UUID, request: Request, actor: AuthenticatedUser = Depends(policy_create), db: AsyncSession = Depends(get_db)):
    created = await birthright_service.evaluate_birthright_policies(db, user_id, actor.directory_object_id, get_request_id(request))
    return BirthrightEvaluationResult(user_id=user_id, matched_policies=len(created), assignments_created=created)
