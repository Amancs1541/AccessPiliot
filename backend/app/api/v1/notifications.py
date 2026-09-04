from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.notifications import NotificationResponse
from app.security.auth import AuthenticatedUser, require_authenticated_user
from app.services import notifications as notification_service
from app.services.assignments import _resolve_internal_user_id

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("", response_model=list[NotificationResponse])
async def list_notifications(actor: AuthenticatedUser = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    """Every authenticated user's own notifications — assignment/approval lifecycle events addressed to them
    specifically. Unlike the SoD notification log, there is no permission gate beyond being signed in, since
    every row here is already scoped to the caller's own internal user id."""
    user_id = await _resolve_internal_user_id(db, actor.directory_object_id)
    if user_id is None:
        return []
    return await notification_service.list_my_notifications(db, user_id)


@router.post("/{notification_id}/read", status_code=204)
async def mark_read(notification_id: UUID, actor: AuthenticatedUser = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    user_id = await _resolve_internal_user_id(db, actor.directory_object_id)
    if user_id is not None:
        await notification_service.mark_notification_read(db, user_id, notification_id)
    return None


@router.post("/read-all", status_code=204)
async def mark_all_read(actor: AuthenticatedUser = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    user_id = await _resolve_internal_user_id(db, actor.directory_object_id)
    if user_id is not None:
        await notification_service.mark_all_notifications_read(db, user_id)
    return None
