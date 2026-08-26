from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AccessAssignment, AuditLog, Group, IdentityProvider, Role, SyncRun, User


async def admin_dashboard(session: AsyncSession) -> dict[str, Any]:
    users_count = (await session.execute(select(func.count()).select_from(User))).scalar_one()
    groups_count = (await session.execute(select(func.count()).select_from(Group))).scalar_one()
    roles_count = (await session.execute(select(func.count()).select_from(Role))).scalar_one()
    privileged_roles_count = (await session.execute(select(func.count()).select_from(Role).where(Role.is_privileged.is_(True)))).scalar_one()
    active_sessions_count = (await session.execute(select(func.count()).select_from(AccessAssignment).where(AccessAssignment.status == "ACTIVE"))).scalar_one()
    pending_requests_count = (await session.execute(select(func.count()).select_from(AccessAssignment).where(AccessAssignment.status == "PENDING_APPROVAL"))).scalar_one()
    expiring_soon = datetime.now(timezone.utc) + timedelta(hours=24)
    expiring_access_count = (await session.execute(select(func.count()).select_from(AccessAssignment).where(AccessAssignment.status == "ACTIVE", AccessAssignment.expiration_time.isnot(None), AccessAssignment.expiration_time <= expiring_soon))).scalar_one()
    provider: Optional[IdentityProvider] = (await session.execute(select(IdentityProvider).order_by(IdentityProvider.created_at))).scalars().first()
    last_run: Optional[SyncRun] = (await session.execute(select(SyncRun).order_by(SyncRun.started_at.desc()).limit(1))).scalars().first()
    return {
        "users": users_count,
        "groups": groups_count,
        "roles": roles_count,
        "privilegedRoles": privileged_roles_count,
        "activeSessions": active_sessions_count,
        "pendingRequests": pending_requests_count,
        "expiringAccess": expiring_access_count,
        "provider": None if provider is None else {"id": str(provider.id), "name": provider.name, "status": provider.status, "lastSyncAt": provider.last_sync_at.isoformat() if provider.last_sync_at else None},
        "lastSync": None if last_run is None else {"id": str(last_run.id), "status": last_run.status, "startedAt": last_run.started_at.isoformat(), "completedAt": last_run.completed_at.isoformat() if last_run.completed_at else None, "usersProcessed": last_run.users_processed, "groupsProcessed": last_run.groups_processed, "rolesProcessed": last_run.roles_processed, "errorsCount": last_run.errors_count},
    }


async def _user_access_segment_ids(session: AsyncSession) -> tuple[set, set]:
    """Distinct user ids segregated into two mutually exclusive buckets: those holding at least one Permanent+Active
    assignment ("permanent_active") vs those whose access is still only Eligible, never activated ("eligible") — a
    user with both lands under permanent_active only, so the two sets never overlap."""
    permanent_active_user_ids = set((await session.execute(
        select(AccessAssignment.user_id).distinct().where(AccessAssignment.assignment_type == "PERMANENT", AccessAssignment.status == "ACTIVE")
    )).scalars().all())
    eligible_user_ids = set((await session.execute(
        select(AccessAssignment.user_id).distinct().where(AccessAssignment.status == "ELIGIBLE")
    )).scalars().all()) - permanent_active_user_ids
    return permanent_active_user_ids, eligible_user_ids


async def get_user_access_segments(session: AsyncSession) -> dict[str, Any]:
    """Counts for the two buckets — powers the dashboard pie chart."""
    permanent_active_user_ids, eligible_user_ids = await _user_access_segment_ids(session)
    return {"permanentActive": len(permanent_active_user_ids), "eligible": len(eligible_user_ids)}


async def get_user_access_segment_members(session: AsyncSession, segment: str) -> list[dict[str, Any]]:
    """The actual users behind one pie-chart slice — fetched on demand when an admin clicks into it, not bundled
    into the counts-only endpoint above."""
    permanent_active_user_ids, eligible_user_ids = await _user_access_segment_ids(session)
    user_ids = permanent_active_user_ids if segment == "permanent-active" else eligible_user_ids if segment == "eligible" else set()
    if not user_ids:
        return []
    users = (await session.execute(select(User).where(User.id.in_(user_ids)).order_by(User.display_name))).scalars().all()
    return [{"id": str(user.id), "display_name": user.display_name, "email": user.email} for user in users]


async def get_privileged_role_activation_timeline(session: AsyncSession, days: int = 30) -> dict[str, Any]:
    """Daily count of DISTINCT users who now hold an activated privileged role — credited to the assignment's own
    user_id, not whichever admin may have clicked activate on their behalf (see _authorize_activation). Sourced
    entirely from the audit log's ASSIGNMENT_ACTIVATED entries, joined back to the assignment/role to filter for
    privileged roles specifically — works identically whether the activation came from a package or a direct
    assignment, since both paths create an ordinary AccessAssignment and fire the same audit action.
    Bucketed in Python (not SQL date_trunc/GROUP BY) so this behaves identically on SQLite (tests) and Postgres."""
    days = max(1, min(days, 90))
    since = datetime.now(timezone.utc) - timedelta(days=days - 1)
    rows = (await session.execute(
        select(AuditLog.timestamp, AccessAssignment.user_id)
        .join(AccessAssignment, AccessAssignment.id == AuditLog.target_id)
        .join(Role, Role.id == AccessAssignment.resource_id)
        .where(
            AuditLog.action == "ASSIGNMENT_ACTIVATED",
            AuditLog.result == "SUCCESS",
            AuditLog.target_type == "ASSIGNMENT",
            AccessAssignment.resource_type == "ROLE",
            Role.is_privileged.is_(True),
            AuditLog.timestamp >= since,
        )
    )).all()

    users_by_day: dict[date, set] = {}
    for timestamp, user_id in rows:
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        day = timestamp.astimezone(timezone.utc).date()
        users_by_day.setdefault(day, set()).add(user_id)

    today = datetime.now(timezone.utc).date()
    series = []
    for offset in range(days - 1, -1, -1):
        day = today - timedelta(days=offset)
        series.append({"date": day.isoformat(), "count": len(users_by_day.get(day, set()))})
    return {"days": days, "series": series}
