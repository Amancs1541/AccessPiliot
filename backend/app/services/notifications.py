from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AccessPilotError
from app.models import Notification


async def create_notification(session: AsyncSession, user_id: UUID, notification_type: str, message: str, link: str | None = None) -> None:
    """Adds the row to the session but does not commit — callers create these alongside the real state change
    they're announcing (assignment created/approved/rejected/etc.) and commit once at the end of that existing
    transaction, exactly like record_audit()'s own calling convention elsewhere in this app."""
    session.add(Notification(user_id=user_id, notification_type=notification_type, message=message, link=link))


async def list_my_notifications(session: AsyncSession, user_id: UUID) -> list[Notification]:
    stmt = select(Notification).where(Notification.user_id == user_id).order_by(Notification.created_at.desc()).limit(100)
    return list((await session.scalars(stmt)).all())


async def mark_notification_read(session: AsyncSession, user_id: UUID, notification_id: UUID) -> None:
    notification = await session.get(Notification, notification_id)
    if notification is None or notification.user_id != user_id:
        raise AccessPilotError("NOTIFICATION_NOT_FOUND", "This notification was not found.", 404)
    if notification.read_at is None:
        notification.read_at = datetime.now(timezone.utc)
        await session.commit()


async def mark_all_notifications_read(session: AsyncSession, user_id: UUID) -> None:
    await session.execute(update(Notification).where(Notification.user_id == user_id, Notification.read_at.is_(None)).values(read_at=datetime.now(timezone.utc)))
    await session.commit()
