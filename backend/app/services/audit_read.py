from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, IdentityProvider, User


async def list_audit_logs(session: AsyncSession, limit: int = 200) -> list[tuple[AuditLog, dict]]:
    entries = list((await session.scalars(select(AuditLog).order_by(AuditLog.timestamp.desc()).limit(limit))).all())
    hydrated: list[tuple[AuditLog, dict]] = []
    for entry in entries:
        actor_name = None
        if entry.actor_user_id:
            actor = await session.get(User, entry.actor_user_id)
            actor_name = actor.display_name if actor else None
        provider_name = None
        if entry.provider_id:
            provider = await session.get(IdentityProvider, entry.provider_id)
            provider_name = provider.name if provider else None
        hydrated.append((entry, {"actor_display_name": actor_name, "provider_name": provider_name}))
    return hydrated
