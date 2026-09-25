from __future__ import annotations

import logging
import calendar
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Optional
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AccessPilotError
from app.models import AccessAssignment, AccessPackage, AccessPackageAssignment, AccessPackageOwner, AccessReviewCampaign, AccessReviewItem, Application, ApplicationOwner, Group, GroupOwner, User
from app.providers.entra import EntraProvider
from app.providers.graph_client import GraphError
from app.schemas.access_reviews import AccessReviewCampaignCreate, AccessReviewCampaignUpdate, AccessReviewDashboard, AccessReviewItemDecide, ResourceTally, ScopeTargetItem
from app.services.assignments import _resolve_internal_user_id, _resolve_target, revoke_assignment
from app.services.audit import record_audit
from app.services.notifications import create_notification
from app.services.provider_configuration import _connector
from app.services.provisioning import primary_identity_provider
from app.services.security_settings import get_security_settings

logger = logging.getLogger("accesspilot.access_reviews")

NON_FINAL_ASSIGNMENT_STATUSES = ("ELIGIBLE", "ACTIVE")
REVOKED_DECISIONS = ("REVOKED", "AUTO_REVOKED")
_REVIEW_ADMIN_ROLES = ("AccessPilot.Admin", "AccessPilot.AccessReviewAdmin")


async def _resolve_display_name(session: AsyncSession, resource_type: str, resource_id: UUID, app_role_external_id: Optional[str]) -> Optional[str]:
    if resource_type == "PACKAGE":
        # _resolve_target doesn't know about PACKAGE (see _validate_target_exists) — an AccessReviewItem's own
        # resource_type is never PACKAGE (see _matching_assignments_for_target's docstring), but a campaign's own
        # scope_targets list can legitimately contain one, so this needs to resolve it too.
        package = await session.get(AccessPackage, resource_id)
        return package.name if package else None
    try:
        _, name, _ = await _resolve_target(session, resource_type, resource_id)
    except AccessPilotError:
        return None
    if resource_type == "APPLICATION" and app_role_external_id:
        from app.models import Application
        from app.services.assignments import _app_role_name
        application = await session.get(Application, resource_id)
        role_name = _app_role_name(application, app_role_external_id)
        if role_name:
            return f"{name} — {role_name}"
    return name


async def _matching_assignments_for_target(session: AsyncSession, resource_type: str, resource_id: UUID) -> list[AccessAssignment]:
    """Every currently non-final assignment for ONE specific target. PACKAGE is special: AccessAssignment.
    resource_type is always the real underlying GROUP/ROLE/APPLICATION item, never literally "PACKAGE" — a
    package-sourced grant is only identifiable via the AccessPackageAssignment join table (see
    app.services.packages), so this means "every assignment that came from THIS package" rather than a
    resource_type/resource_id equality filter. Shared by both SPECIFIC_RESOURCE (one target) and
    MULTIPLE_RESOURCES (several, possibly mixed-type, targets in one campaign) so there's exactly one place this
    per-target resolution logic lives."""
    stmt = select(AccessAssignment).where(AccessAssignment.status.in_(NON_FINAL_ASSIGNMENT_STATUSES))
    if resource_type == "PACKAGE":
        stmt = stmt.join(AccessPackageAssignment, AccessPackageAssignment.assignment_id == AccessAssignment.id).where(AccessPackageAssignment.package_id == resource_id)
    else:
        stmt = stmt.where(AccessAssignment.resource_type == resource_type, AccessAssignment.resource_id == resource_id)
    return list((await session.scalars(stmt)).all())


def compute_next_run(after: datetime, day: int, time_str: str, every_months: int, tz_name: str) -> datetime:
    """Next scheduled start strictly after `after` (UTC-aware in, UTC-aware out): day-of-month `day` (clamped to
    the month's length, so 31 means "last day" in short months) at `time_str` in the app's configured timezone,
    stepping `every_months` months from the month `after` falls in — the current month's slot if it is still
    ahead, otherwise `every_months` months on."""
    tz = ZoneInfo(tz_name)
    hour, minute = (int(part) for part in time_str.split(":"))
    local_after = after.astimezone(tz)

    def slot(year: int, month: int) -> datetime:
        return datetime(year, month, min(day, calendar.monthrange(year, month)[1]), hour, minute, tzinfo=tz)

    year, month = local_after.year, local_after.month
    candidate = slot(year, month)
    while candidate.astimezone(timezone.utc) <= after:
        month += every_months
        year += (month - 1) // 12
        month = (month - 1) % 12 + 1
        candidate = slot(year, month)
    return candidate.astimezone(timezone.utc)


async def _app_timezone(session: AsyncSession) -> str:
    return (await get_security_settings(session)).timezone


async def _resolve_inactive_user_ids(session: AsyncSession, candidate_user_ids: set[UUID], threshold_days: int) -> set[UUID]:
    """Best-effort: which of these users have no real Entra sign-in within threshold_days (or have never signed
    in at all). Needs Microsoft Graph AuditLog.Read.All, which is NOT confirmed granted on this tenant as of
    2026-09-25 (see reference-entra-permissions memory / privileged_accounts._live_sign_in_activity, the same
    call this reuses). Fails FAST with a clear, actionable error the moment the very first lookup is denied for
    that reason — looping through every remaining candidate hitting the identical tenant-wide 403 would be both
    slow and misleading (a silently-empty campaign would look like "nobody's inactive" instead of "can't tell").
    A lookup failure for an unrelated reason (one bad record, transient error) only excludes that one user,
    the same conservative "can't prove it, don't flag it" choice _live_sign_in_activity's own callers make."""
    if not candidate_user_ids:
        return set()
    provider = await primary_identity_provider(session)
    connector = _connector(provider) if provider else None
    if not isinstance(connector, EntraProvider):
        raise AccessPilotError("INACTIVE_USER_CHECK_UNAVAILABLE", "Inactive-user detection needs a live Microsoft Entra connection, which isn't configured.", 424)
    users = list((await session.scalars(select(User).where(User.id.in_(candidate_user_ids)))).all())
    now = datetime.now(timezone.utc)
    inactive: set[UUID] = set()
    for index, user in enumerate(users):
        try:
            activity = await connector.get_user_sign_in_activity(user.external_id)
        except GraphError as exc:
            if index == 0:
                raise AccessPilotError("INACTIVE_USER_CHECK_UNAVAILABLE", "This tenant hasn't granted Microsoft Graph AuditLog.Read.All, so last-sign-in data can't be read. Grant that permission in Entra to use the Inactive Users scope.", 424) from exc
            logger.warning("Sign-in activity lookup failed for %s during inactive-user scope resolution: %s", user.external_id, exc)
            continue
        last_raw = (activity or {}).get("last_sign_in_at") or (activity or {}).get("last_non_interactive_sign_in_at")
        if not last_raw:
            inactive.add(user.id)
            continue
        last_dt = datetime.fromisoformat(last_raw.replace("Z", "+00:00"))
        if (now - last_dt) >= timedelta(days=threshold_days):
            inactive.add(user.id)
    return inactive


async def _matching_assignments(session: AsyncSession, data: AccessReviewCampaignCreate) -> list[AccessAssignment]:
    """Every currently non-final (ELIGIBLE/ACTIVE) AccessAssignment matching the campaign's scope — a one-time
    snapshot at campaign-creation time, deliberately not re-evaluated later (see AccessReviewCampaign's own
    docstring for why: a review certifies a fixed roster as of the moment it started)."""
    if data.scope_type == "SPECIFIC_RESOURCE":
        return await _matching_assignments_for_target(session, data.scope_resource_type, data.scope_resource_id)
    if data.scope_type == "MULTIPLE_RESOURCES":
        # Mixing resource types (e.g. a Group and a Package reviewed together) means no single SQL WHERE clause
        # covers every target uniformly — resolve each target independently and dedupe by assignment id, in case
        # the same real assignment is ever reachable through two overlapping targets.
        seen_ids: set[UUID] = set()
        combined: list[AccessAssignment] = []
        for target in data.scope_targets:
            for assignment in await _matching_assignments_for_target(session, target.resource_type, target.resource_id):
                if assignment.id not in seen_ids:
                    seen_ids.add(assignment.id)
                    combined.append(assignment)
        return combined

    if data.scope_type == "INACTIVE_USERS":
        # Every non-final assignment first, then narrow to holders who haven't signed in within the threshold —
        # this way we only ever call Graph once per DISTINCT user actually in scope, never every tenant user.
        all_assignments = list((await session.scalars(select(AccessAssignment).where(AccessAssignment.status.in_(NON_FINAL_ASSIGNMENT_STATUSES)))).all())
        candidate_user_ids = {assignment.user_id for assignment in all_assignments}
        inactive_user_ids = await _resolve_inactive_user_ids(session, candidate_user_ids, data.scope_inactive_days)
        return [assignment for assignment in all_assignments if assignment.user_id in inactive_user_ids]

    stmt = select(AccessAssignment).where(AccessAssignment.status.in_(NON_FINAL_ASSIGNMENT_STATUSES))
    if data.scope_type == "RESOURCE_TYPE":
        if data.scope_resource_type == "PACKAGE":
            stmt = stmt.join(AccessPackageAssignment, AccessPackageAssignment.assignment_id == AccessAssignment.id)
        else:
            stmt = stmt.where(AccessAssignment.resource_type == data.scope_resource_type)
    elif data.scope_type == "USER":
        stmt = stmt.where(AccessAssignment.user_id == data.scope_user_id)
    elif data.scope_type == "ACCOUNT_TYPE":
        stmt = stmt.join(User, User.id == AccessAssignment.user_id).where(User.account_type == data.scope_account_type)
    # ALL: no further filter.
    return list((await session.scalars(stmt)).all())


async def _validate_target_exists(session: AsyncSession, resource_type: str, resource_id: UUID) -> None:
    """_resolve_target doesn't know about PACKAGE (see app.services.birthright's identical local check) —
    validate it exists here instead. Raises a 404 AccessPilotError if the target doesn't exist."""
    if resource_type == "PACKAGE":
        if await session.get(AccessPackage, resource_id) is None:
            raise AccessPilotError("PACKAGE_NOT_FOUND", "The access package was not found.", 404)
    else:
        await _resolve_target(session, resource_type, resource_id)  # 404s if missing


async def create_campaign(session: AsyncSession, data: AccessReviewCampaignCreate, actor_subject: str, request_id: str) -> AccessReviewCampaign:
    reviewer = await session.get(User, data.reviewer_id)
    if reviewer is None:
        raise AccessPilotError("USER_NOT_FOUND", "The selected reviewer was not found.", 404)
    if data.fallback_reviewer_id is not None and await session.get(User, data.fallback_reviewer_id) is None:
        raise AccessPilotError("USER_NOT_FOUND", "The selected fallback reviewer was not found.", 404)
    if data.scope_type == "USER" and await session.get(User, data.scope_user_id) is None:
        raise AccessPilotError("USER_NOT_FOUND", "The user this campaign is scoped to was not found.", 404)
    if data.scope_type == "SPECIFIC_RESOURCE":
        await _validate_target_exists(session, data.scope_resource_type, data.scope_resource_id)
    scope_targets_json = None
    if data.scope_type == "MULTIPLE_RESOURCES":
        for target in data.scope_targets:
            await _validate_target_exists(session, target.resource_type, target.resource_id)
        scope_targets_json = [{"resource_type": t.resource_type, "resource_id": str(t.resource_id)} for t in data.scope_targets]

    created_by = await _resolve_internal_user_id(session, actor_subject)
    campaign = AccessReviewCampaign(
        name=data.name, description=data.description, scope_type=data.scope_type,
        scope_resource_type=data.scope_resource_type, scope_resource_id=data.scope_resource_id, scope_targets=scope_targets_json,
        scope_user_id=data.scope_user_id, scope_account_type=data.scope_account_type, scope_inactive_days=data.scope_inactive_days,
        reviewer_id=data.reviewer_id, fallback_reviewer_id=data.fallback_reviewer_id, fallback_unlock_hours=data.fallback_unlock_hours,
        status="ACTIVE", due_at=data.due_at, frequency_days=data.frequency_days, created_by=created_by,
    )
    if data.schedule_day_of_month is not None:
        now = datetime.now(timezone.utc)
        due_at = data.due_at if data.due_at.tzinfo else data.due_at.replace(tzinfo=timezone.utc)
        campaign.schedule_day_of_month = data.schedule_day_of_month
        campaign.schedule_time = data.schedule_time
        campaign.schedule_every_months = data.schedule_every_months or 1
        campaign.schedule_due_days = data.schedule_due_days or max(1, (due_at - now).days)
        campaign.next_run_at = compute_next_run(now, campaign.schedule_day_of_month, campaign.schedule_time, campaign.schedule_every_months, await _app_timezone(session))
    session.add(campaign)
    await session.flush()

    assignments = await _matching_assignments(session, data)
    for assignment in assignments:
        session.add(AccessReviewItem(
            campaign_id=campaign.id, assignment_id=assignment.id, user_id=assignment.user_id,
            resource_type=assignment.resource_type, resource_id=assignment.resource_id, app_role_external_id=assignment.app_role_external_id,
            assignment_status_at_snapshot=assignment.status, decision="PENDING",
        ))
    await record_audit(session, action="ACCESS_REVIEW_CAMPAIGN_CREATED", target_type="ACCESS_REVIEW_CAMPAIGN", target_id=campaign.id, actor_user_id=created_by, request_id=request_id, metadata={"name": campaign.name, "scope_type": campaign.scope_type, "item_count": len(assignments)})
    await create_notification(session, data.reviewer_id, "ACCESS_REVIEW_ASSIGNED", f"You've been assigned {len(assignments)} item{'s' if len(assignments) != 1 else ''} to review in \"{campaign.name}\", due {campaign.due_at.strftime('%Y-%m-%d')}.", link="/access-reviews/mine")
    if data.fallback_reviewer_id is not None:
        await create_notification(session, data.fallback_reviewer_id, "ACCESS_REVIEW_ASSIGNED", f"You're the fallback reviewer for \"{campaign.name}\" ({len(assignments)} item{'s' if len(assignments) != 1 else ''}), due {campaign.due_at.strftime('%Y-%m-%d')}.", link="/access-reviews/mine")
    await session.commit()
    await session.refresh(campaign)
    return campaign


async def _get_campaign(session: AsyncSession, campaign_id: UUID) -> AccessReviewCampaign:
    campaign = await session.get(AccessReviewCampaign, campaign_id)
    if campaign is None:
        raise AccessPilotError("ACCESS_REVIEW_CAMPAIGN_NOT_FOUND", "The access review campaign was not found.", 404)
    return campaign


async def list_campaigns(session: AsyncSession) -> list[AccessReviewCampaign]:
    return list((await session.scalars(select(AccessReviewCampaign).order_by(AccessReviewCampaign.created_at.desc()))).all())


async def update_campaign(session: AsyncSession, campaign_id: UUID, data: AccessReviewCampaignUpdate, actor_subject: str, request_id: str) -> AccessReviewCampaign:
    """Edits a campaign's own metadata — name/description, reviewer, fallback reviewer + unlock hours, due date.
    Never touches scope or its already-snapshotted items (see AccessReviewCampaignUpdate's own docstring for
    why). Reassigning the reviewer takes effect immediately for any still-PENDING item, since
    _authorize_review_decision always reads the campaign's CURRENT reviewer_id/fallback_reviewer_id, never a
    value cached on the item itself."""
    campaign = await _get_campaign(session, campaign_id)
    if campaign.status != "ACTIVE":
        raise AccessPilotError("REQUEST_ALREADY_PROCESSED", "A closed campaign can no longer be edited.", 409)

    changes: dict[str, object] = {}
    if data.name is not None:
        campaign.name = data.name
        changes["name"] = data.name
    if data.description is not None:
        campaign.description = data.description
        changes["description"] = data.description
    new_reviewer_id = None
    if data.reviewer_id is not None and data.reviewer_id != campaign.reviewer_id:
        reviewer = await session.get(User, data.reviewer_id)
        if reviewer is None:
            raise AccessPilotError("USER_NOT_FOUND", "The selected reviewer was not found.", 404)
        campaign.reviewer_id = data.reviewer_id
        changes["reviewer_id"] = str(data.reviewer_id)
        new_reviewer_id = data.reviewer_id
    new_fallback_id = None
    if data.clear_fallback_reviewer:
        campaign.fallback_reviewer_id = None
        campaign.fallback_unlock_hours = None
        changes["fallback_reviewer_id"] = None
    elif data.fallback_reviewer_id is not None and data.fallback_reviewer_id != campaign.fallback_reviewer_id:
        fallback = await session.get(User, data.fallback_reviewer_id)
        if fallback is None:
            raise AccessPilotError("USER_NOT_FOUND", "The selected fallback reviewer was not found.", 404)
        campaign.fallback_reviewer_id = data.fallback_reviewer_id
        changes["fallback_reviewer_id"] = str(data.fallback_reviewer_id)
        new_fallback_id = data.fallback_reviewer_id
    if data.fallback_unlock_hours is not None:
        campaign.fallback_unlock_hours = data.fallback_unlock_hours
        changes["fallback_unlock_hours"] = data.fallback_unlock_hours
    if data.due_at is not None:
        campaign.due_at = data.due_at
        changes["due_at"] = data.due_at.isoformat()
    if data.clear_frequency:
        campaign.frequency_days = None
        changes["frequency_days"] = None
    elif data.frequency_days is not None:
        campaign.frequency_days = data.frequency_days
        changes["frequency_days"] = data.frequency_days
    if data.clear_schedule:
        campaign.schedule_day_of_month = campaign.schedule_time = campaign.schedule_every_months = campaign.schedule_due_days = campaign.next_run_at = None
        changes["schedule"] = None
    elif data.schedule_day_of_month is not None or data.schedule_time is not None:
        if data.schedule_day_of_month is None or data.schedule_time is None:
            raise AccessPilotError("VALIDATION_ERROR", "A day of the month and a time must be provided together.", 422)
        campaign.schedule_day_of_month = data.schedule_day_of_month
        campaign.schedule_time = data.schedule_time
        campaign.schedule_every_months = data.schedule_every_months or campaign.schedule_every_months or 1
        campaign.schedule_due_days = data.schedule_due_days or campaign.schedule_due_days or 30
        campaign.frequency_days = None  # a fixed schedule and repeat-after-completion are mutually exclusive
        campaign.next_run_at = compute_next_run(datetime.now(timezone.utc), campaign.schedule_day_of_month, campaign.schedule_time, campaign.schedule_every_months, await _app_timezone(session))
        changes["schedule"] = f"day {campaign.schedule_day_of_month} at {campaign.schedule_time} every {campaign.schedule_every_months} month(s)"
    if campaign.frequency_days is not None and campaign.schedule_day_of_month is not None and not data.clear_schedule and data.schedule_day_of_month is None:
        # frequency was just (re)set on a campaign that had a schedule — the newer choice wins
        campaign.schedule_day_of_month = campaign.schedule_time = campaign.schedule_every_months = campaign.schedule_due_days = campaign.next_run_at = None

    actor_id = await _resolve_internal_user_id(session, actor_subject)
    if changes:
        await record_audit(session, action="ACCESS_REVIEW_CAMPAIGN_UPDATED", target_type="ACCESS_REVIEW_CAMPAIGN", target_id=campaign.id, actor_user_id=actor_id, request_id=request_id, metadata=changes)
    # A newly (re)assigned reviewer/fallback wasn't necessarily told about this campaign yet — same notification
    # create_campaign already sends, just triggered by the reassignment instead of the initial creation.
    if new_reviewer_id is not None:
        total, decided = await campaign_progress(session, campaign.id)
        await create_notification(session, new_reviewer_id, "ACCESS_REVIEW_ASSIGNED", f"You've been assigned as reviewer for \"{campaign.name}\" ({total - decided} item{'s' if total - decided != 1 else ''} still pending), due {campaign.due_at.strftime('%Y-%m-%d')}.", link="/access-reviews/mine")
    if new_fallback_id is not None:
        await create_notification(session, new_fallback_id, "ACCESS_REVIEW_ASSIGNED", f"You're now the fallback reviewer for \"{campaign.name}\", due {campaign.due_at.strftime('%Y-%m-%d')}.", link="/access-reviews/mine")
    await session.commit()
    await session.refresh(campaign)
    return campaign


async def campaign_progress(session: AsyncSession, campaign_id: UUID) -> tuple[int, int]:
    items = list((await session.scalars(select(AccessReviewItem).where(AccessReviewItem.campaign_id == campaign_id))).all())
    decided = sum(1 for item in items if item.decision != "PENDING")
    return len(items), decided


async def campaign_outcomes(session: AsyncSession, campaign_id: UUID) -> dict[str, int]:
    """Item counts by outcome for one campaign: approved / revoked (a reviewer took the access away) /
    auto_revoked (removed because the deadline passed or the campaign was closed with the item undecided) /
    pending. Kept separate on purpose — lumping the two kinds of removal together reads as "everything was
    removed" even when most items were approved."""
    rows = (await session.execute(select(AccessReviewItem.decision, func.count()).where(AccessReviewItem.campaign_id == campaign_id).group_by(AccessReviewItem.decision))).all()
    counts = {decision: count for decision, count in rows}
    return {"approved": counts.get("APPROVED", 0), "revoked": counts.get("REVOKED", 0), "auto_revoked": counts.get("AUTO_REVOKED", 0), "pending": counts.get("PENDING", 0)}


async def list_campaign_items(session: AsyncSession, campaign_id: UUID) -> list[AccessReviewItem]:
    await _get_campaign(session, campaign_id)  # 404s if missing
    return list((await session.scalars(select(AccessReviewItem).where(AccessReviewItem.campaign_id == campaign_id).order_by(AccessReviewItem.created_at))).all())


async def list_my_review_items(session: AsyncSession, actor_id: UUID) -> list[AccessReviewItem]:
    """Same shape as list_my_approvals (assignments.py) — every PENDING item across every campaign where the
    caller is the reviewer or the fallback reviewer, newest campaign first."""
    stmt = (
        select(AccessReviewItem)
        .join(AccessReviewCampaign, AccessReviewCampaign.id == AccessReviewItem.campaign_id)
        .where(AccessReviewItem.decision == "PENDING", or_(AccessReviewCampaign.reviewer_id == actor_id, AccessReviewCampaign.fallback_reviewer_id == actor_id))
        .order_by(AccessReviewItem.created_at)
    )
    return list((await session.scalars(stmt)).all())


async def _authorize_review_decision(session: AsyncSession, campaign: AccessReviewCampaign, actor_subject: str, actor_roles: tuple[str, ...]) -> Optional[UUID]:
    """Same fallback-after-unlock shape as _authorize_decision (assignments.py) — the campaign's own reviewer, an
    Admin/AccessReviewAdmin, or the fallback reviewer once fallback_unlock_hours has elapsed since the campaign
    started."""
    actor_id = await _resolve_internal_user_id(session, actor_subject)
    if any(role in actor_roles for role in _REVIEW_ADMIN_ROLES):
        return actor_id
    if actor_id is not None and actor_id == campaign.reviewer_id:
        return actor_id
    if actor_id is not None and actor_id == campaign.fallback_reviewer_id:
        if campaign.fallback_unlock_hours is not None:
            unlock_at = campaign.created_at.replace(tzinfo=timezone.utc) if campaign.created_at.tzinfo is None else campaign.created_at
            from datetime import timedelta
            unlock_at = unlock_at + timedelta(hours=campaign.fallback_unlock_hours)
            if datetime.now(timezone.utc) < unlock_at:
                raise AccessPilotError("FALLBACK_NOT_YET_AVAILABLE", f"The fallback reviewer may only act after {unlock_at.isoformat()}.", 403)
        return actor_id
    raise AccessPilotError("ACCESS_DENIED", "Only the designated reviewer, the fallback reviewer (once eligible), or an administrator can decide this item.", 403)


async def _maybe_spawn_recurrence(session: AsyncSession, campaign: AccessReviewCampaign, request_id: str) -> None:
    """Called once a campaign transitions to COMPLETED, from any of the three paths that can do that (every item
    decided, manual early completion, or the overdue-sweep worker). If the campaign was recurring
    (frequency_days set), snapshots a fresh campaign with the identical scope/reviewer/fallback, due
    frequency_days from now, linked back via parent_campaign_id — so a periodic review never lapses just because
    nobody manually re-created it. Reuses create_campaign() itself rather than duplicating its snapshot logic.
    A failed spawn (e.g. a SPECIFIC_RESOURCE target deleted since the last run) notifies the reviewer instead of
    silently breaking the recurrence chain."""
    if not campaign.frequency_days:
        return
    scope_targets = None
    if campaign.scope_targets:
        scope_targets = [ScopeTargetItem(resource_type=t["resource_type"], resource_id=UUID(t["resource_id"])) for t in campaign.scope_targets]
    next_data = AccessReviewCampaignCreate(
        name=campaign.name, description=campaign.description, scope_type=campaign.scope_type,
        scope_resource_type=campaign.scope_resource_type, scope_resource_id=campaign.scope_resource_id, scope_targets=scope_targets,
        scope_user_id=campaign.scope_user_id, scope_account_type=campaign.scope_account_type, scope_inactive_days=campaign.scope_inactive_days,
        reviewer_id=campaign.reviewer_id, fallback_reviewer_id=campaign.fallback_reviewer_id, fallback_unlock_hours=campaign.fallback_unlock_hours,
        due_at=datetime.now(timezone.utc) + timedelta(days=campaign.frequency_days), frequency_days=campaign.frequency_days,
    )
    try:
        next_campaign = await create_campaign(session, next_data, "system:access-review-recurrence", f"{request_id}-recurrence")
    except AccessPilotError as exc:
        logger.warning("Recurrence spawn failed for campaign %s: %s", campaign.id, exc)
        await create_notification(session, campaign.reviewer_id, "ACCESS_REVIEW_RECURRENCE_FAILED", f"\"{campaign.name}\" recurs every {campaign.frequency_days} days, but the next campaign couldn't be created automatically ({exc.message}). Create it by hand if it's still needed.", link="/access-reviews/mine")
        await session.commit()
        return
    next_campaign.parent_campaign_id = campaign.id
    # Lets whoever owns this recurring review know one cycle just wrapped up and the next one is already on the
    # calendar — so they can plan other campaigns/work around it instead of finding out only when it's due again.
    recipients = {campaign.reviewer_id}
    if campaign.created_by is not None:
        recipients.add(campaign.created_by)
    for recipient_id in recipients:
        await create_notification(session, recipient_id, "ACCESS_REVIEW_RECURRENCE_CREATED", f"\"{campaign.name}\" completed its {campaign.frequency_days}-day review cycle — the next one is already scheduled, due {next_campaign.due_at.strftime('%Y-%m-%d')}.", link="/admin/access-reviews")
    await session.commit()


async def decide_item(session: AsyncSession, item_id: UUID, data: AccessReviewItemDecide, actor_subject: str, actor_roles: tuple[str, ...], request_id: str) -> AccessReviewItem:
    item = await session.get(AccessReviewItem, item_id)
    if item is None:
        raise AccessPilotError("ACCESS_REVIEW_ITEM_NOT_FOUND", "The access review item was not found.", 404)
    if item.decision != "PENDING":
        raise AccessPilotError("REQUEST_ALREADY_PROCESSED", "This item has already been decided.", 409)
    campaign = await _get_campaign(session, item.campaign_id)
    actor_id = await _authorize_review_decision(session, campaign, actor_subject, actor_roles)

    if data.decision == "REVOKED":
        # Reuses the existing, unmodified universal revoke path — real Entra/Graph removal, its own
        # ASSIGNMENT_REVOKED audit entry, the target-user notification, and (for a GROUP) its existing cascade
        # into linked GroupRoleMapping assignments, all for free.
        try:
            await revoke_assignment(session, item.assignment_id, actor_subject, data.justification, request_id, reason="ACCESS_REVIEW_REVOKED")
        except AccessPilotError as exc:
            if exc.code != "REQUEST_ALREADY_PROCESSED":  # already revoked/expired independently — still record the review decision
                raise

    item.decision = data.decision
    item.decided_by = actor_id
    item.decided_at = datetime.now(timezone.utc)
    item.justification = data.justification
    await record_audit(session, action="ACCESS_REVIEW_ITEM_DECIDED", target_type="ACCESS_REVIEW_ITEM", target_id=item.id, actor_user_id=actor_id, request_id=request_id, metadata={"decision": data.decision, "campaign_id": str(campaign.id)})
    # Lets whoever created the campaign watch it progress without needing the page open — separate from
    # revoke_assignment's own notification to the AFFECTED user (item.user_id), which fires independently.
    if campaign.created_by is not None and campaign.created_by != actor_id:
        resource_name = await _resolve_display_name(session, item.resource_type, item.resource_id, item.app_role_external_id)
        verb = "approved" if data.decision == "APPROVED" else "revoked"
        await create_notification(session, campaign.created_by, "ACCESS_REVIEW_ITEM_DECIDED", f"An item in \"{campaign.name}\" was {verb}: {resource_name or item.resource_type} for the reviewed user.", link=f"/admin/access-reviews/{campaign.id}")
    await session.commit()
    await session.refresh(item)

    total, decided = await campaign_progress(session, campaign.id)
    if decided >= total and campaign.status == "ACTIVE":
        campaign.status = "COMPLETED"
        campaign.completed_at = datetime.now(timezone.utc)
        await session.commit()
        await _maybe_spawn_recurrence(session, campaign, request_id)
    return item


async def complete_campaign(session: AsyncSession, campaign_id: UUID, actor_subject: str, request_id: str) -> AccessReviewCampaign:
    """Manual early completion — applies the same auto-revoke-on-pending the 60s worker would apply once due_at
    passes, immediately, then closes the campaign. One bad target never blocks the rest."""
    campaign = await _get_campaign(session, campaign_id)
    if campaign.status != "ACTIVE":
        raise AccessPilotError("REQUEST_ALREADY_PROCESSED", "This campaign is already closed.", 409)
    pending_items = list((await session.scalars(select(AccessReviewItem).where(AccessReviewItem.campaign_id == campaign_id, AccessReviewItem.decision == "PENDING"))).all())
    for item in pending_items:
        try:
            await revoke_assignment(session, item.assignment_id, actor_subject, "Access review campaign closed with no decision — auto-revoked.", request_id, reason="ACCESS_REVIEW_AUTO_REVOKED")
        except AccessPilotError:
            pass
        item.decision = "AUTO_REVOKED"
        item.decided_at = datetime.now(timezone.utc)
    campaign.status = "COMPLETED"
    campaign.completed_at = datetime.now(timezone.utc)
    # actor_id resolves to None for the worker's own synthetic "system:access-review-worker" subject (no User row
    # ever has that external_id) and to the real admin's id for a manual completion — the same actor_user_id IS
    # NULL signal every other worker-vs-human distinction in this app already relies on (see
    # list_system_generated_audit_logs), not a hardcoded "manual": True flag standing in for it.
    actor_id = await _resolve_internal_user_id(session, actor_subject)
    await record_audit(session, action="ACCESS_REVIEW_CAMPAIGN_COMPLETED", target_type="ACCESS_REVIEW_CAMPAIGN", target_id=campaign.id, actor_user_id=actor_id, request_id=request_id, metadata={"auto_revoked_count": len(pending_items)})
    await session.commit()
    await session.refresh(campaign)
    await _maybe_spawn_recurrence(session, campaign, request_id)
    return campaign


async def sweep_overdue_campaigns(session: AsyncSession) -> int:
    """Background-worker entry point (see workers/access_review.py, polled every 60s): every ACTIVE campaign past
    its due_at gets every still-PENDING item auto-revoked and the campaign closed — the same real revoke path,
    the same one-bad-target-never-blocks-the-rest discipline as every other reconciliation loop this app has."""
    now = datetime.now(timezone.utc)
    overdue = list((await session.scalars(select(AccessReviewCampaign).where(AccessReviewCampaign.status == "ACTIVE", AccessReviewCampaign.due_at <= now))).all())
    completed_count = 0
    for campaign in overdue:
        try:
            await complete_campaign(session, campaign.id, "system:access-review-worker", f"access-review-worker-{campaign.id}")
            completed_count += 1
        except AccessPilotError:
            continue
    return completed_count


async def get_dashboard_summary(session: AsyncSession) -> AccessReviewDashboard:
    """Live-computed, never stored — same "recompute on every read" convention as every other dashboard panel in
    this app (Dashboard's own StatCards, SoD's violations). Deliberately no "high risk users" figure: this app
    has no sign-in-risk data source to back one honestly (see AccessReview.md)."""
    total_campaigns = (await session.execute(select(func.count()).select_from(AccessReviewCampaign))).scalar_one()
    active_campaigns = (await session.execute(select(func.count()).select_from(AccessReviewCampaign).where(AccessReviewCampaign.status == "ACTIVE"))).scalar_one()
    completed_campaigns = (await session.execute(select(func.count()).select_from(AccessReviewCampaign).where(AccessReviewCampaign.status == "COMPLETED"))).scalar_one()
    recurring_campaigns = (await session.execute(select(func.count()).select_from(AccessReviewCampaign).where(AccessReviewCampaign.frequency_days.is_not(None)))).scalar_one()
    total_items = (await session.execute(select(func.count()).select_from(AccessReviewItem))).scalar_one()
    pending_items = (await session.execute(select(func.count()).select_from(AccessReviewItem).where(AccessReviewItem.decision == "PENDING"))).scalar_one()
    approved_items = (await session.execute(select(func.count()).select_from(AccessReviewItem).where(AccessReviewItem.decision == "APPROVED"))).scalar_one()
    revoked_items = (await session.execute(select(func.count()).select_from(AccessReviewItem).where(AccessReviewItem.decision == "REVOKED"))).scalar_one()
    auto_revoked_items = (await session.execute(select(func.count()).select_from(AccessReviewItem).where(AccessReviewItem.decision == "AUTO_REVOKED"))).scalar_one()

    async def _top_resources(resource_type: str) -> list[ResourceTally]:
        rows = (await session.execute(
            select(AccessReviewItem.resource_id, func.count().label("cnt"))
            .where(AccessReviewItem.resource_type == resource_type)
            .group_by(AccessReviewItem.resource_id)
            .order_by(func.count().desc())
            .limit(5)
        )).all()
        tallies: list[ResourceTally] = []
        for resource_id, count in rows:
            name = await _resolve_display_name(session, resource_type, resource_id, None)
            tallies.append(ResourceTally(name=name or str(resource_id), count=count))
        return tallies

    return AccessReviewDashboard(
        total_campaigns=total_campaigns, active_campaigns=active_campaigns, completed_campaigns=completed_campaigns,
        recurring_campaigns=recurring_campaigns, total_items=total_items, pending_items=pending_items,
        approved_items=approved_items, revoked_items=revoked_items, auto_revoked_items=auto_revoked_items,
        top_groups=await _top_resources("GROUP"), top_applications=await _top_resources("APPLICATION"),
    )


async def suggest_owner_reviewers(session: AsyncSession, resource_type: str, resource_id: UUID) -> list[dict]:
    """Reviewer suggestions for a campaign scoped to one Package / Application / Group: the resource's owners.
    PACKAGE and APPLICATION owners are AccessPilot's own records; a GROUP's owners are read live from Entra
    (best-effort — an unavailable lookup just yields no suggestion). Returns [{user_id, display_name, source}]."""
    suggestions: list[dict] = []
    if resource_type == "PACKAGE":
        rows = (await session.scalars(select(AccessPackageOwner).where(AccessPackageOwner.package_id == resource_id))).all()
        source = "Package owner"
        user_ids = [row.user_id for row in rows]
    elif resource_type == "APPLICATION":
        rows = (await session.scalars(select(ApplicationOwner).where(ApplicationOwner.application_id == resource_id))).all()
        source = "Application owner"
        user_ids = [row.user_id for row in rows]
    elif resource_type == "GROUP":
        source = "Group owner (Entra)"
        internal = (await session.scalars(select(GroupOwner).where(GroupOwner.group_id == resource_id))).all()
        for row in internal:
            user = await session.get(User, row.user_id)
            if user is not None:
                suggestions.append({"user_id": user.id, "display_name": user.display_name, "source": "Group owner"})
        user_ids = []
        group = await session.get(Group, resource_id)
        provider = await primary_identity_provider(session)
        connector = _connector(provider) if provider else None
        if group is not None and isinstance(connector, EntraProvider):
            try:
                external_ids = await connector.get_group_owner_ids(group.external_id)
            except GraphError as exc:
                logger.warning("Group owner lookup failed for %s: %s", group.external_id, exc)
                external_ids = []
            if external_ids:
                user_ids = [u.id for u in (await session.scalars(select(User).where(User.external_id.in_(external_ids)))).all()]
    else:
        return []
    seen = {suggestion["user_id"] for suggestion in suggestions}
    for user_id in user_ids:
        user = await session.get(User, user_id)
        if user is not None and user.id not in seen:
            suggestions.append({"user_id": user.id, "display_name": user.display_name, "source": source})
    return suggestions


async def sweep_scheduled_campaigns(session: AsyncSession) -> int:
    """Worker entry point for FIXED-CALENDAR recurrence ("the 15th of every month at 09:00"): whichever campaign
    currently holds a due next_run_at starts the next campaign in its chain — same scope/reviewer/fallback, a
    fresh snapshot of who holds access right now, open for schedule_due_days. If the previous cycle is still
    ACTIVE when its next slot arrives, that occurrence is skipped (never two open at once) and the reviewer is
    told. A failed start (e.g. a since-deleted target) notifies the reviewer and retries at the next slot.
    Returns how many campaigns were started."""
    now = datetime.now(timezone.utc)
    tz_name = await _app_timezone(session)
    holders = list((await session.scalars(select(AccessReviewCampaign).where(AccessReviewCampaign.next_run_at.is_not(None), AccessReviewCampaign.next_run_at <= now, AccessReviewCampaign.schedule_day_of_month.is_not(None)))).all())
    started = 0
    for holder in holders:
        def following() -> datetime:
            return compute_next_run(now, holder.schedule_day_of_month, holder.schedule_time, holder.schedule_every_months or 1, tz_name)

        if holder.status == "ACTIVE":
            holder.next_run_at = following()
            await create_notification(session, holder.reviewer_id, "ACCESS_REVIEW_SCHEDULE_SKIPPED", f"The scheduled start of \"{holder.name}\" was skipped because the previous review is still open. Next attempt: {holder.next_run_at.strftime('%Y-%m-%d %H:%M')} UTC.", link="/admin/access-reviews")
            await session.commit()
            continue
        scope_targets = [ScopeTargetItem(resource_type=t["resource_type"], resource_id=UUID(t["resource_id"])) for t in holder.scope_targets] if holder.scope_targets else None
        due_days = holder.schedule_due_days or 30
        next_data = AccessReviewCampaignCreate(
            name=holder.name, description=holder.description, scope_type=holder.scope_type,
            scope_resource_type=holder.scope_resource_type, scope_resource_id=holder.scope_resource_id, scope_targets=scope_targets,
            scope_user_id=holder.scope_user_id, scope_account_type=holder.scope_account_type, scope_inactive_days=holder.scope_inactive_days,
            reviewer_id=holder.reviewer_id, fallback_reviewer_id=holder.fallback_reviewer_id, fallback_unlock_hours=holder.fallback_unlock_hours,
            due_at=now + timedelta(days=due_days),
            schedule_day_of_month=holder.schedule_day_of_month, schedule_time=holder.schedule_time, schedule_every_months=holder.schedule_every_months or 1, schedule_due_days=due_days,
        )
        try:
            new_campaign = await create_campaign(session, next_data, "system:access-review-schedule", f"access-review-schedule-{holder.id}")
        except AccessPilotError as exc:
            logger.warning("Scheduled start failed for campaign %s: %s", holder.id, exc)
            holder.next_run_at = following()
            await create_notification(session, holder.reviewer_id, "ACCESS_REVIEW_RECURRENCE_FAILED", f"The scheduled review \"{holder.name}\" could not start automatically ({exc.message}). It will try again at its next slot.", link="/admin/access-reviews")
            await session.commit()
            continue
        new_campaign.parent_campaign_id = holder.id
        holder.next_run_at = None  # the schedule now lives on the new campaign
        recipients = {holder.reviewer_id}
        if holder.created_by is not None:
            recipients.add(holder.created_by)
        for recipient_id in recipients:
            await create_notification(session, recipient_id, "ACCESS_REVIEW_RECURRENCE_CREATED", f"The scheduled review \"{holder.name}\" has started, due {new_campaign.due_at.strftime('%Y-%m-%d')}.", link="/admin/access-reviews")
        await session.commit()
        started += 1
    return started
