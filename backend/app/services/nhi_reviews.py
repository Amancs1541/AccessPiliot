from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AccessPilotError
from app.models import Application, ApplicationOwner, NhiReviewCampaign, NhiReviewItem, User
from app.schemas.nhi import NhiReviewCampaignCreate, NhiReviewItemDecide
from app.services.assignments import _resolve_internal_user_id
from app.services.audit import record_audit
from app.services.nhi import remove_owner
from app.services.notifications import create_notification

_REVIEW_ADMIN_ROLES = ("AccessPilot.Admin", "AccessPilot.NHIAdmin")


async def create_campaign(session: AsyncSession, data: NhiReviewCampaignCreate, actor_subject: str, request_id: str) -> NhiReviewCampaign:
    """Snapshots one NhiReviewItem per CURRENTLY-OWNED (application, owner) pair at creation time — deliberately a
    ONE-TIME SNAPSHOT, not live-recomputed, same reasoning as AccessReviewCampaign: a review certifies who owned
    what as of the moment it started. An application with multiple owners gets one item per owner (ownership to
    confirm is a per-owner decision, not a per-application one). Exactly one of reviewer_id/workflow_definition_id
    is set (enforced by NhiReviewCampaignCreate's own validator) — when workflow-routed, each item gets its own
    independent WorkflowInstance instead of a single reviewer deciding every item."""
    workflow_definition = None
    if data.workflow_definition_id is not None:
        from app.models import WorkflowDefinition
        workflow_definition = await session.get(WorkflowDefinition, data.workflow_definition_id)
        if workflow_definition is None:
            raise AccessPilotError("WORKFLOW_DEFINITION_NOT_FOUND", "The selected workflow was not found.", 404)
        if workflow_definition.status != "ACTIVE":
            raise AccessPilotError("WORKFLOW_DEFINITION_NOT_ACTIVE", "This workflow is not currently accepting requests.", 409)
    else:
        reviewer = await session.get(User, data.reviewer_id)
        if reviewer is None:
            raise AccessPilotError("USER_NOT_FOUND", "The selected reviewer was not found.", 404)
        if data.fallback_reviewer_id is not None and await session.get(User, data.fallback_reviewer_id) is None:
            raise AccessPilotError("USER_NOT_FOUND", "The selected fallback reviewer was not found.", 404)

    created_by = await _resolve_internal_user_id(session, actor_subject)
    if workflow_definition is not None and created_by is None:
        raise AccessPilotError("USER_NOT_FOUND", "Your account could not be resolved.", 404)
    campaign = NhiReviewCampaign(
        name=data.name, reviewer_id=data.reviewer_id, fallback_reviewer_id=data.fallback_reviewer_id,
        fallback_unlock_hours=data.fallback_unlock_hours, workflow_definition_id=data.workflow_definition_id,
        status="ACTIVE", due_at=data.due_at, frequency_days=data.frequency_days, created_by=created_by,
    )
    session.add(campaign)
    await session.flush()

    owners = list((await session.scalars(select(ApplicationOwner))).all())
    items = []
    for owner in owners:
        item = NhiReviewItem(campaign_id=campaign.id, application_id=owner.application_id, owner_user_id=owner.user_id, decision="PENDING")
        session.add(item)
        items.append(item)
    await session.flush()

    if workflow_definition is not None:
        # One INDEPENDENT WorkflowInstance per item (not one shared per campaign) — each reviewed ownership gets
        # its own stage-by-stage decision, exactly like each access-review item already does.
        from app.services.workflows import start_workflow_instance
        for item in items:
            application = await session.get(Application, item.application_id)
            owner_user = await session.get(User, item.owner_user_id)
            label = f"NHI ownership review: {application.name if application else 'Unknown identity'} owned by {owner_user.display_name if owner_user else 'Unknown user'}"
            instance = await start_workflow_instance(session, workflow_definition, created_by, {}, "NHI_REVIEW_ITEM", label, subject_id=item.id)
            item.workflow_instance_id = instance.id

    await record_audit(session, action="NHI_REVIEW_CAMPAIGN_CREATED", target_type="NHI_REVIEW_CAMPAIGN", target_id=campaign.id, actor_user_id=created_by, request_id=request_id, metadata={"name": campaign.name, "item_count": len(owners), "workflow_definition_id": str(workflow_definition.id) if workflow_definition else None})
    if workflow_definition is None:
        await create_notification(session, data.reviewer_id, "NHI_REVIEW_ASSIGNED", f"You've been assigned {len(owners)} non-human identity ownership{'s' if len(owners) != 1 else ''} to review in \"{campaign.name}\", due {campaign.due_at.strftime('%Y-%m-%d')}.", link="/admin/nhi/reviews")
        if data.fallback_reviewer_id is not None:
            await create_notification(session, data.fallback_reviewer_id, "NHI_REVIEW_ASSIGNED", f"You're the fallback reviewer for \"{campaign.name}\" ({len(owners)} item{'s' if len(owners) != 1 else ''}), due {campaign.due_at.strftime('%Y-%m-%d')}.", link="/admin/nhi/reviews")
    await session.commit()
    await session.refresh(campaign)
    return campaign


async def _get_campaign(session: AsyncSession, campaign_id: UUID) -> NhiReviewCampaign:
    campaign = await session.get(NhiReviewCampaign, campaign_id)
    if campaign is None:
        raise AccessPilotError("NHI_REVIEW_CAMPAIGN_NOT_FOUND", "The NHI ownership review campaign was not found.", 404)
    return campaign


async def list_campaigns(session: AsyncSession) -> list[NhiReviewCampaign]:
    return list((await session.scalars(select(NhiReviewCampaign).order_by(NhiReviewCampaign.created_at.desc()))).all())


async def campaign_progress(session: AsyncSession, campaign_id: UUID) -> tuple[int, int]:
    items = list((await session.scalars(select(NhiReviewItem).where(NhiReviewItem.campaign_id == campaign_id))).all())
    decided = sum(1 for item in items if item.decision != "PENDING")
    return len(items), decided


async def list_campaign_items(session: AsyncSession, campaign_id: UUID) -> list[NhiReviewItem]:
    await _get_campaign(session, campaign_id)  # 404s if missing
    return list((await session.scalars(select(NhiReviewItem).where(NhiReviewItem.campaign_id == campaign_id).order_by(NhiReviewItem.created_at))).all())


async def list_my_review_items(session: AsyncSession, actor_id: UUID) -> list[NhiReviewItem]:
    """Same shape as access_reviews.list_my_review_items — every PENDING item across every campaign where the
    caller is the reviewer or the fallback reviewer, newest campaign first."""
    from sqlalchemy import or_
    stmt = (
        select(NhiReviewItem)
        .join(NhiReviewCampaign, NhiReviewCampaign.id == NhiReviewItem.campaign_id)
        .where(NhiReviewItem.decision == "PENDING", or_(NhiReviewCampaign.reviewer_id == actor_id, NhiReviewCampaign.fallback_reviewer_id == actor_id))
        .order_by(NhiReviewItem.created_at)
    )
    return list((await session.scalars(stmt)).all())


async def _authorize_review_decision(session: AsyncSession, campaign: NhiReviewCampaign, actor_subject: str, actor_roles: tuple[str, ...]) -> Optional[UUID]:
    """Same fallback-after-unlock shape as access_reviews._authorize_review_decision — the campaign's own
    reviewer, an Admin/NHIAdmin, or the fallback reviewer once fallback_unlock_hours has elapsed."""
    actor_id = await _resolve_internal_user_id(session, actor_subject)
    if any(role in actor_roles for role in _REVIEW_ADMIN_ROLES):
        return actor_id
    if actor_id is not None and actor_id == campaign.reviewer_id:
        return actor_id
    if actor_id is not None and actor_id == campaign.fallback_reviewer_id:
        if campaign.fallback_unlock_hours is not None:
            created_at = campaign.created_at if campaign.created_at.tzinfo else campaign.created_at.replace(tzinfo=timezone.utc)
            unlock_at = created_at + timedelta(hours=campaign.fallback_unlock_hours)
            if datetime.now(timezone.utc) < unlock_at:
                raise AccessPilotError("FALLBACK_NOT_YET_AVAILABLE", f"The fallback reviewer may only act after {unlock_at.isoformat()}.", 403)
        return actor_id
    raise AccessPilotError("ACCESS_DENIED", "Only the designated reviewer, the fallback reviewer (once eligible), or an administrator can decide this item.", 403)


async def _maybe_spawn_recurrence(session: AsyncSession, campaign: NhiReviewCampaign, request_id: str) -> None:
    """Mirrors access_reviews._maybe_spawn_recurrence's simpler (frequency_days-only, no fixed-calendar variant)
    half: if the campaign was recurring, snapshots a fresh campaign with the identical reviewer/fallback, due
    frequency_days from now, linked back via parent_campaign_id."""
    if not campaign.frequency_days:
        return
    next_data = NhiReviewCampaignCreate(name=campaign.name, reviewer_id=campaign.reviewer_id, fallback_reviewer_id=campaign.fallback_reviewer_id, fallback_unlock_hours=campaign.fallback_unlock_hours, workflow_definition_id=campaign.workflow_definition_id, due_at=datetime.now(timezone.utc) + timedelta(days=campaign.frequency_days), frequency_days=campaign.frequency_days)
    next_campaign = await create_campaign(session, next_data, "system:nhi-review-recurrence", f"{request_id}-recurrence")
    next_campaign.parent_campaign_id = campaign.id
    await session.commit()


async def decide_item(session: AsyncSession, item_id: UUID, data: NhiReviewItemDecide, actor_subject: str, actor_roles: tuple[str, ...], request_id: str) -> NhiReviewItem:
    item = await session.get(NhiReviewItem, item_id)
    if item is None:
        raise AccessPilotError("NHI_REVIEW_ITEM_NOT_FOUND", "The NHI ownership review item was not found.", 404)
    if item.decision != "PENDING":
        raise AccessPilotError("REQUEST_ALREADY_PROCESSED", "This item has already been decided.", 409)
    if item.workflow_instance_id is not None:
        raise AccessPilotError("NHI_REVIEW_ITEM_ROUTED_THROUGH_WORKFLOW", "This item is routed through a workflow — decide it from Workflow Requests instead.", 409)
    campaign = await _get_campaign(session, item.campaign_id)
    actor_id = await _authorize_review_decision(session, campaign, actor_subject, actor_roles)

    if data.decision == "OWNER_REMOVED":
        # Reuses the existing, unmodified owner-removal path — its own audit entry, same as every other place
        # ownership is removed.
        await remove_owner(session, item.application_id, item.owner_user_id, actor_id, request_id)

    item.decision = data.decision
    item.decided_by = actor_id
    item.decided_at = datetime.now(timezone.utc)
    await record_audit(session, action="NHI_REVIEW_ITEM_DECIDED", target_type="NHI_REVIEW_ITEM", target_id=item.id, actor_user_id=actor_id, request_id=request_id, metadata={"decision": data.decision, "campaign_id": str(campaign.id)})
    await session.commit()
    await session.refresh(item)

    total, decided = await campaign_progress(session, campaign.id)
    if decided >= total and campaign.status == "ACTIVE":
        campaign.status = "COMPLETED"
        campaign.completed_at = datetime.now(timezone.utc)
        await session.commit()
        await _maybe_spawn_recurrence(session, campaign, request_id)
    return item


async def auto_resolve_items_for_removed_owner(session: AsyncSession, application_id: UUID, user_id: UUID, request_id: str) -> None:
    """Called from app.services.lifecycle.run_leaver right after it removes a Leaver's ownership of an NHI: any
    still-PENDING review item naming that exact (application, owner) pair would otherwise sit forever awaiting a
    decision from someone no longer an owner. Resolved as OWNER_REMOVED_LEAVER — distinct from a reviewer's own
    manual OWNER_REMOVED decision, same AUTO_* vs manual distinction AccessReviewItem's own AUTO_REVOKED makes.
    Best-effort: never raises, so one bad item never blocks the rest of the Leaver process."""
    items = list((await session.scalars(select(NhiReviewItem).where(NhiReviewItem.application_id == application_id, NhiReviewItem.owner_user_id == user_id, NhiReviewItem.decision == "PENDING"))).all())
    if not items:
        return
    now = datetime.now(timezone.utc)
    for item in items:
        item.decision = "OWNER_REMOVED_LEAVER"
        item.decided_at = now
        await record_audit(session, action="NHI_REVIEW_ITEM_AUTO_RESOLVED", target_type="NHI_REVIEW_ITEM", target_id=item.id, request_id=request_id, actor_user_id=None, metadata={"reason": "OWNER_LEFT"})
    await session.commit()
