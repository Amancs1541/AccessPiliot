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


async def _hydrate_entry(session: AsyncSession, entry: AuditLog) -> dict:
    actor_name = None
    if entry.actor_user_id:
        actor = await session.get(User, entry.actor_user_id)
        actor_name = actor.display_name if actor else None
    provider_name = None
    if entry.provider_id:
        provider = await session.get(IdentityProvider, entry.provider_id)
        provider_name = provider.name if provider else None
    target_user = await _resolve_target_user(session, entry)
    return {
        "actor_display_name": actor_name, "provider_name": provider_name,
        "target_user_display_name": target_user.display_name if target_user else None,
        "target_user_email": target_user.email if target_user else None,
    }


async def list_audit_logs(session: AsyncSession, limit: int = 200) -> list[tuple[AuditLog, dict]]:
    entries = list((await session.scalars(select(AuditLog).order_by(AuditLog.timestamp.desc()).limit(limit))).all())
    return [(entry, await _hydrate_entry(session, entry)) for entry in entries]


async def list_audit_logs_by_action(session: AsyncSession, actions: list[str], limit: int = 50) -> list[tuple[AuditLog, dict]]:
    """Same hydration as list_audit_logs, filtered to a specific action set — used by the Security Operations
    dashboard (services/soc.py) to surface a curated "high-signal" feed instead of the most recent N entries
    overall, which would mostly be routine activity diluting anything actually worth a SoC admin's attention."""
    entries = list((await session.scalars(select(AuditLog).where(AuditLog.action.in_(actions)).order_by(AuditLog.timestamp.desc()).limit(limit))).all())
    return [(entry, await _hydrate_entry(session, entry)) for entry in entries]


async def list_audit_logs_by_target(session: AsyncSession, target_type: str, target_id: UUID, limit: int = 20) -> list[tuple[AuditLog, dict]]:
    """Same hydration again, filtered to everything recorded against one specific entity — the NHI detail page's
    "Recent activity" tab uses this for target_type="APPLICATION" (owner assignment, risk exceptions, type
    reclassification, enable/disable, and the sync events that touched it)."""
    entries = list((await session.scalars(select(AuditLog).where(AuditLog.target_type == target_type, AuditLog.target_id == target_id).order_by(AuditLog.timestamp.desc()).limit(limit))).all())
    return [(entry, await _hydrate_entry(session, entry)) for entry in entries]


async def list_system_generated_audit_logs(session: AsyncSession, limit: int = 50) -> list[tuple[AuditLog, dict]]:
    """Same hydration again, filtered to entries with no actor at all — every record_audit() call the sync
    worker, expiration worker, activation worker, and SoD exception expiry worker make omits actor_user_id
    entirely (it's genuinely nobody, not "System" as a placeholder), which makes this a real, already-existing
    signal for "the server did this on its own", not something inferred or newly tracked. Used by the System
    Health dashboard's live event log — the point is to show what the *server* is doing (syncs, worker actions,
    provider failures), not ordinary human-driven business activity like a plain assignment grant, which already
    has its own place in the regular Audit Logs page."""
    entries = list((await session.scalars(select(AuditLog).where(AuditLog.actor_user_id.is_(None)).order_by(AuditLog.timestamp.desc()).limit(limit))).all())
    return [(entry, await _hydrate_entry(session, entry)) for entry in entries]
