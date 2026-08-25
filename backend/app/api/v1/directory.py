from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AccessPilotError
from app.db.session import get_db
from app.providers.base import NewGroupRequest, NewUserRequest, ProviderConflictError
from app.providers.graph_client import GraphError
from app.schemas.directory import ApplicationResponse, GroupCreate, GroupResponse, RoleResponse, UserAccessSummary, UserCreate, UserCreateResponse, UserResponse
from app.security.auth import AuthenticatedUser, require_permission
from app.services import directory_read
from app.services.audit import record_audit
from app.services.dashboard import admin_dashboard
from app.services.directory_sync import upsert_group, upsert_user
from app.services.provider_configuration import _connector, list_providers

router = APIRouter(tags=["directory"])
user_read = require_permission("USER_READ")
group_read = require_permission("GROUP_READ")
group_manage = require_permission("GROUP_MANAGE")
role_read = require_permission("ROLE_READ")
dashboard_admin_read = require_permission("DASHBOARD_ADMIN_READ")
dashboard_user_read = require_permission("DASHBOARD_USER_READ")


async def _primary_provider(db: AsyncSession):
    providers = await list_providers(db)
    entra = next((provider for provider in providers if provider.type == "ENTRA"), None)
    return entra or (providers[0] if providers else None)


@router.get("/users", response_model=list[UserResponse])
async def users(q: str | None = Query(default=None), _: AuthenticatedUser = Depends(user_read), db: AsyncSession = Depends(get_db)):
    return await directory_read.list_users(db, q)


@router.get("/users/{user_id}", response_model=UserResponse)
async def user_detail(user_id: UUID, _: AuthenticatedUser = Depends(user_read), db: AsyncSession = Depends(get_db)):
    return await directory_read.get_user(db, user_id)


@router.get("/users/{user_id}/access-summary", response_model=UserAccessSummary)
async def user_access_summary(user_id: UUID, _: AuthenticatedUser = Depends(user_read), db: AsyncSession = Depends(get_db)):
    return await directory_read.get_user_access_summary(db, user_id)


@router.post("/users", response_model=UserCreateResponse, status_code=201)
async def create_user(data: UserCreate, request: Request, _: AuthenticatedUser = Depends(group_manage), db: AsyncSession = Depends(get_db)):
    provider = await _primary_provider(db)
    if not provider:
        raise AccessPilotError("PROVIDER_NOT_FOUND", "No identity provider is configured.", 404)
    connector = _connector(provider)
    try:
        created = await connector.create_user(NewUserRequest(display_name=data.display_name, user_principal_name=data.user_principal_name, mail_nickname=data.mail_nickname or data.user_principal_name.split("@")[0], department=data.department, job_title=data.job_title))
    except ProviderConflictError as exc:
        raise AccessPilotError("USER_ALREADY_EXISTS", str(exc), 409) from exc
    except GraphError as exc:
        raise AccessPilotError(exc.code, exc.message, exc.status_code) from exc
    row = await upsert_user(db, provider.id, created.user)
    await record_audit(db, action="USER_CREATED", target_type="USER", target_id=row.id, provider_id=provider.id, request_id=request.state.request_id)
    await db.commit()
    await db.refresh(row)
    return UserCreateResponse(user=UserResponse.model_validate(row), temporary_password=created.temporary_password)


@router.get("/groups", response_model=list[GroupResponse])
async def groups(q: str | None = Query(default=None), _: AuthenticatedUser = Depends(group_read), db: AsyncSession = Depends(get_db)):
    return await directory_read.list_groups(db, q)


@router.get("/groups/{group_id}", response_model=GroupResponse)
async def group_detail(group_id: UUID, _: AuthenticatedUser = Depends(group_read), db: AsyncSession = Depends(get_db)):
    return await directory_read.get_group(db, group_id)


@router.get("/groups/{group_id}/members", response_model=list[UserResponse])
async def group_members(group_id: UUID, _: AuthenticatedUser = Depends(group_read), db: AsyncSession = Depends(get_db)):
    return await directory_read.list_group_members(db, group_id)


@router.post("/groups", response_model=GroupResponse, status_code=201)
async def create_group(data: GroupCreate, request: Request, _: AuthenticatedUser = Depends(group_manage), db: AsyncSession = Depends(get_db)):
    provider = await _primary_provider(db)
    if not provider:
        raise AccessPilotError("PROVIDER_NOT_FOUND", "No identity provider is configured.", 404)
    connector = _connector(provider)
    try:
        created = await connector.create_group(NewGroupRequest(display_name=data.display_name, description=data.description, mail_nickname=data.mail_nickname))
    except ProviderConflictError as exc:
        raise AccessPilotError("GROUP_ALREADY_EXISTS", str(exc), 409) from exc
    except GraphError as exc:
        raise AccessPilotError(exc.code, exc.message, exc.status_code) from exc
    row = await upsert_group(db, provider.id, created)
    await record_audit(db, action="GROUP_CREATED", target_type="GROUP", target_id=row.id, provider_id=provider.id, request_id=request.state.request_id)
    await db.commit()
    await db.refresh(row)
    return GroupResponse.model_validate(row)


@router.get("/roles", response_model=list[RoleResponse])
async def roles(q: str | None = Query(default=None), _: AuthenticatedUser = Depends(role_read), db: AsyncSession = Depends(get_db)):
    return await directory_read.list_roles(db, q)


@router.get("/applications", response_model=list[ApplicationResponse])
async def applications(q: str | None = Query(default=None), _: AuthenticatedUser = Depends(role_read), db: AsyncSession = Depends(get_db)):
    return await directory_read.list_applications(db, q)


@router.get("/dashboard/admin")
async def dashboard_admin(_: AuthenticatedUser = Depends(dashboard_admin_read), db: AsyncSession = Depends(get_db)):
    return await admin_dashboard(db)


@router.get("/dashboard/user")
async def dashboard_user(_: AuthenticatedUser = Depends(dashboard_user_read)):
    return {"data": {}, "meta": {"scope": "user", "status": "foundation_only"}}
