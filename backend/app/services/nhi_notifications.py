from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AccessPilotError
from app.models import Application, NhiNotification, NhiNotificationSettings, User
from app.schemas.nhi import NhiNotificationResponse, NhiNotificationSettingsUpdateRequest
from app.services.nhi import RISK_CREDENTIAL_EXPIRED, RISK_CREDENTIAL_EXPIRING_SOON, RISK_NO_OWNER, list_non_human_identities
from app.services.notifications import create_notification

# Maps each shared-log notification_type to (a) the live risk flag it tracks, (b) whether there's a specific
# owner to also personally notify (NO_OWNER has nobody — nothing to notify personally about), and (c) the
# message fragment used for both the shared log and the personal ping.
_RISK_BY_TYPE = {"NO_OWNER": RISK_NO_OWNER, "CREDENTIAL_EXPIRING": RISK_CREDENTIAL_EXPIRING_SOON, "CREDENTIAL_EXPIRED": RISK_CREDENTIAL_EXPIRED}
_NOTIFIES_OWNER = {"NO_OWNER": False, "CREDENTIAL_EXPIRING": True, "CREDENTIAL_EXPIRED": True}
_MESSAGE_FRAGMENT = {"NO_OWNER": "has no owner assigned", "CREDENTIAL_EXPIRING": "has a credential expiring soon", "CREDENTIAL_EXPIRED": "has an expired credential"}


async def get_nhi_notification_settings(session: AsyncSession) -> NhiNotificationSettings:
    """Singleton — creates the one row with its defaults (all three notify toggles ON, 30-day credential-expiry
    warning — replacing nhi.py's previously-fixed CREDENTIAL_EXPIRING_SOON_WINDOW_DAYS constant) on first ever
    read, matching the SodNotificationSettings/SecuritySettings get-or-create convention."""
    settings = (await session.execute(select(NhiNotificationSettings))).scalars().first()
    if settings is None:
        settings = NhiNotificationSettings()
        session.add(settings)
        await session.commit()
        await session.refresh(settings)
    return settings


async def update_nhi_notification_settings(session: AsyncSession, data: NhiNotificationSettingsUpdateRequest) -> NhiNotificationSettings:
    settings = await get_nhi_notification_settings(session)
    settings.notify_on_no_owner = data.notify_on_no_owner
    settings.notify_on_credential_expiring = data.notify_on_credential_expiring
    settings.credential_expiring_warning_days = data.credential_expiring_warning_days
    settings.notify_on_credential_expired = data.notify_on_credential_expired
    await session.commit()
    await session.refresh(settings)
    return settings


async def reconcile_nhi_notifications(session: AsyncSession) -> None:
    """Creates/resolves NhiNotification rows (the shared log, visible to the NHIAdmin team) to match current
    reality, and — for the two credential risk types, where there IS a specific person to tell — also creates a
    personal Notification for each current owner via the existing generic create_notification() helper, at the
    exact moment the shared row is newly created (never re-fired on a later poll, since by then the application
    is already in already_notified_application_ids — same dedup gate covers both). Same "opportunistic, on-read,
    no new worker" philosophy as reconcile_sod_notifications (app.services.sod): runs once, whenever
    GET /nhi/notifications is called (or piggy-backed on GET /nhi/summary), never a separate scheduler — so
    notifications are only as fresh as the last time someone (or the Bell icon's unread-count fetch) checked."""
    settings = await get_nhi_notification_settings(session)
    now = datetime.now(timezone.utc)
    identities = await list_non_human_identities(session)
    toggles = {"NO_OWNER": settings.notify_on_no_owner, "CREDENTIAL_EXPIRING": settings.notify_on_credential_expiring, "CREDENTIAL_EXPIRED": settings.notify_on_credential_expired}

    for notification_type, risk_type in _RISK_BY_TYPE.items():
        if not toggles[notification_type]:
            continue
        open_application_ids = {identity["id"] for identity in identities if risk_type in identity["risk_flags"]}
        open_notifications = list((await session.scalars(select(NhiNotification).where(NhiNotification.notification_type == notification_type, NhiNotification.resolved_at.is_(None)))).all())
        already_notified_application_ids: set[UUID] = set()
        for note in open_notifications:
            if note.application_id not in open_application_ids:
                note.resolved_at = now
            else:
                already_notified_application_ids.add(note.application_id)
        for identity in identities:
            if risk_type not in identity["risk_flags"] or identity["id"] in already_notified_application_ids:
                continue
            session.add(NhiNotification(notification_type=notification_type, application_id=identity["id"], owner_user_id=identity["owners"][0]["user_id"] if (_NOTIFIES_OWNER[notification_type] and identity["owners"]) else None, message=f"\"{identity['name']}\" {_MESSAGE_FRAGMENT[notification_type]}."))
            if _NOTIFIES_OWNER[notification_type]:
                for owner in identity["owners"]:
                    await create_notification(session, owner["user_id"], f"NHI_{notification_type}", f"Your non-human identity \"{identity['name']}\" {_MESSAGE_FRAGMENT[notification_type]}.", link=f"/admin/nhi/{identity['id']}")
        await session.commit()


async def hydrate_nhi_notification(session: AsyncSession, notification: NhiNotification) -> NhiNotificationResponse:
    application = await session.get(Application, notification.application_id)
    owner = await session.get(User, notification.owner_user_id) if notification.owner_user_id else None
    return NhiNotificationResponse(
        id=notification.id, notification_type=notification.notification_type, application_id=notification.application_id,
        application_name=application.name if application else None, owner_user_id=notification.owner_user_id,
        owner_display_name=owner.display_name if owner else None, message=notification.message,
        read_at=notification.read_at, resolved_at=notification.resolved_at, created_at=notification.created_at,
    )


async def list_nhi_notifications(session: AsyncSession, reconcile: bool = True) -> list[NhiNotificationResponse]:
    if reconcile:
        await reconcile_nhi_notifications(session)
    rows = list((await session.scalars(select(NhiNotification).order_by(NhiNotification.created_at.desc()))).all())
    return [await hydrate_nhi_notification(session, row) for row in rows]


async def mark_nhi_notification_read(session: AsyncSession, notification_id: UUID) -> None:
    notification = await session.get(NhiNotification, notification_id)
    if notification is None:
        raise AccessPilotError("NHI_NOTIFICATION_NOT_FOUND", "The notification was not found.", 404)
    if notification.read_at is None:
        notification.read_at = datetime.now(timezone.utc)
        await session.commit()


async def mark_all_nhi_notifications_read(session: AsyncSession) -> None:
    unread = list((await session.scalars(select(NhiNotification).where(NhiNotification.read_at.is_(None)))).all())
    now = datetime.now(timezone.utc)
    for notification in unread:
        notification.read_at = now
    if unread:
        await session.commit()
