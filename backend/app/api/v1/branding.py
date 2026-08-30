from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.branding import BrandingSettingsResponse, BrandingSettingsUpdateRequest
from app.security.auth import AuthenticatedUser, require_permission
from app.services.branding import get_branding, update_branding

router = APIRouter(prefix="/branding", tags=["branding"])


@router.get("", response_model=BrandingSettingsResponse)
async def get_settings(db: AsyncSession = Depends(get_db)):
    """Deliberately public, no auth at all — the public sign-in screen needs its logo/attribution BEFORE anyone
    has signed in. Contains no sensitive data (just images and a text label)."""
    return await get_branding(db)


@router.patch("", response_model=BrandingSettingsResponse)
async def patch_settings(data: BrandingSettingsUpdateRequest, db: AsyncSession = Depends(get_db), _: AuthenticatedUser = Depends(require_permission("BRANDING_MANAGE"))):
    return await update_branding(db, data)
