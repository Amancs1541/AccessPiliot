from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AccessPilotError
from app.models import GroupLabel
from app.services.audit import record_audit


async def list_group_labels(session: AsyncSession) -> list[GroupLabel]:
    return list((await session.execute(select(GroupLabel).order_by(GroupLabel.name))).scalars().all())


async def create_group_label(session: AsyncSession, name: str, request_id: str) -> GroupLabel:
    existing = (await session.execute(select(GroupLabel).where(GroupLabel.name == name))).scalar_one_or_none()
    if existing is not None:
        raise AccessPilotError("GROUP_LABEL_NAME_TAKEN", "This label is already in the list.", 409)
    row = GroupLabel(name=name)
    session.add(row)
    await session.flush()
    await record_audit(session, action="GROUP_LABEL_CREATED", target_type="GROUP_LABEL", target_id=row.id, request_id=request_id, metadata={"name": name})
    await session.commit()
    await session.refresh(row)
    return row


async def delete_group_label(session: AsyncSession, group_label_id: UUID, request_id: str) -> None:
    row = await session.get(GroupLabel, group_label_id)
    if row is None:
        raise AccessPilotError("GROUP_LABEL_NOT_FOUND", "The label was not found.", 404)
    await record_audit(session, action="GROUP_LABEL_DELETED", target_type="GROUP_LABEL", target_id=row.id, request_id=request_id, metadata={"name": row.name})
    await session.delete(row)
    await session.commit()
