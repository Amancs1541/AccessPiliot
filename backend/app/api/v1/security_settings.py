from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.security_settings import SecuritySettingsResponse, SecuritySettingsUpdateRequest, SupportContactResponse
from app.security.auth import AuthenticatedUser, require_authenticated_user, require_permission
from app.services.security_settings import get_security_settings, update_security_settings

router = APIRouter(prefix="/security-settings", tags=["security-settings"])


@router.get("", response_model=SecuritySettingsResponse)
async def get_settings(db: AsyncSession = Depends(get_db), _: AuthenticatedUser = Depends(require_authenticated_user)):
    """Readable by any authenticated user, admin or end-user alike — the idle blur/lock behavior applies to both,
    so both need to know the current configuration, not just admins."""
    return await get_security_settings(db)


@router.get("/support-contact", response_model=SupportContactResponse)
async def get_support_contact(db: AsyncSession = Depends(get_db)):
    """Deliberately public, no auth at all — mirrors GET /branding exactly. The one scenario this exists for is
    a user who can't sign in at all (the IDP itself is unreachable), so this can never sit behind
    require_authenticated_user. Returns only the one field, never the idle-timeout configuration above."""
    settings = await get_security_settings(db)
    return SupportContactResponse(support_contact_email=settings.support_contact_email)


@router.patch("", response_model=SecuritySettingsResponse)
async def patch_settings(data: SecuritySettingsUpdateRequest, db: AsyncSession = Depends(get_db), _: AuthenticatedUser = Depends(require_permission("SECURITY_SETTINGS_MANAGE"))):
    return await update_security_settings(db, data)
