from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BrandingSettings
from app.schemas.branding import BrandingSettingsUpdateRequest


async def get_branding(session: AsyncSession) -> BrandingSettings:
    """Singleton — creates the one row (all fields NULL, meaning "use the bundled defaults") on first ever read."""
    settings = (await session.execute(select(BrandingSettings))).scalars().first()
    if settings is None:
        settings = BrandingSettings()
        session.add(settings)
        await session.commit()
        await session.refresh(settings)
    return settings


async def update_branding(session: AsyncSession, data: BrandingSettingsUpdateRequest) -> BrandingSettings:
    settings = await get_branding(session)
    settings.sign_in_logo = data.sign_in_logo
    settings.internal_logo = data.internal_logo
    settings.powered_by_text = data.powered_by_text
    await session.commit()
    await session.refresh(settings)
    return settings
