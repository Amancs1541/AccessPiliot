from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import func

from app.models import AccessAssignment, AccessReviewCampaign, AccessReviewItem, Group, IdentityProvider, LeaverPolicy, LifecycleEvent, LifecycleSettings, PendingMove, User, UserGroup
from app.schemas.access_reviews import AccessReviewCampaignCreate
from app.services.access_reviews import _matching_assignments, create_campaign
from app.core.errors import AccessPilotError
from app.providers.graph_client import GraphError
from app.schemas.lifecycle import EMPLOYMENT_TYPES, LeaverPolicyCreate, LeaverPolicyUpdate, LifecycleEventResponse, LifecycleOwnerInfo, LifecycleSettingsResponse, LifecycleSettingsUpdate, PendingMoveResponse, PersonLifecycleResponse, PersonLifecycleUpdate, ScheduledLeaverResponse
from app.services.provider_configuration import _connector
from app.services.notifications import create_notification
from app.services.audit import record_audit

logger = logging.getLogger("accesspilot.lifecycle")

MOVER_FIELDS = ("department", "job_title")
LIFECYCLE_ACTOR = "system:lifecycle"


async def get_lifecycle_settings(session: AsyncSession) -> LifecycleSettings:
    """Singleton — created with its defaults (review on, due in 14 days, no owners) on first read."""
    settings = (await session.execute(select(LifecycleSettings))).scalars().first()
    if settings is None:
        settings = LifecycleSettings(lifecycle_owner_ids=[])
        session.add(settings)
        await session.commit()
        await session.refresh(settings)
    return settings


def build_changes(before: dict, after: dict) -> dict:
    """{field: {"from", "to"}} for every field whose value differs (case-insensitive for email)."""
    changes: dict = {}
    for field, old in before.items():
        new = after.get(field)
        same = (str(old or "").lower() == str(new or "").lower()) if field == "email" else (old == new)
        if not same:
            changes[field] = {"from": old, "to": new}
    return changes


def _label(value: Optional[str]) -> str:
    return value if value else "—"


async def _start_mover_review(session: AsyncSession, user: User, changes: dict, settings: LifecycleSettings) -> tuple[Optional[AccessReviewCampaign], Optional[str]]:
    """Starts the review of a mover's leftover (non-policy-granted) access. Returns (campaign, None) or
    (None, reason) — the reason lands on the LifecycleEvent so the report can say why nothing was started."""
    owners = [UUID(str(owner)) for owner in (settings.lifecycle_owner_ids or [])]
    owners = [owner for owner in owners if owner != user.id]
    reviewer_id = user.manager_id if user.manager_id and user.manager_id != user.id else (owners[0] if owners else None)
    if reviewer_id is None:
        return None, "NO_REVIEWER"
    fallback_id = next((owner for owner in owners if owner != reviewer_id), None)

    already_open = (await session.scalars(select(AccessReviewCampaign.id).where(AccessReviewCampaign.scope_type == "MOVER", AccessReviewCampaign.scope_user_id == user.id, AccessReviewCampaign.status == "ACTIVE"))).first()
    if already_open is not None:
        return None, "REVIEW_ALREADY_OPEN"

    moved = [f"{field.replace('_', ' ')}: {_label(change['from'])} → {_label(change['to'])}" for field, change in changes.items() if field in MOVER_FIELDS]
    data = AccessReviewCampaignCreate(
        name=f"Mover review — {user.display_name} ({'; '.join(moved)})"[:255],
        description="Automatically started because this person's department or job title changed. Access that no policy granted (manual, package or requested), including any linked privileged (PU) or test (TU) accounts, is re-certified here.",
        scope_type="MOVER", scope_user_id=user.id, reviewer_id=reviewer_id,
        fallback_reviewer_id=fallback_id, fallback_unlock_hours=72 if fallback_id else None,
        due_at=datetime.now(timezone.utc) + timedelta(days=settings.review_due_days),
    )
    if not await _matching_assignments(session, data):
        return None, "NO_LEFTOVER_ACCESS"
    return await create_campaign(session, data, LIFECYCLE_ACTOR, f"lifecycle-mover-{user.id}"), None


async def record_mover(session: AsyncSession, user_id: UUID, changes: Optional[dict], source: str, outcome: Optional[dict], request_id: str) -> Optional[LifecycleEvent]:
    """The one entry point every change source calls AFTER it has run the birthright reconcile. Decides whether the
    change is a mover (department/job title changed away from a real previous value, on an active NORMAL account),
    records a LifecycleEvent, and starts the leftover-access review. Anything else returns None untouched: a first-
    time fill of an empty department, status/email-only changes, PU/TU shadow accounts, disabled accounts."""
    if not changes:
        return None
    moved = {field: change for field, change in changes.items() if field in MOVER_FIELDS and change.get("from")}
    if not moved:
        return None
    user = await session.get(User, user_id)
    if user is None or user.status != "ACTIVE" or user.account_type != "NORMAL":
        return None

    settings = await get_lifecycle_settings(session)
    campaign, note = (await _start_mover_review(session, user, changes, settings)) if settings.mover_review_enabled else (None, "REVIEW_DISABLED")
    event = LifecycleEvent(
        user_id=user.id, event_type="MOVER", source=source, changes=changes,
        revoked_count=len((outcome or {}).get("revoked", [])), granted_count=len((outcome or {}).get("granted", [])),
        review_campaign_id=campaign.id if campaign else None, review_note=note,
    )
    if campaign is not None:
        event.privileged_flagged_count = (await session.execute(select(func.count()).select_from(AccessReviewItem).where(AccessReviewItem.campaign_id == campaign.id, AccessReviewItem.user_id != user.id))).scalar_one()
    session.add(event)
    await session.flush()
    # Tell the mover's manager and every lifecycle owner (deduped, never the mover) what happened.
    recipients = list(dict.fromkeys([r for r in [user.manager_id, *[UUID(str(o)) for o in (settings.lifecycle_owner_ids or [])]] if r is not None and r != user.id]))
    if recipients:
        what = "; ".join(f"{field.replace('_', ' ')} {_label(change['from'])} to {_label(change['to'])}" for field, change in moved.items())
        review = f"a review of their remaining access is due {campaign.due_at.strftime('%Y-%m-%d')}" if campaign else {"NO_LEFTOVER_ACCESS": "no other access needs review", "NO_REVIEWER": "no reviewer could be assigned (set a manager or a lifecycle owner)", "REVIEW_ALREADY_OPEN": "a review is already open", "REVIEW_DISABLED": "automatic review is switched off"}.get(note, "no review was started")
        flagged = f" {event.privileged_flagged_count} item(s) on their linked privileged/test accounts are included in the review." if event.privileged_flagged_count else ""
        text = f"{user.display_name} moved ({what}): {event.revoked_count} access removed, {event.granted_count} new eligible; {review}.{flagged}"
        for recipient_id in recipients:
            await create_notification(session, recipient_id, "LIFECYCLE_MOVER", text, link="/admin/movers")
        event.notified_user_ids = [str(r) for r in recipients]
    await record_audit(session, action="LIFECYCLE_MOVER_DETECTED", target_type="USER", target_id=user.id, request_id=request_id, metadata={"source": source, "changes": changes, "review_started": campaign is not None, "review_note": note})
    await session.commit()
    await session.refresh(event)
    return event


async def settings_response(session: AsyncSession) -> LifecycleSettingsResponse:
    settings = await get_lifecycle_settings(session)
    owners: list[LifecycleOwnerInfo] = []
    for raw in settings.lifecycle_owner_ids or []:
        user = await session.get(User, UUID(str(raw)))
        if user is not None:
            owners.append(LifecycleOwnerInfo(user_id=user.id, display_name=user.display_name, email=user.email))
    return LifecycleSettingsResponse(mover_review_enabled=settings.mover_review_enabled, revoke_on_directory_disable=settings.revoke_on_directory_disable, review_due_days=settings.review_due_days, lifecycle_owners=owners, joiner_provisioning_delay_days=settings.joiner_provisioning_delay_days)


async def update_settings(session: AsyncSession, data: LifecycleSettingsUpdate, request_id: str) -> LifecycleSettingsResponse:
    settings = await get_lifecycle_settings(session)
    if data.mover_review_enabled is not None:
        settings.mover_review_enabled = data.mover_review_enabled
    if data.review_due_days is not None:
        settings.review_due_days = data.review_due_days
    if data.revoke_on_directory_disable is not None:
        settings.revoke_on_directory_disable = data.revoke_on_directory_disable
    if data.lifecycle_owner_ids is not None:
        unique_ids = list(dict.fromkeys(data.lifecycle_owner_ids))
        for user_id in unique_ids:
            if await session.get(User, user_id) is None:
                raise AccessPilotError("USER_NOT_FOUND", "One of the selected lifecycle owners was not found.", 404)
        settings.lifecycle_owner_ids = [str(user_id) for user_id in unique_ids]
    if data.clear_joiner_provisioning_delay_days:
        settings.joiner_provisioning_delay_days = None
    elif data.joiner_provisioning_delay_days is not None:
        settings.joiner_provisioning_delay_days = data.joiner_provisioning_delay_days
    await record_audit(session, action="LIFECYCLE_SETTINGS_UPDATED", target_type="LIFECYCLE_SETTINGS", target_id=settings.id, request_id=request_id, metadata={"mover_review_enabled": settings.mover_review_enabled, "review_due_days": settings.review_due_days, "owner_count": len(settings.lifecycle_owner_ids or []), "joiner_provisioning_delay_days": settings.joiner_provisioning_delay_days})
    await session.commit()
    return await settings_response(session)


async def list_events(session: AsyncSession, event_type: Optional[str] = None, limit: int = 200) -> list[LifecycleEventResponse]:
    """Newest first, fully hydrated for the Movers report (person, review progress, who was notified)."""
    from app.services.access_reviews import campaign_progress

    stmt = select(LifecycleEvent).order_by(LifecycleEvent.created_at.desc()).limit(limit)
    if event_type:
        stmt = stmt.where(LifecycleEvent.event_type == event_type)
    responses: list[LifecycleEventResponse] = []
    for event in (await session.scalars(stmt)).all():
        user = await session.get(User, event.user_id)
        campaign = await session.get(AccessReviewCampaign, event.review_campaign_id) if event.review_campaign_id else None
        reviewer = await session.get(User, campaign.reviewer_id) if campaign else None
        total, decided = await campaign_progress(session, campaign.id) if campaign else (0, 0)
        notified_names = []
        for raw in event.notified_user_ids or []:
            recipient = await session.get(User, UUID(str(raw)))
            if recipient is not None:
                notified_names.append(recipient.display_name)
        responses.append(LifecycleEventResponse(
            id=event.id, event_type=event.event_type, source=event.source, created_at=event.created_at, user_id=event.user_id,
            user_display_name=user.display_name if user else None, user_email=user.email if user else None, changes=event.changes or {},
            revoked_count=event.revoked_count, granted_count=event.granted_count, privileged_flagged_count=event.privileged_flagged_count, review_campaign_id=event.review_campaign_id,
            review_campaign_name=campaign.name if campaign else None, review_status=campaign.status if campaign else None,
            review_reviewer_name=reviewer.display_name if reviewer else None, review_item_count=total, review_decided_count=decided,
            review_note=event.review_note, notified=notified_names,
        ))
    return responses


# ---------------------------------------------------------------- effective-dated moves

async def schedule_move(session: AsyncSession, user_id: UUID, department: Optional[str], job_title: Optional[str], effective_at: datetime, source: str, actor_subject: str, request_id: str, import_id: Optional[UUID] = None) -> PendingMove:
    """Schedules a department/job-title change for a FUTURE moment. Nothing changes now. A newer schedule for the
    same person replaces (cancels) any still-scheduled one."""
    from app.services.assignments import _resolve_internal_user_id

    user = await session.get(User, user_id)
    if user is None:
        raise AccessPilotError("USER_NOT_FOUND", "The user was not found.", 404)
    if user.account_type != "NORMAL":
        raise AccessPilotError("VALIDATION_ERROR", "Privileged (PU) and test (TU) accounts cannot be moved.", 422)
    if department is None and job_title is None:
        raise AccessPilotError("VALIDATION_ERROR", "Provide a new department and/or job title.", 422)
    if effective_at.tzinfo is None:
        effective_at = effective_at.replace(tzinfo=timezone.utc)
    if effective_at <= datetime.now(timezone.utc):
        raise AccessPilotError("VALIDATION_ERROR", "The effective date must be in the future; edit the person directly for an immediate change.", 422)
    actor_id = await _resolve_internal_user_id(session, actor_subject)
    for earlier in (await session.scalars(select(PendingMove).where(PendingMove.user_id == user_id, PendingMove.status == "SCHEDULED"))).all():
        earlier.status = "CANCELLED"
        earlier.failure_reason = "SUPERSEDED"
    move = PendingMove(user_id=user_id, new_department=department, new_job_title=job_title, effective_at=effective_at, source=source, created_by=actor_id, import_id=import_id)
    session.add(move)
    await session.flush()
    await record_audit(session, action="LIFECYCLE_MOVE_SCHEDULED", target_type="USER", target_id=user_id, actor_user_id=actor_id, request_id=request_id, metadata={"department": department, "job_title": job_title, "effective_at": effective_at.isoformat(), "source": source})
    await session.commit()
    await session.refresh(move)
    return move


async def cancel_move(session: AsyncSession, move_id: UUID, actor_subject: str, request_id: str) -> PendingMove:
    from app.services.assignments import _resolve_internal_user_id

    move = await session.get(PendingMove, move_id)
    if move is None:
        raise AccessPilotError("MOVE_NOT_FOUND", "The scheduled move was not found.", 404)
    if move.status != "SCHEDULED":
        raise AccessPilotError("REQUEST_ALREADY_PROCESSED", "Only a still-scheduled move can be cancelled.", 409)
    move.status = "CANCELLED"
    move.failure_reason = "CANCELLED_BY_ADMIN"
    await record_audit(session, action="LIFECYCLE_MOVE_CANCELLED", target_type="USER", target_id=move.user_id, actor_user_id=await _resolve_internal_user_id(session, actor_subject), request_id=request_id)
    await session.commit()
    await session.refresh(move)
    return move


async def apply_move(session: AsyncSession, move: PendingMove, request_id: str) -> None:
    """Applies a due scheduled move as ONE change: push the new attributes to the person's directory provider (like
    an in-app edit, so the next sync agrees), update the local row, run the birthright reconcile, then record the
    mover event / start the leftover-access review. A provider failure marks the move FAILED (visible in the
    report) and changes nothing."""
    from app.services.birthright import reconcile_birthright_policies_for_user

    user = await session.get(User, move.user_id)
    if user is None:
        move.status, move.failure_reason = "FAILED", "USER_NOT_FOUND"
        await session.commit()
        return
    before = {"department": user.department, "job_title": user.job_title}
    new_department = move.new_department if move.new_department is not None else user.department
    new_job_title = move.new_job_title if move.new_job_title is not None else user.job_title
    provider = await session.get(IdentityProvider, user.provider_id)
    if provider is not None and provider.type != "CSV":
        try:
            await _connector(provider).update_user(user.external_id, department=new_department, job_title=new_job_title)
        except GraphError as exc:
            move.status, move.failure_reason = "FAILED", f"{exc.code}: {exc.message}"[:500]
            await record_audit(session, action="LIFECYCLE_MOVE_FAILED", target_type="USER", target_id=user.id, request_id=request_id, metadata={"reason": move.failure_reason})
            await session.commit()
            return
    user.department, user.job_title = new_department, new_job_title
    await session.commit()
    outcome = await reconcile_birthright_policies_for_user(session, user.id, "system:lifecycle", request_id)
    event = await record_mover(session, user.id, build_changes(before, {"department": new_department, "job_title": new_job_title}), move.source, outcome, request_id)
    move.status, move.applied_at = "APPLIED", datetime.now(timezone.utc)
    move.lifecycle_event_id = event.id if event else None
    await record_audit(session, action="LIFECYCLE_MOVE_APPLIED", target_type="USER", target_id=user.id, request_id=request_id, metadata={"effective_at": move.effective_at.isoformat(), "source": move.source})
    await session.commit()


async def sweep_pending_moves(session: AsyncSession) -> int:
    """Worker entry point: applies every SCHEDULED move whose effective_at has arrived. One failing move never
    blocks the others."""
    now = datetime.now(timezone.utc)
    due = list((await session.scalars(select(PendingMove).where(PendingMove.status == "SCHEDULED", PendingMove.effective_at <= now).order_by(PendingMove.effective_at))).all())
    applied = 0
    for move in due:
        try:
            await apply_move(session, move, f"lifecycle-move-{move.id}")
            applied += 1 if move.status == "APPLIED" else 0
        except AccessPilotError as exc:
            logger.warning("Scheduled move %s failed: %s", move.id, exc)
            await session.rollback()
            failed = await session.get(PendingMove, move.id)
            if failed is not None:
                failed.status, failed.failure_reason = "FAILED", f"{exc.code}: {exc.message}"[:500]
                await session.commit()
    return applied


async def list_moves(session: AsyncSession, status: Optional[str] = None) -> list[PendingMoveResponse]:
    stmt = select(PendingMove).order_by(PendingMove.effective_at)
    if status:
        stmt = stmt.where(PendingMove.status == status)
    rows: list[PendingMoveResponse] = []
    for move in (await session.scalars(stmt)).all():
        user = await session.get(User, move.user_id)
        rows.append(PendingMoveResponse(
            id=move.id, user_id=move.user_id, user_display_name=user.display_name if user else None, user_email=user.email if user else None,
            current_department=user.department if user else None, current_job_title=user.job_title if user else None,
            new_department=move.new_department, new_job_title=move.new_job_title, effective_at=move.effective_at, source=move.source,
            status=move.status, failure_reason=move.failure_reason, created_at=move.created_at, applied_at=move.applied_at,
        ))
    return rows


# ---------------------------------------------------------------- joiners and leavers

async def _notify_lifecycle(session: AsyncSession, user: User, settings: LifecycleSettings, notification_type: str, text: str) -> list[str]:
    recipients = list(dict.fromkeys([r for r in [user.manager_id, *[UUID(str(o)) for o in (settings.lifecycle_owner_ids or [])]] if r is not None and r != user.id]))
    for recipient_id in recipients:
        await create_notification(session, recipient_id, notification_type, text, link="/admin/movers")
    return [str(r) for r in recipients]


async def record_joiner(session: AsyncSession, user_id: UUID, source: str, outcome: Optional[dict], request_id: str) -> Optional[LifecycleEvent]:
    """A new person first seen by directory sync or created by a CSV import — recorded (with how much birthright
    access they were made eligible for) so the report is a complete joiner/mover/leaver history. No notification:
    a joiner is routine, and they are not reviewed."""
    user = await session.get(User, user_id)
    if user is None or user.account_type != "NORMAL":
        return None
    event = LifecycleEvent(user_id=user.id, event_type="JOINER", source=source, changes={"status": {"from": None, "to": user.status}}, revoked_count=0, granted_count=len((outcome or {}).get("granted", [])))
    session.add(event)
    await session.flush()
    await record_audit(session, action="LIFECYCLE_JOINER_DETECTED", target_type="USER", target_id=user.id, request_id=request_id, metadata={"source": source, "granted": event.granted_count})
    await session.commit()
    await session.refresh(event)
    return event


async def record_leaver(session: AsyncSession, user_id: UUID, source: str, revoked_count: int, note: Optional[str], request_id: str, linked_accounts_disabled: int = 0) -> Optional[LifecycleEvent]:
    """Records a leaver (someone disabled in the directory, or terminated via CSV) and tells the manager and every
    lifecycle owner. `note` says why nothing was revoked when that is the case (AUTO_REVOKE_OFF)."""
    user = await session.get(User, user_id)
    if user is None or user.account_type != "NORMAL":
        return None
    settings = await get_lifecycle_settings(session)
    event = LifecycleEvent(user_id=user.id, event_type="LEAVER", source=source, changes={"status": {"from": "ACTIVE", "to": "DISABLED"}, "linked_accounts_disabled": linked_accounts_disabled}, revoked_count=revoked_count, granted_count=0, review_note=note)
    session.add(event)
    await session.flush()
    what = f"{revoked_count} access removed" if note is None else "automatic revocation is switched off, no access was removed"
    linked = f"; {linked_accounts_disabled} linked privileged/test account(s) disabled" if linked_accounts_disabled else ""
    event.notified_user_ids = await _notify_lifecycle(session, user, settings, "LIFECYCLE_LEAVER", f"{user.display_name} left (disabled in the directory): {what}{linked}.")
    await record_audit(session, action="LIFECYCLE_LEAVER_DETECTED", target_type="USER", target_id=user.id, request_id=request_id, metadata={"source": source, "revoked": revoked_count, "note": note, "linked_accounts_disabled": linked_accounts_disabled})
    await session.commit()
    await session.refresh(event)
    return event


async def handle_directory_disable(session: AsyncSession, user_id: UUID, changes: Optional[dict], request_id: str, already_revoked: int = 0) -> Optional[LifecycleEvent]:
    """Directory sync saw this person go ACTIVE -> DISABLED. With revoke_on_directory_disable on (default) every one
    of their non-final assignments is revoked through the same universal revoke path the CSV leaver flow uses (so
    real Entra access goes too) and their linked PU/TU accounts are disabled; off = the event is recorded only,
    because a temporary disable in the directory would otherwise strip access."""
    status = (changes or {}).get("status")
    if not status or status.get("from") != "ACTIVE" or status.get("to") == "ACTIVE":
        return None
    user = await session.get(User, user_id)
    if user is None or user.account_type != "NORMAL":
        return None
    settings = await get_lifecycle_settings(session)
    if not settings.revoke_on_directory_disable:
        return await record_leaver(session, user_id, "SYNC", already_revoked, "AUTO_REVOKE_OFF", request_id)
    # The directory that reported the disable already has it off; every OTHER IdP account is disabled by the leaver runner.
    return await run_leaver(session, user_id, "SYNC", "system:lifecycle", request_id, already_revoked=already_revoked, skip_provider_ids={user.provider_id})


async def handle_leaver_reactivated(session: AsyncSession, user_id: UUID, changes: Optional[dict], request_id: str) -> bool:
    """Directory sync saw a person who already went through the leaver process become ACTIVE again - someone enabled
    the account directly in the directory, bypassing the reason + manager approval. Nothing is changed automatically;
    the manager and lifecycle owners are alerted and the event is audited."""
    status = (changes or {}).get("status")
    if not status or status.get("to") != "ACTIVE" or status.get("from") == "ACTIVE":
        return False
    user = await session.get(User, user_id)
    if user is None or user.account_type != "NORMAL" or user.leaver_processed_at is None:
        return False
    settings = await get_lifecycle_settings(session)
    await _notify_lifecycle(session, user, settings, "LIFECYCLE_LEAVER_REACTIVATED", f"{user.display_name} already left, but their account was enabled again directly in the directory (not through AccessPilot's approval). Check whether this is intended.")
    await record_audit(session, action="LEAVER_ACCOUNT_REACTIVATED_EXTERNALLY", target_type="USER", target_id=user_id, request_id=request_id, metadata={"leaver_processed_at": user.leaver_processed_at.isoformat()})
    await session.commit()
    return True


# ---------------------------------------------------------------- leaver policies and the ONE leaver runner

SOURCE_TEXT = {"SCHEDULED": "leaver date reached", "SYNC": "disabled in the directory", "CSV": "terminated via CSV", "MANUAL": "started manually"}


def _scope_matches(policy: LeaverPolicy, user: User) -> bool:
    if policy.scope_type == "ALL":
        return True
    if policy.scope_type == "DEPARTMENT":
        return bool(user.department) and (policy.scope_value or "").strip().lower() == user.department.strip().lower()
    if policy.scope_type == "EMPLOYMENT_TYPE":
        return bool(user.employment_type) and (policy.scope_value or "").strip().upper() == user.employment_type.upper()
    return False


async def get_default_leaver_policy(session: AsyncSession) -> LeaverPolicy:
    """The seeded Default (created by migration 0057; created here too if it is ever missing) — covers everyone no
    scoped policy matches."""
    policy = (await session.scalars(select(LeaverPolicy).where(LeaverPolicy.is_default.is_(True)))).first()
    if policy is None:
        policy = LeaverPolicy(name="Default leaver policy", priority=1000, scope_type="ALL", notify_days_before=[7, 1], is_default=True)
        session.add(policy)
        await session.commit()
        await session.refresh(policy)
    return policy


async def resolve_leaver_policy(session: AsyncSession, user: User) -> LeaverPolicy:
    """First ACTIVE scoped policy by priority (then name) whose scope matches this person; else the Default."""
    candidates = (await session.scalars(select(LeaverPolicy).where(LeaverPolicy.status == "ACTIVE", LeaverPolicy.is_default.is_(False)).order_by(LeaverPolicy.priority, LeaverPolicy.name))).all()
    for policy in candidates:
        if _scope_matches(policy, user):
            return policy
    return await get_default_leaver_policy(session)


def leaver_due_at(leaver_date, effective_time: str, tz_name: str) -> datetime:
    """The moment (UTC) the leaver process runs: the leaver date at the policy's time of day in the app timezone."""
    from zoneinfo import ZoneInfo

    hour, minute = (int(part) for part in effective_time.split(":"))
    return datetime(leaver_date.year, leaver_date.month, leaver_date.day, hour, minute, tzinfo=ZoneInfo(tz_name)).astimezone(timezone.utc)


async def _remove_group_memberships(session: AsyncSession, user: User, request_id: str) -> int:
    """Removes the person from EVERY group they belong to (in the directory too), not just the ones AccessPilot
    granted. One group failing never blocks the rest."""
    provider = await session.get(IdentityProvider, user.provider_id)
    connector = _connector(provider) if provider is not None and provider.type != "CSV" else None
    person_id, external_id = user.id, user.external_id
    removed = 0
    for group_id in list((await session.scalars(select(UserGroup.group_id).where(UserGroup.user_id == person_id))).all()):
        group = await session.get(Group, group_id)
        if connector is not None and group is not None:
            try:
                await connector.remove_group_member(group.external_id, external_id)
            except GraphError:
                continue
        for row in (await session.scalars(select(UserGroup).where(UserGroup.user_id == person_id, UserGroup.group_id == group_id))).all():
            await session.delete(row)
        removed += 1
    await session.commit()
    return removed


async def run_leaver(session: AsyncSession, user_id: UUID, source: str, actor_subject: str, request_id: str, *, skip_revoke: bool = False, skip_privileged: bool = False, already_revoked: int = 0, skip_provider_ids: Optional[set] = None) -> Optional[LifecycleEvent]:
    """THE leaver process — the only path. Applies the person's leaver policy: revoke all AccessPilot access, disable
    linked PU/TU accounts, disable their account in every connected IdP (forced, because AccessPilot's own status can
    run ahead of the directory), optionally remove all group memberships; then records a LEAVER event and notifies
    the manager and lifecycle owners. Called by the scheduled leaver-date worker, the CSV TERMINATED path,
    sync-detected disables and the manual 'Start leaver process now' button, so all four behave identically."""
    from app.services.accounts import set_person_enabled
    from app.services.assignments import revoke_assignment
    from app.services.privileged_accounts import disable_linked_accounts_for_leaver

    user = await session.get(User, user_id)
    if user is None or user.account_type != "NORMAL":
        return None
    policy = await resolve_leaver_policy(session, user)
    person_id, name = user.id, user.display_name
    policy_name, policy_disable, policy_priv, policy_revoke, policy_groups = policy.name, policy.disable_accounts, policy.disable_privileged_accounts, policy.revoke_access, policy.remove_group_memberships
    policy_delete_days = policy.delete_after_days

    revoked = already_revoked
    if policy_revoke and not skip_revoke:
        for assignment_id in (await session.scalars(select(AccessAssignment.id).where(AccessAssignment.user_id == person_id, AccessAssignment.status.notin_(("REJECTED", "REVOKED", "EXPIRED"))))).all():
            try:
                await revoke_assignment(session, assignment_id, "system:lifecycle", f"Automated leaver revocation ({SOURCE_TEXT.get(source, source)}; policy: {policy_name}).", request_id, reason="LEAVER_" + source)
                revoked += 1
            except AccessPilotError:
                continue
    groups_removed = await _remove_group_memberships(session, user, request_id) if policy_groups else 0
    linked_disabled = await disable_linked_accounts_for_leaver(session, person_id, "system:lifecycle", request_id) if (policy_priv and not skip_privileged) else 0
    account_results: list[dict] = []
    if policy_disable:
        response = await set_person_enabled(session, person_id, False, "system:lifecycle", request_id, force=True, skip_provider_ids=skip_provider_ids)
        account_results = [{"provider": r.provider_name, "ok": r.ok, "error": r.error} for r in response.results]
    failed = [r for r in account_results if not r["ok"]]

    user = await session.get(User, person_id)
    user.leaver_processed_at = datetime.now(timezone.utc)
    user.accounts_delete_at = user.leaver_processed_at + timedelta(days=policy_delete_days) if policy_delete_days else None
    settings = await get_lifecycle_settings(session)
    event = LifecycleEvent(
        user_id=person_id, event_type="LEAVER", source=source, revoked_count=revoked, granted_count=0, review_note="ACCOUNT_DISABLE_FAILED" if failed else None,
        changes={"status": {"from": "ACTIVE", "to": "DISABLED"}, "policy": policy_name, "linked_accounts_disabled": linked_disabled, "groups_removed": groups_removed, "accounts": account_results, "delete_after_days": policy_delete_days},
    )
    session.add(event)
    await session.flush()
    done = [r["provider"] for r in account_results if r["ok"]]
    text = f"{name} left ({SOURCE_TEXT.get(source, source)}): {revoked} access removed"
    text += f"; accounts disabled in {', '.join(done)}" if done else ""
    text += f"; FAILED in {', '.join(r['provider'] for r in failed)}" if failed else ""
    text += f"; {linked_disabled} linked privileged/test account(s) disabled" if linked_disabled else ""
    text += f"; removed from {groups_removed} group(s)" if groups_removed else ""
    event.notified_user_ids = await _notify_lifecycle(session, user, settings, "LIFECYCLE_LEAVER", text + f". Policy: {policy_name}.")
    await record_audit(session, action="LIFECYCLE_LEAVER_RUN", target_type="USER", target_id=person_id, request_id=request_id, metadata={"source": source, "policy": policy_name, "revoked": revoked, "accounts": account_results, "linked_accounts_disabled": linked_disabled, "groups_removed": groups_removed})
    await session.commit()
    await session.refresh(event)
    return event


async def sweep_leavers(session: AsyncSession) -> int:
    """Worker entry point: runs the leaver process for everyone whose leaver date + policy time has arrived, and
    sends the policy's 'N days before' reminders. Idempotent: a person is processed once (leaver_processed_at)."""
    from zoneinfo import ZoneInfo

    from app.services.access_reviews import _app_timezone

    tz_name = await _app_timezone(session)
    now = datetime.now(timezone.utc)
    today = now.astimezone(ZoneInfo(tz_name)).date()
    candidate_ids = list((await session.scalars(select(User.id).where(User.leaver_date.is_not(None), User.leaver_processed_at.is_(None), User.account_type == "NORMAL"))).all())
    processed = 0
    for user_id in candidate_ids:
        user = await session.get(User, user_id)
        if user is None or user.leaver_date is None:
            continue
        policy = await resolve_leaver_policy(session, user)
        leaver_date, sent = user.leaver_date, list(user.leaver_reminders_sent or [])
        due = leaver_due_at(leaver_date, policy.effective_time, tz_name)
        if due <= now:
            try:
                await run_leaver(session, user_id, "SCHEDULED", "system:lifecycle", f"leaver-{user_id}")
                processed += 1
            except AccessPilotError as exc:
                logger.warning("Scheduled leaver run failed for %s: %s", user_id, exc)
                await session.rollback()
            continue
        days_left = (leaver_date - today).days
        if days_left in (policy.notify_days_before or []) and days_left not in sent:
            settings = await get_lifecycle_settings(session)
            when = "today" if days_left == 0 else f"in {days_left} day{'s' if days_left != 1 else ''}"
            await _notify_lifecycle(session, user, settings, "LIFECYCLE_LEAVER_REMINDER", f"{user.display_name} is due to leave {when} ({leaver_date.isoformat()}, policy: {policy.name}).")
            user.leaver_reminders_sent = sent + [days_left]
            await session.commit()
    return processed


async def list_scheduled_leavers(session: AsyncSession) -> list[ScheduledLeaverResponse]:
    from app.services.access_reviews import _app_timezone

    tz_name = await _app_timezone(session)
    now = datetime.now(timezone.utc)
    rows: list[ScheduledLeaverResponse] = []
    for user in (await session.scalars(select(User).where(User.leaver_date.is_not(None), User.leaver_processed_at.is_(None), User.account_type == "NORMAL").order_by(User.leaver_date))).all():
        policy = await resolve_leaver_policy(session, user)
        due = leaver_due_at(user.leaver_date, policy.effective_time, tz_name)
        rows.append(ScheduledLeaverResponse(user_id=user.id, user_display_name=user.display_name, user_email=user.email, department=user.department, employment_type=user.employment_type, leaver_date=user.leaver_date, policy_name=policy.name, due_at=due, status="DUE" if due <= now else "SCHEDULED"))
    return rows


async def update_person_lifecycle(session: AsyncSession, user_id: UUID, data: PersonLifecycleUpdate, actor_subject: str, request_id: str) -> PersonLifecycleResponse:
    """Sets / clears a person's leaver date and employment type (the latter can scope a leaver policy). Changing the
    leaver date re-arms the process: processed flag and sent reminders are reset."""
    from app.services.access_reviews import _app_timezone
    from app.services.assignments import _resolve_internal_user_id

    user = await session.get(User, user_id)
    if user is None:
        raise AccessPilotError("USER_NOT_FOUND", "The user was not found.", 404)
    if user.account_type != "NORMAL":
        raise AccessPilotError("VALIDATION_ERROR", "Privileged (PU) and test (TU) accounts have no leaver date; they follow their owner.", 422)
    if user.leaver_processed_at is not None and user.status == "DISABLED" and (data.clear_leaver_date or data.leaver_date is not None):
        raise AccessPilotError("LEAVER_ALREADY_PROCESSED", "The leaver process already ran for this person and their account is still disabled. To enable it again, request it (reason + manager approval).", 409)
    changes: dict = {}
    if data.clear_leaver_date and user.leaver_date is not None:
        user.leaver_date, user.leaver_processed_at, user.leaver_reminders_sent = None, None, []
        changes["leaver_date"] = None
    elif data.leaver_date is not None and data.leaver_date != user.leaver_date:
        user.leaver_date, user.leaver_processed_at, user.leaver_reminders_sent = data.leaver_date, None, []
        changes["leaver_date"] = data.leaver_date.isoformat()
    if data.clear_employment_type and user.employment_type is not None:
        user.employment_type = None
        changes["employment_type"] = None
    elif data.employment_type is not None and data.employment_type != user.employment_type:
        user.employment_type = data.employment_type
        changes["employment_type"] = data.employment_type
    if changes:
        await record_audit(session, action="LEAVER_DATE_UPDATED", target_type="USER", target_id=user.id, actor_user_id=await _resolve_internal_user_id(session, actor_subject), request_id=request_id, metadata=changes)
    await session.commit()
    user = await session.get(User, user_id)
    policy_name = due = None
    if user.leaver_date is not None:
        policy = await resolve_leaver_policy(session, user)
        policy_name, due = policy.name, leaver_due_at(user.leaver_date, policy.effective_time, await _app_timezone(session))
    return PersonLifecycleResponse(user_id=user.id, leaver_date=user.leaver_date, employment_type=user.employment_type, policy_name=policy_name, due_at=due)


async def leave_now(session: AsyncSession, user_id: UUID, actor_subject: str, request_id: str) -> LifecycleEventResponse:
    """The manual 'Start leaver process now' button."""
    from app.services.assignments import _resolve_internal_user_id

    user = await session.get(User, user_id)
    if user is None:
        raise AccessPilotError("USER_NOT_FOUND", "The user was not found.", 404)
    if user.account_type != "NORMAL":
        raise AccessPilotError("VALIDATION_ERROR", "Only regular accounts go through the leaver process; PU/TU accounts follow their owner.", 422)
    actor_id = await _resolve_internal_user_id(session, actor_subject)
    if actor_id is not None and actor_id == user.id:
        raise AccessPilotError("VALIDATION_ERROR", "You cannot start the leaver process for yourself.", 400)
    if user.leaver_processed_at is not None:
        raise AccessPilotError("REQUEST_ALREADY_PROCESSED", "The leaver process has already run for this person.", 409)
    event = await run_leaver(session, user_id, "MANUAL", actor_subject, request_id)
    return next(row for row in await list_events(session, "LEAVER", 50) if row.id == event.id)


# -- leaver policy CRUD

def _validate_policy_scope(scope_type: str, scope_value: Optional[str]) -> Optional[str]:
    if scope_type == "ALL":
        return None
    value = (scope_value or "").strip()
    if not value:
        raise AccessPilotError("VALIDATION_ERROR", "Choose the department or employment type this policy applies to.", 422)
    if scope_type == "EMPLOYMENT_TYPE":
        value = value.upper()
        if value not in EMPLOYMENT_TYPES:
            raise AccessPilotError("VALIDATION_ERROR", f"Employment type must be one of {', '.join(EMPLOYMENT_TYPES)}.", 422)
    return value


def _clean_days(days: list[int]) -> list[int]:
    return sorted({d for d in days if 0 <= d <= 90}, reverse=True)


async def list_leaver_policies(session: AsyncSession) -> list[LeaverPolicy]:
    await get_default_leaver_policy(session)
    return list((await session.scalars(select(LeaverPolicy).order_by(LeaverPolicy.is_default, LeaverPolicy.priority, LeaverPolicy.name))).all())


async def create_leaver_policy(session: AsyncSession, data: LeaverPolicyCreate, request_id: str) -> LeaverPolicy:
    if (await session.scalars(select(LeaverPolicy.id).where(LeaverPolicy.name == data.name))).first() is not None:
        raise AccessPilotError("POLICY_NAME_TAKEN", "A leaver policy with this name already exists.", 409)
    scope_value = _validate_policy_scope(data.scope_type, data.scope_value)
    policy = LeaverPolicy(name=data.name.strip(), priority=data.priority, scope_type=data.scope_type, scope_value=scope_value, effective_time=data.effective_time, notify_days_before=_clean_days(data.notify_days_before), revoke_access=data.revoke_access, disable_accounts=data.disable_accounts, disable_privileged_accounts=data.disable_privileged_accounts, remove_group_memberships=data.remove_group_memberships, delete_after_days=data.delete_after_days, status=data.status)
    session.add(policy)
    await session.flush()
    await record_audit(session, action="LEAVER_POLICY_CREATED", target_type="LEAVER_POLICY", target_id=policy.id, request_id=request_id, metadata={"name": policy.name, "scope": f"{policy.scope_type}:{policy.scope_value}"})
    await session.commit()
    await session.refresh(policy)
    return policy


async def update_leaver_policy(session: AsyncSession, policy_id: UUID, data: LeaverPolicyUpdate, request_id: str) -> LeaverPolicy:
    policy = await session.get(LeaverPolicy, policy_id)
    if policy is None:
        raise AccessPilotError("POLICY_NOT_FOUND", "The leaver policy was not found.", 404)
    fields = data.model_dump(exclude_unset=True)
    if policy.is_default and fields.get("scope_type", "ALL") != "ALL":
        raise AccessPilotError("VALIDATION_ERROR", "The default policy always applies to everyone.", 422)
    if policy.is_default and fields.get("status") == "DISABLED":
        raise AccessPilotError("VALIDATION_ERROR", "The default policy cannot be disabled.", 422)
    if "name" in fields and fields["name"] != policy.name and (await session.scalars(select(LeaverPolicy.id).where(LeaverPolicy.name == fields["name"], LeaverPolicy.id != policy.id))).first() is not None:
        raise AccessPilotError("POLICY_NAME_TAKEN", "A leaver policy with this name already exists.", 409)
    scope_type = fields.get("scope_type", policy.scope_type)
    if "scope_type" in fields or "scope_value" in fields:
        policy.scope_value = _validate_policy_scope(scope_type, fields.get("scope_value", policy.scope_value))
        policy.scope_type = scope_type
    for key in ("name", "priority", "effective_time", "revoke_access", "disable_accounts", "disable_privileged_accounts", "remove_group_memberships", "status"):
        if key in fields:
            setattr(policy, key, fields[key])
    if "notify_days_before" in fields and fields["notify_days_before"] is not None:
        policy.notify_days_before = _clean_days(fields["notify_days_before"])
    if fields.get("clear_delete_after_days"):
        policy.delete_after_days = None
    elif fields.get("delete_after_days") is not None:
        policy.delete_after_days = fields["delete_after_days"]
    await record_audit(session, action="LEAVER_POLICY_UPDATED", target_type="LEAVER_POLICY", target_id=policy.id, request_id=request_id, metadata={k: (v if not isinstance(v, list) else list(v)) for k, v in fields.items()})
    await session.commit()
    await session.refresh(policy)
    return policy


async def delete_leaver_policy(session: AsyncSession, policy_id: UUID, request_id: str) -> None:
    policy = await session.get(LeaverPolicy, policy_id)
    if policy is None:
        raise AccessPilotError("POLICY_NOT_FOUND", "The leaver policy was not found.", 404)
    if policy.is_default:
        raise AccessPilotError("VALIDATION_ERROR", "The default policy cannot be deleted.", 409)
    await record_audit(session, action="LEAVER_POLICY_DELETED", target_type="LEAVER_POLICY", target_id=policy.id, request_id=request_id, metadata={"name": policy.name})
    await session.delete(policy)
    await session.commit()
