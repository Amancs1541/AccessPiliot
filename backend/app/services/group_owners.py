from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AccessPilotError
from app.models import Group, GroupOwner, User
from app.schemas.group_owners import GroupOwnerInfo, GroupOwnerSelfServiceUpdate
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


async def list_owned_groups(session: AsyncSession, actor_subject: str) -> list[Group]:
    actor_id = await _resolve_internal_user_id(session, actor_subject)
    if actor_id is None:
        return []
    return list((await session.scalars(select(Group).join(GroupOwner, GroupOwner.group_id == Group.id).where(GroupOwner.user_id == actor_id).order_by(Group.name))).all())


async def owner_update_group(session: AsyncSession, group_id: UUID, data: GroupOwnerSelfServiceUpdate, actor_subject: str, request_id: str) -> Group:
    """The group owner portal's only edit power: description and Group Label — cosmetic fields alone, mirroring
    the narrow scope of owner_rename_package/owner_rename_business_role (PACKAGE/BUSINESS_ROLE). Unlike those two,
    there's no single "rename" here — a group's real name is synced from the directory provider and never
    editable in AccessPilot at all, owner or Admin alike; description and group_label are the only fields a Group
    has ever been editable on (see GroupCreate), so this is the full available surface, not an arbitrary subset."""
    group = await session.get(Group, group_id)
    if group is None:
        raise AccessPilotError("GROUP_NOT_FOUND", "The group was not found.", 404)
    actor_id = await _resolve_internal_user_id(session, actor_subject)
    is_owner = actor_id is not None and (await session.scalars(select(GroupOwner.id).where(GroupOwner.group_id == group_id, GroupOwner.user_id == actor_id))).first() is not None
    if not is_owner:
        raise AccessPilotError("ACCESS_DENIED", "Only an owner of this group can manage it here.", 403)
    changes = data.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(group, field, value)
    if changes:
        await record_audit(session, action="GROUP_UPDATED_BY_OWNER", target_type="GROUP", target_id=group_id, actor_user_id=actor_id, request_id=request_id, metadata=changes)
    await session.commit()
    await session.refresh(group)
    return group
