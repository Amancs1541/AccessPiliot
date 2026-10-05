from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import request_id as get_request_id
from app.db.session import get_db
from app.models import User
from app.schemas.access_reviews import AccessReviewCampaignCreate, AccessReviewCampaignResponse, AccessReviewCampaignUpdate, AccessReviewDashboard, AccessReviewItemDecide, AccessReviewItemResponse, ScopeTargetResolved
from app.security.auth import AuthenticatedUser, require_authenticated_user, require_permission
from app.services import access_reviews as service
from app.services.assignments import _resolve_internal_user_id

router = APIRouter(prefix="/access-reviews", tags=["access-reviews"])
review_read = require_permission("ACCESS_REVIEW_READ")
review_manage = require_permission("ACCESS_REVIEW_MANAGE")


async def _hydrate_campaign(db: AsyncSession, campaign) -> AccessReviewCampaignResponse:
    reviewer = await db.get(User, campaign.reviewer_id)
    fallback = await db.get(User, campaign.fallback_reviewer_id) if campaign.fallback_reviewer_id else None
    total, decided = await service.campaign_progress(db, campaign.id)
    scope_targets = None
    if campaign.scope_targets:
        scope_targets = [
            ScopeTargetResolved(resource_type=t["resource_type"], resource_id=UUID(t["resource_id"]), resource_display_name=await service._resolve_display_name(db, t["resource_type"], UUID(t["resource_id"]), None))
            for t in campaign.scope_targets
        ]
    outcomes = await service.campaign_outcomes(db, campaign.id)
    return AccessReviewCampaignResponse(
        id=campaign.id, name=campaign.name, description=campaign.description, scope_type=campaign.scope_type,
        scope_resource_type=campaign.scope_resource_type, scope_resource_id=campaign.scope_resource_id, scope_targets=scope_targets,
        scope_user_id=campaign.scope_user_id, scope_account_type=campaign.scope_account_type, scope_inactive_days=campaign.scope_inactive_days,
        reviewer_id=campaign.reviewer_id, reviewer_display_name=reviewer.display_name if reviewer else None,
        fallback_reviewer_id=campaign.fallback_reviewer_id, fallback_reviewer_display_name=fallback.display_name if fallback else None,
        fallback_unlock_hours=campaign.fallback_unlock_hours, status=campaign.status, due_at=campaign.due_at,
        frequency_days=campaign.frequency_days, on_no_response=campaign.on_no_response, schedule_day_of_month=campaign.schedule_day_of_month, schedule_time=campaign.schedule_time, schedule_every_months=campaign.schedule_every_months, schedule_due_days=campaign.schedule_due_days, next_run_at=campaign.next_run_at, parent_campaign_id=campaign.parent_campaign_id,
        created_by=campaign.created_by, created_at=campaign.created_at, completed_at=campaign.completed_at,
        item_count=total, decided_count=decided, approved_count=outcomes["approved"], revoked_count=outcomes["revoked"], auto_revoked_count=outcomes["auto_revoked"],
    )


async def _package_of(db: AsyncSession, assignment_id):
    from sqlalchemy import select
    from app.models import AccessPackage, AccessPackageAssignment
    row = (await db.execute(select(AccessPackage.id, AccessPackage.name).join(AccessPackageAssignment, AccessPackageAssignment.package_id == AccessPackage.id).where(AccessPackageAssignment.assignment_id == assignment_id))).first()
    return (row[0], row[1]) if row else (None, None)


async def _business_role_of(db: AsyncSession, assignment_id):
    """Unlike PACKAGE (which needs the AccessPackageAssignment join table), a Business Role's provenance is a
    direct column on AccessAssignment — no join needed."""
    from app.models import AccessAssignment, BusinessRole
    assignment = await db.get(AccessAssignment, assignment_id)
    if not assignment or not assignment.business_role_id:
        return (None, None)
    role = await db.get(BusinessRole, assignment.business_role_id)
    return (assignment.business_role_id, role.name if role else None)


async def _granted_via(db: AsyncSession, assignment_id) -> str:
    """Where this entitlement actually came from: an access package, a business role, a birthright policy, a
    group-role mapping, or (none of those) a direct/manual grant."""
    from sqlalchemy import select
    from app.models import AccessAssignment, AccessPackage, AccessPackageAssignment, BirthrightPolicy, BusinessRole, GroupRoleMapping
    package_name = (await db.execute(select(AccessPackage.name).join(AccessPackageAssignment, AccessPackageAssignment.package_id == AccessPackage.id).where(AccessPackageAssignment.assignment_id == assignment_id))).scalar_one_or_none()
    assignment = await db.get(AccessAssignment, assignment_id)
    if package_name:
        if assignment and assignment.birthright_policy_id:
            policy = await db.get(BirthrightPolicy, assignment.birthright_policy_id)
            return f"Package: {package_name} (via birthright{': ' + policy.name if policy else ''})"
        return f"Package: {package_name}"
    if assignment and assignment.business_role_id:
        role = await db.get(BusinessRole, assignment.business_role_id)
        role_name = role.name if role else "Business Role"
        if assignment.birthright_policy_id:
            policy = await db.get(BirthrightPolicy, assignment.birthright_policy_id)
            return f"Business Role: {role_name} (via birthright{': ' + policy.name if policy else ''})"
        return f"Business Role: {role_name}"
    if assignment and assignment.birthright_policy_id:
        policy = await db.get(BirthrightPolicy, assignment.birthright_policy_id)
        return f"Birthright: {policy.name}" if policy else "Birthright policy"
    if assignment and assignment.group_role_mapping_id:
        return "Group role mapping"
    return "Direct assignment"


async def _hydrate_item(db: AsyncSession, item) -> AccessReviewItemResponse:
    from app.models import AccessReviewCampaign
    campaign = await db.get(AccessReviewCampaign, item.campaign_id)
    user = await db.get(User, item.user_id)
    decider = await db.get(User, item.decided_by) if item.decided_by else None
    resource_name = await service._resolve_display_name(db, item.resource_type, item.resource_id, item.app_role_external_id)
    pkg_id, pkg_name = await _package_of(db, item.assignment_id)
    role_id, role_name = await _business_role_of(db, item.assignment_id)
    return AccessReviewItemResponse(
        id=item.id, campaign_id=item.campaign_id, campaign_name=campaign.name if campaign else None,
        assignment_id=item.assignment_id, user_id=item.user_id, user_display_name=user.display_name if user else None, user_email=user.email if user else None, granted_via=await _granted_via(db, item.assignment_id), package_id=pkg_id, package_name=pkg_name,
        business_role_id=role_id, business_role_name=role_name,
        resource_type=item.resource_type, resource_id=item.resource_id, resource_display_name=resource_name,
        app_role_external_id=item.app_role_external_id, assignment_status_at_snapshot=item.assignment_status_at_snapshot,
        decision=item.decision, decided_by=item.decided_by, decided_by_display_name=decider.display_name if decider else None,
        decided_at=item.decided_at, justification=item.justification, created_at=item.created_at,
    )


@router.post("", response_model=AccessReviewCampaignResponse, status_code=201)
async def create_campaign(data: AccessReviewCampaignCreate, request: Request, actor: AuthenticatedUser = Depends(review_manage), db: AsyncSession = Depends(get_db)):
    campaign = await service.create_campaign(db, data, actor.directory_object_id, get_request_id(request))
    return await _hydrate_campaign(db, campaign)


@router.get("", response_model=list[AccessReviewCampaignResponse])
async def list_campaigns(_: AuthenticatedUser = Depends(review_read), db: AsyncSession = Depends(get_db)):
    return [await _hydrate_campaign(db, campaign) for campaign in await service.list_campaigns(db)]


@router.get("/items/mine", response_model=list[AccessReviewItemResponse])
async def list_my_review_items(actor: AuthenticatedUser = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    actor_id = await _resolve_internal_user_id(db, actor.directory_object_id)
    if actor_id is None:
        return []
    return [await _hydrate_item(db, item) for item in await service.list_my_review_items(db, actor_id)]


@router.get("/owner-suggestions")
async def owner_suggestions(resource_type: str, resource_id: UUID, _: AuthenticatedUser = Depends(review_manage), db: AsyncSession = Depends(get_db)):
    """Owners of a Package / Application / Group, offered as reviewer suggestions in the campaign form. Registered
    before /{campaign_id} (literal segment before a variable one)."""
    return await service.suggest_owner_reviewers(db, resource_type.upper(), resource_id)


@router.get("/dashboard", response_model=AccessReviewDashboard)
async def get_dashboard(_: AuthenticatedUser = Depends(review_read), db: AsyncSession = Depends(get_db)):
    """Registered before /{campaign_id} deliberately — a literal path segment must come before a same-shape
    variable one, or FastAPI tries (and fails) to parse "dashboard" as a campaign UUID first."""
    return await service.get_dashboard_summary(db)


@router.get("/{campaign_id}", response_model=AccessReviewCampaignResponse)
async def get_campaign(campaign_id: UUID, _: AuthenticatedUser = Depends(review_read), db: AsyncSession = Depends(get_db)):
    campaign = await service._get_campaign(db, campaign_id)
    return await _hydrate_campaign(db, campaign)


@router.patch("/{campaign_id}", response_model=AccessReviewCampaignResponse)
async def update_campaign(campaign_id: UUID, data: AccessReviewCampaignUpdate, request: Request, actor: AuthenticatedUser = Depends(review_manage), db: AsyncSession = Depends(get_db)):
    """Edits a campaign's own metadata (name/description/reviewer/fallback/due date) — never its scope or
    already-snapshotted items (see AccessReviewCampaignUpdate). Only an ACTIVE campaign can be edited."""
    campaign = await service.update_campaign(db, campaign_id, data, actor.directory_object_id, get_request_id(request))
    return await _hydrate_campaign(db, campaign)


@router.get("/{campaign_id}/items", response_model=list[AccessReviewItemResponse])
async def get_campaign_items(campaign_id: UUID, _: AuthenticatedUser = Depends(review_read), db: AsyncSession = Depends(get_db)):
    return [await _hydrate_item(db, item) for item in await service.list_campaign_items(db, campaign_id)]


@router.post("/{campaign_id}/complete", response_model=AccessReviewCampaignResponse)
async def complete_campaign(campaign_id: UUID, request: Request, actor: AuthenticatedUser = Depends(review_manage), db: AsyncSession = Depends(get_db)):
    campaign = await service.complete_campaign(db, campaign_id, actor.directory_object_id, get_request_id(request))
    return await _hydrate_campaign(db, campaign)


@router.post("/items/{item_id}/decide", response_model=AccessReviewItemResponse)
async def decide_item(item_id: UUID, data: AccessReviewItemDecide, request: Request, actor: AuthenticatedUser = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    item = await service.decide_item(db, item_id, data, actor.directory_object_id, actor.roles, get_request_id(request))
    return await _hydrate_item(db, item)
