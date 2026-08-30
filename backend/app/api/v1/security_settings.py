from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.security_settings import SecuritySettingsResponse, SecuritySettingsUpdateRequest
from app.security.auth import AuthenticatedUser, require_authenticated_user, require_permission
from app.services.security_settings import get_security_settings, update_security_settings

router = APIRouter(prefix="/security-settings", tags=["security-settings"])


@router.get("", response_model=SecuritySettingsResponse)
async def get_settings(db: AsyncSession = Depends(get_db), _: AuthenticatedUser = Depends(require_authenticated_user)):
    """Readable by any authenticated user, admin or end-user alike — the idle blur/lock behavior applies to both,
    so both need to know the current configuration, not just admins."""
    return await get_security_settings(db)


@router.patch("", response_model=SecuritySettingsResponse)
async def patch_settings(data: SecuritySettingsUpdateRequest, db: AsyncSession = Depends(get_db), _: AuthenticatedUser = Depends(require_permission("SECURITY_SETTINGS_MANAGE"))):
    return await update_security_settings(db, data)
