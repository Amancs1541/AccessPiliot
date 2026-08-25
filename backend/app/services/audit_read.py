from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AccessAssignment, AuditLog, IdentityProvider, User


async def _resolve_target_user(session: AsyncSession, entry: AuditLog) -> User | None:
    """Best-effort: who was actually granted/decided access for this entry, if derivable.
    ASSIGNMENT rows carry it directly via the (never hard-deleted) AccessAssignment they target;
    PACKAGE_ASSIGNED carries it in its own metadata since one package audit row covers many users' items... actually
    one row per assign_package() call, one target user, recorded explicitly in metadata."""
    if entry.target_type == "ASSIGNMENT" and entry.target_id:
        assignment = await session.get(AccessAssignment, entry.target_id)
        return await session.get(User, assignment.user_id) if assignment else None
    if entry.target_type == "PACKAGE" and entry.metadata_json and entry.metadata_json.get("user_id"):
        try:
            return await session.get(User, UUID(entry.metadata_json["user_id"]))
        except (ValueError, TypeError):
            return None
    return None


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
        target_user = await _resolve_target_user(session, entry)
        hydrated.append((entry, {
            "actor_display_name": actor_name, "provider_name": provider_name,
            "target_user_display_name": target_user.display_name if target_user else None,
            "target_user_email": target_user.email if target_user else None,
        }))
    return hydrated
