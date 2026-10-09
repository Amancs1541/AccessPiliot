from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.workflows import WorkflowDecideRequest, WorkflowDefinitionCreate, WorkflowDefinitionResponse, WorkflowDefinitionUpdate, WorkflowRequestCreate, WorkflowRequestResponse
from app.security.auth import AuthenticatedUser, require_authenticated_user, require_permission
from app.services import workflows as workflow_service

router = APIRouter(prefix="/workflows", tags=["workflows"])
workflow_read = require_permission("WORKFLOW_READ")
workflow_manage = require_permission("WORKFLOW_MANAGE")


@router.get("/definitions", response_model=list[WorkflowDefinitionResponse])
async def list_workflow_definitions(_: AuthenticatedUser = Depends(workflow_read), db: AsyncSession = Depends(get_db)):
    return await workflow_service.list_workflow_definitions(db)


@router.post("/definitions", response_model=WorkflowDefinitionResponse, status_code=201)
async def create_workflow_definition(data: WorkflowDefinitionCreate, request: Request, actor: AuthenticatedUser = Depends(workflow_manage), db: AsyncSession = Depends(get_db)):
    return await workflow_service.create_workflow_definition(db, data, actor.directory_object_id, request.state.request_id)


@router.get("/definitions/active", response_model=list[WorkflowDefinitionResponse])
async def list_active_workflow_definitions(_: AuthenticatedUser = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    """Open to any authenticated user — how a requester sees which workflows they can submit a request against."""
    return await workflow_service.list_active_workflow_definitions(db)


@router.get("/definitions/{definition_id}", response_model=WorkflowDefinitionResponse)
async def get_workflow_definition(definition_id: UUID, _: AuthenticatedUser = Depends(workflow_read), db: AsyncSession = Depends(get_db)):
    return await workflow_service.get_workflow_definition_response(db, definition_id)


@router.patch("/definitions/{definition_id}", response_model=WorkflowDefinitionResponse)
async def update_workflow_definition(definition_id: UUID, data: WorkflowDefinitionUpdate, request: Request, actor: AuthenticatedUser = Depends(workflow_manage), db: AsyncSession = Depends(get_db)):
    return await workflow_service.update_workflow_definition(db, definition_id, data, actor.directory_object_id, request.state.request_id)


@router.delete("/definitions/{definition_id}", status_code=204)
async def delete_workflow_definition(definition_id: UUID, request: Request, actor: AuthenticatedUser = Depends(workflow_manage), db: AsyncSession = Depends(get_db)):
    await workflow_service.delete_workflow_definition(db, definition_id, actor.directory_object_id, request.state.request_id)


@router.get("/requests", response_model=list[WorkflowRequestResponse])
async def list_all_workflow_requests(_: AuthenticatedUser = Depends(workflow_read), db: AsyncSession = Depends(get_db)):
    """Every workflow request ever submitted — admin oversight view."""
    return await workflow_service.list_all_workflow_requests(db)


@router.post("/requests", response_model=WorkflowRequestResponse, status_code=201)
async def submit_workflow_request(data: WorkflowRequestCreate, request: Request, actor: AuthenticatedUser = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    """Any authenticated user may submit a request against an ACTIVE workflow definition."""
    return await workflow_service.submit_workflow_request(db, data, actor.directory_object_id, request.state.request_id)


@router.get("/requests/mine", response_model=list[WorkflowRequestResponse])
async def list_my_workflow_requests(actor: AuthenticatedUser = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    return await workflow_service.list_my_workflow_requests(db, actor.directory_object_id)


@router.get("/requests/pending-my-decision", response_model=list[WorkflowRequestResponse])
async def list_pending_my_decision(actor: AuthenticatedUser = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    return await workflow_service.list_pending_my_decision(db, actor.directory_object_id, actor.roles)


@router.post("/requests/{instance_id}/stages/{stage_instance_id}/decide", response_model=WorkflowRequestResponse)
async def decide_stage(instance_id: UUID, stage_instance_id: UUID, data: WorkflowDecideRequest, request: Request, actor: AuthenticatedUser = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    """instance_id addresses the underlying WorkflowInstance directly — the same id works whether it's a
    self-service WorkflowRequest or an assignment routed through create_assignment()."""
    return await workflow_service.decide_stage(db, instance_id, stage_instance_id, data, actor.directory_object_id, actor.roles, request.state.request_id)


@router.post("/requests/{instance_id}/cancel", response_model=WorkflowRequestResponse)
async def cancel_workflow_instance(instance_id: UUID, request: Request, actor: AuthenticatedUser = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    return await workflow_service.cancel_workflow_instance(db, instance_id, actor.directory_object_id, actor.roles, request.state.request_id)
