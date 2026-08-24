from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AccessPilotError
from app.models import Application, Group, Role, User, UserGroup


async def list_users(session: AsyncSession, query: Optional[str] = None) -> list[User]:
    statement = select(User).order_by(User.display_name)
    if query:
        like = f"%{query}%"
        statement = statement.where(User.display_name.ilike(like) | User.email.ilike(like))
    return list((await session.scalars(statement)).all())


async def get_user(session: AsyncSession, user_id: UUID) -> User:
    user = await session.get(User, user_id)
    if not user:
        raise AccessPilotError("USER_NOT_FOUND", "The user was not found.", 404)
    return user


async def list_groups(session: AsyncSession, query: Optional[str] = None) -> list[Group]:
    statement = select(Group).order_by(Group.name)
    if query:
        statement = statement.where(Group.name.ilike(f"%{query}%"))
    return list((await session.scalars(statement)).all())


async def get_group(session: AsyncSession, group_id: UUID) -> Group:
    group = await session.get(Group, group_id)
    if not group:
        raise AccessPilotError("GROUP_NOT_FOUND", "The group was not found.", 404)
    return group


async def list_group_members(session: AsyncSession, group_id: UUID) -> list[User]:
    await get_group(session, group_id)
    statement = select(User).join(UserGroup, UserGroup.user_id == User.id).where(UserGroup.group_id == group_id).order_by(User.display_name)
    return list((await session.scalars(statement)).all())


async def list_roles(session: AsyncSession, query: Optional[str] = None) -> list[Role]:
    statement = select(Role).order_by(Role.name)
    if query:
        statement = statement.where(Role.name.ilike(f"%{query}%"))
    return list((await session.scalars(statement)).all())


async def list_applications(session: AsyncSession, query: Optional[str] = None) -> list[Application]:
    statement = select(Application).order_by(Application.name)
    if query:
        statement = statement.where(Application.name.ilike(f"%{query}%"))
    return list((await session.scalars(statement)).all())
