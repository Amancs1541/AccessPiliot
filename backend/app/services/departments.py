from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AccessPilotError
from app.models import Department
from app.services.audit import record_audit


async def list_departments(session: AsyncSession) -> list[Department]:
    return list((await session.execute(select(Department).order_by(Department.name))).scalars().all())


async def create_department(session: AsyncSession, name: str, request_id: str) -> Department:
    existing = (await session.execute(select(Department).where(Department.name == name))).scalar_one_or_none()
    if existing is not None:
        raise AccessPilotError("DEPARTMENT_NAME_TAKEN", "This department is already in the list.", 409)
    row = Department(name=name)
    session.add(row)
    await session.flush()
    await record_audit(session, action="DEPARTMENT_CREATED", target_type="DEPARTMENT", target_id=row.id, request_id=request_id, metadata={"name": name})
    await session.commit()
    await session.refresh(row)
    return row


async def delete_department(session: AsyncSession, department_id: UUID, request_id: str) -> None:
    row = await session.get(Department, department_id)
    if row is None:
        raise AccessPilotError("DEPARTMENT_NOT_FOUND", "The department was not found.", 404)
    await record_audit(session, action="DEPARTMENT_DELETED", target_type="DEPARTMENT", target_id=row.id, request_id=request_id, metadata={"name": row.name})
    await session.delete(row)
    await session.commit()
