from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.packages import PackageAssignCreate, PackageAssignMemberResult, PackageAssignResponse, PackageAssignmentBatch, PackageCreate, PackageEligibilityUpdate, PackageRequestCreate, PackageResponse, PackageUpdate
from app.security.auth import AuthenticatedUser, require_authenticated_user, require_permission
from app.services import packages as package_service

router = APIRouter(prefix="/packages", tags=["packages"])
package_read = require_permission("PACKAGE_READ")
package_manage = require_permission("PACKAGE_MANAGE")
assignment_manage = require_permission("ASSIGNMENT_CREATE")


@router.get("", response_model=list[PackageResponse])
async def list_packages(_: AuthenticatedUser = Depends(package_read), db: AsyncSession = Depends(get_db)):
    return await package_service.list_packages(db)


@router.get("/assignment-batches", response_model=list[PackageAssignmentBatch])
async def list_assignment_batches(_: AuthenticatedUser = Depends(package_read), db: AsyncSession = Depends(get_db)):
    return await package_service.list_assignment_batches(db)


@router.get("/my-assignment-batches", response_model=list[PackageAssignmentBatch])
async def list_my_assignment_batches(actor: AuthenticatedUser = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    """Package assignment batches where the caller is the designated approver — available to any authenticated
    user, not just Admins, matching GET /assignments/pending-approval's access model."""
    return await package_service.list_my_assignment_batches(db, actor.directory_object_id)


@router.get("/my-package-batches", response_model=list[PackageAssignmentBatch])
async def list_my_package_batches(actor: AuthenticatedUser = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    """Package assignment batches where the caller is the target user — powers the My Access page's grouping of a
    package's items into one row, distinct from /my-assignment-batches (which is scoped to the approver instead)."""
    return await package_service.list_my_package_batches(db, actor.directory_object_id)


@router.get("/requestable", response_model=list[PackageResponse])
async def list_requestable_packages(actor: AuthenticatedUser = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    """Active packages the caller is personally eligible to self-request — available to any authenticated user."""
    return await package_service.list_requestable_packages(db, actor.directory_object_id)


@router.post("", response_model=PackageResponse, status_code=201)
async def create_package(data: PackageCreate, request: Request, actor: AuthenticatedUser = Depends(package_manage), db: AsyncSession = Depends(get_db)):
    return await package_service.create_package(db, data, actor.directory_object_id, request.state.request_id)


@router.get("/{package_id}", response_model=PackageResponse)
async def get_package(package_id: UUID, _: AuthenticatedUser = Depends(package_read), db: AsyncSession = Depends(get_db)):
    return await package_service.get_package_response(db, package_id)


@router.patch("/{package_id}", response_model=PackageResponse)
async def update_package(package_id: UUID, data: PackageUpdate, request: Request, actor: AuthenticatedUser = Depends(package_manage), db: AsyncSession = Depends(get_db)):
    return await package_service.update_package(db, package_id, data, actor.directory_object_id, request.state.request_id)


@router.delete("/{package_id}")
async def delete_package(package_id: UUID, request: Request, actor: AuthenticatedUser = Depends(package_manage), db: AsyncSession = Depends(get_db)):
    """Deletes the package if it's never been assigned to anyone; otherwise archives it (kept for assignment
    history/audit) and returns the archived package. A true delete returns a simple confirmation instead."""
    result = await package_service.delete_package(db, package_id, actor.directory_object_id, request.state.request_id)
    return result if result is not None else {"deleted": True, "id": str(package_id)}


@router.post("/{package_id}/assign", response_model=PackageAssignResponse, status_code=201)
async def assign_package(package_id: UUID, data: PackageAssignCreate, request: Request, actor: AuthenticatedUser = Depends(assignment_manage), db: AsyncSession = Depends(get_db)):
    return await package_service.assign_package(db, package_id, data, actor.directory_object_id, request.state.request_id)


@router.put("/{package_id}/eligibility", response_model=PackageResponse)
async def set_package_eligibility(package_id: UUID, data: PackageEligibilityUpdate, request: Request, actor: AuthenticatedUser = Depends(package_manage), db: AsyncSession = Depends(get_db)):
    """Admin-only: sets exactly who (individual users and/or whole groups) may self-request this package, and the
    approver automatically used when they do."""
    return await package_service.set_package_eligibility(db, package_id, data, actor.directory_object_id, request.state.request_id)


@router.post("/{package_id}/request", response_model=PackageAssignMemberResult, status_code=201)
async def request_package(package_id: UUID, data: PackageRequestCreate, request: Request, actor: AuthenticatedUser = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    """Self-service: any authenticated user may call this — the service enforces that they're actually eligible."""
    return await package_service.request_package(db, package_id, data, actor.directory_object_id, request.state.request_id)
