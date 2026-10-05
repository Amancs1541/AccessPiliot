from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.business_roles import BusinessRoleAnalytics, BusinessRoleAssignCreate, BusinessRoleAssignResponse, BusinessRoleCreate, BusinessRoleHolder, BusinessRoleItemResponse, BusinessRoleOwnerRename, BusinessRoleResponse, BusinessRoleUpdate, ResourceReferenceUpdate, RoleAssignmentBatch
from app.security.auth import AuthenticatedUser, require_authenticated_user, require_permission
from app.services import business_roles as business_role_service

router = APIRouter(prefix="/business-roles", tags=["business-roles"])
business_role_read = require_permission("BUSINESS_ROLE_READ")
business_role_manage = require_permission("BUSINESS_ROLE_MANAGE")


@router.get("", response_model=list[BusinessRoleResponse])
async def list_business_roles(_: AuthenticatedUser = Depends(business_role_read), db: AsyncSession = Depends(get_db)):
    return await business_role_service.list_business_roles(db)


@router.get("/unmapped-entitlements", response_model=list[BusinessRoleItemResponse])
async def list_unmapped_entitlements(_: AuthenticatedUser = Depends(business_role_read), db: AsyncSession = Depends(get_db)):
    """Every Group/Role/Application not currently mapped to any Business Role — the entitlement-catalog gap view."""
    return await business_role_service.list_unmapped_entitlements(db)


@router.get("/assignment-batches", response_model=list[RoleAssignmentBatch])
async def list_role_assignment_batches(_: AuthenticatedUser = Depends(business_role_read), db: AsyncSession = Depends(get_db)):
    """All Business Role assignment batches — used by the Assignments admin page to show one role grant as a
    single collapsible row instead of one row per mapped item."""
    return await business_role_service.list_role_assignment_batches(db)


@router.get("/my-assignment-batches", response_model=list[RoleAssignmentBatch])
async def list_my_role_assignment_batches(actor: AuthenticatedUser = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    """Business Role assignment batches where the caller is the designated approver — used by My Approvals,
    mirroring GET /packages/my-assignment-batches."""
    return await business_role_service.list_my_role_assignment_batches(db, actor.directory_object_id)


@router.get("/analytics", response_model=BusinessRoleAnalytics)
async def get_business_role_analytics(_: AuthenticatedUser = Depends(business_role_read), db: AsyncSession = Depends(get_db)):
    """Plan Step 6 — a live-computed Business Roles stats panel: counts by status, privileged roles, roles with
    no owner, open SoD conflicts touching a role, unmapped entitlements, and the most-held roles."""
    return await business_role_service.get_business_role_analytics(db)


@router.get("/owned", response_model=list[BusinessRoleResponse])
async def list_owned_business_roles(actor: AuthenticatedUser = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    """Business Roles the caller owns — available to any authenticated user, object-level, mirrors
    GET /packages/owned."""
    return await business_role_service.list_owned_business_roles(db, actor.directory_object_id)


@router.post("", response_model=BusinessRoleResponse, status_code=201)
async def create_business_role(data: BusinessRoleCreate, request: Request, actor: AuthenticatedUser = Depends(business_role_manage), db: AsyncSession = Depends(get_db)):
    return await business_role_service.create_business_role(db, data, actor.directory_object_id, request.state.request_id)


@router.get("/{role_id}", response_model=BusinessRoleResponse)
async def get_business_role(role_id: UUID, _: AuthenticatedUser = Depends(business_role_read), db: AsyncSession = Depends(get_db)):
    return await business_role_service.get_business_role_response(db, role_id)


@router.patch("/{role_id}", response_model=BusinessRoleResponse)
async def update_business_role(role_id: UUID, data: BusinessRoleUpdate, request: Request, actor: AuthenticatedUser = Depends(business_role_manage), db: AsyncSession = Depends(get_db)):
    return await business_role_service.update_business_role(db, role_id, data, actor.directory_object_id, request.state.request_id)


@router.delete("/{role_id}", status_code=200)
async def delete_business_role(role_id: UUID, request: Request, actor: AuthenticatedUser = Depends(business_role_manage), db: AsyncSession = Depends(get_db)):
    result = await business_role_service.delete_business_role(db, role_id, actor.directory_object_id, request.state.request_id)
    return result if result is not None else Response(status_code=204)


@router.post("/{role_id}/assign", response_model=BusinessRoleAssignResponse, status_code=201)
async def assign_business_role(role_id: UUID, data: BusinessRoleAssignCreate, request: Request, actor: AuthenticatedUser = Depends(business_role_manage), db: AsyncSession = Depends(get_db)):
    """Admin direct-assign: grants every entitlement mapped to this Business Role, through the exact same
    approval/activation path a raw assignment or a package already uses."""
    return await business_role_service.assign_business_role(db, role_id, data, actor.directory_object_id, request.state.request_id)


@router.get("/{role_id}/holders", response_model=list[BusinessRoleHolder])
async def list_role_holders(role_id: UUID, _: AuthenticatedUser = Depends(business_role_read), db: AsyncSession = Depends(get_db)):
    """Effective access: everyone currently holding this Business Role and the status of each mapped item."""
    return await business_role_service.list_role_holders(db, role_id)


@router.patch("/{role_id}/owner-rename", response_model=BusinessRoleResponse)
async def owner_rename_business_role(role_id: UUID, data: BusinessRoleOwnerRename, request: Request, actor: AuthenticatedUser = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    """Owner-only: renames a Business Role the caller owns. Mirrors PATCH /packages/{id}/owner-rename — no
    BUSINESS_ROLE_MANAGE permission needed, only a BusinessRoleOwner row for this role."""
    return await business_role_service.owner_rename_business_role(db, role_id, data.name.strip(), actor.directory_object_id, request.state.request_id)


@router.delete("/{role_id}/items/{item_id}", response_model=BusinessRoleResponse)
async def owner_remove_item(role_id: UUID, item_id: UUID, request: Request, actor: AuthenticatedUser = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    """Owner-only: removes one mapped item from a Business Role the caller owns. Never adds items or touches
    anything else — mirrors DELETE /packages/{id}/items/{item_id}."""
    return await business_role_service.owner_remove_item(db, role_id, item_id, actor.directory_object_id, request.state.request_id)


@router.patch("/resources/{resource_type}/{resource_id}/reference")
async def set_resource_reference(resource_type: str, resource_id: UUID, data: ResourceReferenceUpdate, request: Request, actor: AuthenticatedUser = Depends(business_role_manage), db: AsyncSession = Depends(get_db)):
    """Sets the cosmetic resource_code/naming_convention reference fields directly on a Group/Role/Application."""
    resource = await business_role_service.set_resource_reference(db, resource_type.upper(), resource_id, data, actor.directory_object_id, request.state.request_id)
    return {"id": resource.id, "resource_code": resource.resource_code, "naming_convention": resource.naming_convention}
