from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SecuritySettings
from app.schemas.security_settings import SecuritySettingsUpdateRequest


async def get_security_settings(session: AsyncSession) -> SecuritySettings:
    """Singleton — creates the one row with its defaults (both features off) on first ever read, so every
    deployment starts byte-for-byte unaffected until an Admin deliberately opts in."""
    settings = (await session.execute(select(SecuritySettings))).scalars().first()
    if settings is None:
        settings = SecuritySettings()
        session.add(settings)
        await session.commit()
        await session.refresh(settings)
    return settings


async def update_security_settings(session: AsyncSession, data: SecuritySettingsUpdateRequest) -> SecuritySettings:
    settings = await get_security_settings(session)
    settings.blur_enabled = data.blur_enabled
    settings.blur_after_minutes = data.blur_after_minutes
    settings.lock_enabled = data.lock_enabled
    settings.lock_after_minutes = data.lock_after_minutes
    await session.commit()
    await session.refresh(settings)
    return settings
