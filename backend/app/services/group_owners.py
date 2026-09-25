from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AccessPilotError
from app.models import Group, GroupOwner, User
from app.schemas.group_owners import GroupOwnerInfo
from app.services.assignments import _resolve_internal_user_id
from app.services.audit import record_audit


async def list_group_owners(session: AsyncSession, group_id: UUID) -> list[GroupOwnerInfo]:
    if await session.get(Group, group_id) is None:
        raise AccessPilotError("GROUP_NOT_FOUND", "The group was not found.", 404)
    rows = (await session.scalars(select(GroupOwner).where(GroupOwner.group_id == group_id).order_by(GroupOwner.created_at))).all()
    owners: list[GroupOwnerInfo] = []
    for row in rows:
        user = await session.get(User, row.user_id)
        owners.append(GroupOwnerInfo(user_id=row.user_id, display_name=user.display_name if user else None, email=user.email if user else None))
    return owners


async def set_group_owners(session: AsyncSession, group_id: UUID, user_ids: list[UUID], actor_subject: str, request_id: str) -> list[GroupOwnerInfo]:
    """Replaces the group's full AccessPilot-side owner set. Local records only — nothing is written to the
    directory provider."""
    if await session.get(Group, group_id) is None:
        raise AccessPilotError("GROUP_NOT_FOUND", "The group was not found.", 404)
    unique_ids = list(dict.fromkeys(user_ids))
    for user_id in unique_ids:
        if await session.get(User, user_id) is None:
            raise AccessPilotError("USER_NOT_FOUND", "One of the selected owners was not found.", 404)
    actor_id = await _resolve_internal_user_id(session, actor_subject)
    for existing in (await session.scalars(select(GroupOwner).where(GroupOwner.group_id == group_id))).all():
        await session.delete(existing)
    await session.flush()
    for user_id in unique_ids:
        session.add(GroupOwner(group_id=group_id, user_id=user_id, assigned_by=actor_id))
    await record_audit(session, action="GROUP_OWNERS_UPDATED", target_type="GROUP", target_id=group_id, actor_user_id=actor_id, request_id=request_id, metadata={"owner_count": len(unique_ids)})
    await session.commit()
    return await list_group_owners(session, group_id)
