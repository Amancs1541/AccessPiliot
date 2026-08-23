from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Group, IdentityProvider, Role, SyncRun, User


async def admin_dashboard(session: AsyncSession) -> dict[str, Any]:
    users_count = (await session.execute(select(func.count()).select_from(User))).scalar_one()
    groups_count = (await session.execute(select(func.count()).select_from(Group))).scalar_one()
    roles_count = (await session.execute(select(func.count()).select_from(Role))).scalar_one()
    privileged_roles_count = (await session.execute(select(func.count()).select_from(Role).where(Role.is_privileged.is_(True)))).scalar_one()
    provider: Optional[IdentityProvider] = (await session.execute(select(IdentityProvider).order_by(IdentityProvider.created_at))).scalars().first()
    last_run: Optional[SyncRun] = (await session.execute(select(SyncRun).order_by(SyncRun.started_at.desc()).limit(1))).scalars().first()
    return {
        "users": users_count,
        "groups": groups_count,
        "roles": roles_count,
        "privilegedRoles": privileged_roles_count,
        "provider": None if provider is None else {"id": str(provider.id), "name": provider.name, "status": provider.status, "lastSyncAt": provider.last_sync_at.isoformat() if provider.last_sync_at else None},
        "lastSync": None if last_run is None else {"id": str(last_run.id), "status": last_run.status, "startedAt": last_run.started_at.isoformat(), "completedAt": last_run.completed_at.isoformat() if last_run.completed_at else None, "usersProcessed": last_run.users_processed, "groupsProcessed": last_run.groups_processed, "rolesProcessed": last_run.roles_processed, "errorsCount": last_run.errors_count},
    }
