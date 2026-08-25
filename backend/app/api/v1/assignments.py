from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.assignments import AssignmentActivate, AssignmentCreate, AssignmentResponse
from app.security.auth import AuthenticatedUser, require_authenticated_user, require_permission
from app.services import assignments as assignment_service
from app.api.v1.directory import _primary_provider

router = APIRouter(prefix="/assignments", tags=["assignments"])
assignment_read = require_permission("ASSIGNMENT_READ")
assignment_manage = require_permission("ASSIGNMENT_CREATE")


@router.get("/pending-approval", response_model=list[AssignmentResponse])
async def list_my_approvals(actor: AuthenticatedUser = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    """Assignments where the caller is the designated approver — available to any authenticated user, not just Admins."""
    return [assignment_service.to_response(assignment, hydrated) for assignment, hydrated in await assignment_service.list_my_approvals(db, actor.directory_object_id)]


@router.get("/mine", response_model=list[AssignmentResponse])
async def list_my_assignments(actor: AuthenticatedUser = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    """The caller's own assignments (eligible, active, expired, etc.) — powers the end-user 'My Access' dashboard."""
    return [assignment_service.to_response(assignment, hydrated) for assignment, hydrated in await assignment_service.list_my_assignments(db, actor.directory_object_id)]


@router.get("/activation-policy")
async def get_activation_policy(_: AuthenticatedUser = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    """Exposes the admin-configured self-activation cap to any authenticated user, so the end-user activation UI
    can enforce/display the correct maximum without needing Admin-only provider access."""
    provider = await _primary_provider(db)
    return {"max_self_activation_hours": provider.max_self_activation_hours if provider else 8}


@router.get("", response_model=list[AssignmentResponse])
async def list_assignments(_: AuthenticatedUser = Depends(assignment_read), db: AsyncSession = Depends(get_db)):
    return [assignment_service.to_response(assignment, hydrated) for assignment, hydrated in await assignment_service.list_assignments(db)]


@router.post("", response_model=AssignmentResponse, status_code=201)
async def create_assignment(data: AssignmentCreate, request: Request, actor: AuthenticatedUser = Depends(assignment_manage), db: AsyncSession = Depends(get_db)):
    assignment, hydrated = await assignment_service.create_assignment(db, data, actor.directory_object_id, request.state.request_id)
    return assignment_service.to_response(assignment, hydrated)


@router.get("/{assignment_id}", response_model=AssignmentResponse)
async def get_assignment(assignment_id: UUID, _: AuthenticatedUser = Depends(assignment_read), db: AsyncSession = Depends(get_db)):
    assignment = await assignment_service.get_assignment(db, assignment_id)
    hydrated = await assignment_service.hydrate_display_fields(db, assignment)
    return assignment_service.to_response(assignment, hydrated)


@router.post("/{assignment_id}/approve", response_model=AssignmentResponse)
async def approve_assignment(assignment_id: UUID, request: Request, actor: AuthenticatedUser = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    """Any authenticated user may call this — the service enforces that only the designated approver or an Admin can actually decide."""
    assignment, hydrated = await assignment_service.approve_assignment(db, assignment_id, actor.directory_object_id, actor.roles, request.state.request_id)
    return assignment_service.to_response(assignment, hydrated)


@router.post("/{assignment_id}/reject", response_model=AssignmentResponse)
async def reject_assignment(assignment_id: UUID, request: Request, actor: AuthenticatedUser = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    assignment, hydrated = await assignment_service.reject_assignment(db, assignment_id, actor.directory_object_id, actor.roles, request.state.request_id)
    return assignment_service.to_response(assignment, hydrated)


@router.post("/{assignment_id}/activate", response_model=AssignmentResponse)
async def activate_assignment(assignment_id: UUID, data: AssignmentActivate, request: Request, actor: AuthenticatedUser = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    """Any authenticated user may call this — the service enforces that only the assignment's own user or an Admin can actually activate it."""
    assignment, hydrated = await assignment_service.activate_assignment(db, assignment_id, actor.directory_object_id, actor.roles, data.duration_hours, request.state.request_id)
    return assignment_service.to_response(assignment, hydrated)


@router.post("/{assignment_id}/deactivate", response_model=AssignmentResponse)
async def deactivate_assignment(assignment_id: UUID, request: Request, actor: AuthenticatedUser = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    """Any authenticated user may call this — the service enforces that only the assignment's own user or an Admin can actually deactivate it."""
    assignment, hydrated = await assignment_service.deactivate_assignment(db, assignment_id, actor.directory_object_id, actor.roles, request.state.request_id)
    return assignment_service.to_response(assignment, hydrated)
