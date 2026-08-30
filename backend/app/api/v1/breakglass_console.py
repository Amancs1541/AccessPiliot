from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AccessPilotError, request_id as get_request_id
from app.db.session import get_db
from app.schemas.portal_auth import BreakglassPasswordRotateRequest, PortalAuthConfigResponse, PortalAuthConfigUpdateRequest
from app.security.auth import AuthenticatedUser, require_permission
from app.services.portal_auth import get_active_portal_auth_config, rotate_breakglass_password, update_active_portal_auth_config

router = APIRouter(prefix="/auth", tags=["breakglass-console"])

# The two routes an AccessPilot.BreakGlassAdmin session can reach — gated with the same require_permission()
# mechanism as every other protected route in this app, so "sees nothing else" falls out for free: this role
# simply doesn't hold any of the other permission strings, and no existing endpoint needed to change.


@router.get("/portal-auth-config", response_model=PortalAuthConfigResponse)
async def get_portal_auth_config(db: AsyncSession = Depends(get_db), _: AuthenticatedUser = Depends(require_permission("PORTAL_AUTH_MANAGE"))):
    config = await get_active_portal_auth_config(db)
    if config is None:
        raise AccessPilotError("CONFIG_NOT_FOUND", "There is no active portal authentication configuration.", 404)
    return config


@router.patch("/portal-auth-config", response_model=PortalAuthConfigResponse)
async def patch_portal_auth_config(data: PortalAuthConfigUpdateRequest, request: Request, db: AsyncSession = Depends(get_db), _: AuthenticatedUser = Depends(require_permission("PORTAL_AUTH_MANAGE"))):
    return await update_active_portal_auth_config(db, data, get_request_id(request))


@router.post("/breakglass-credential/rotate")
async def rotate_own_credential(data: BreakglassPasswordRotateRequest, request: Request, db: AsyncSession = Depends(get_db), user: AuthenticatedUser = Depends(require_permission("BREAKGLASS_CREDENTIAL_MANAGE"))):
    if not user.subject.startswith("breakglass:"):
        raise AccessPilotError("ACCESS_DENIED", "This action is only available to a break-glass session.", 403)
    account_id = UUID(user.subject.split(":", 1)[1])
    await rotate_breakglass_password(db, account_id, data.new_password, get_request_id(request))
    return {"rotated": True}
