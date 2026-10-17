from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.audit import AuditLogResponse
from app.schemas.nhi import NhiDirectoryActivityResponse, NhiNotificationResponse, NhiNotificationSettingsResponse, NhiNotificationSettingsUpdateRequest, NhiOwnerAssignRequest, NhiPermissionResponse, NhiRegisterRequest, NhiReviewCampaignCreate, NhiReviewCampaignResponse, NhiReviewItemDecide, NhiReviewItemResponse, NhiRiskExceptionCreateRequest, NhiRiskExceptionResponse, NhiSummaryResponse, NhiTypeUpdateRequest, NonHumanIdentityResponse
from app.security.auth import AuthenticatedUser, require_authenticated_user, require_permission
from app.services import nhi as nhi_service
from app.services import nhi_notifications as nhi_notifications_service
from app.services import nhi_reviews as nhi_reviews_service
from app.services.assignments import _resolve_internal_user_id

router = APIRouter(prefix="/nhi", tags=["nhi"])
nhi_read = require_permission("NHI_READ")
nhi_manage = require_permission("NHI_MANAGE")


async def _hydrate_review_campaign(db: AsyncSession, campaign) -> NhiReviewCampaignResponse:
    from app.models import User, WorkflowDefinition
    reviewer = await db.get(User, campaign.reviewer_id) if campaign.reviewer_id else None
    fallback = await db.get(User, campaign.fallback_reviewer_id) if campaign.fallback_reviewer_id else None
    workflow_definition = await db.get(WorkflowDefinition, campaign.workflow_definition_id) if campaign.workflow_definition_id else None
    total, decided = await nhi_reviews_service.campaign_progress(db, campaign.id)
    return NhiReviewCampaignResponse(
        id=campaign.id, name=campaign.name, reviewer_id=campaign.reviewer_id, reviewer_display_name=reviewer.display_name if reviewer else None,
        fallback_reviewer_id=campaign.fallback_reviewer_id, fallback_reviewer_display_name=fallback.display_name if fallback else None,
        fallback_unlock_hours=campaign.fallback_unlock_hours, workflow_definition_id=campaign.workflow_definition_id, workflow_definition_name=workflow_definition.name if workflow_definition else None,
        status=campaign.status, due_at=campaign.due_at, frequency_days=campaign.frequency_days,
        parent_campaign_id=campaign.parent_campaign_id, created_by=campaign.created_by, created_at=campaign.created_at, completed_at=campaign.completed_at,
        item_count=total, pending_count=total - decided,
    )


async def _hydrate_review_item(db: AsyncSession, item) -> NhiReviewItemResponse:
    from app.models import Application, NhiReviewCampaign, User
    campaign = await db.get(NhiReviewCampaign, item.campaign_id)
    application = await db.get(Application, item.application_id)
    owner = await db.get(User, item.owner_user_id)
    return NhiReviewItemResponse(
        id=item.id, campaign_id=item.campaign_id, campaign_name=campaign.name if campaign else None,
        application_id=item.application_id, application_name=application.name if application else None,
        owner_user_id=item.owner_user_id, owner_display_name=owner.display_name if owner else None, owner_email=owner.email if owner else None,
        decision=item.decision, decided_by=item.decided_by, decided_at=item.decided_at, workflow_instance_id=item.workflow_instance_id, created_at=item.created_at,
    )


@router.get("", response_model=list[NonHumanIdentityResponse])
async def list_nhi(_: AuthenticatedUser = Depends(nhi_read), db: AsyncSession = Depends(get_db)):
    return await nhi_service.list_non_human_identities(db)


@router.get("/summary", response_model=NhiSummaryResponse)
async def get_summary(_: AuthenticatedUser = Depends(nhi_read), db: AsyncSession = Depends(get_db)):
    return await nhi_service.get_summary(db)


# NOTE: every route below with a literal (non-{application_id}) path segment must stay registered BEFORE
# GET "/{application_id}" further down — FastAPI/Starlette matches path templates in registration order, so a
# literal path like "/mine" registered AFTER "/{application_id}" would be shadowed (matched as application_id
# == "mine", then fail UUID validation) instead of ever reaching its own handler.
@router.post("", response_model=NonHumanIdentityResponse, status_code=201)
async def register_identity(data: NhiRegisterRequest, request: Request, actor: AuthenticatedUser = Depends(nhi_manage), db: AsyncSession = Depends(get_db)):
    actor_id = await _resolve_internal_user_id(db, actor.directory_object_id)
    application = await nhi_service.register_new_identity(db, data.provider_id, data.display_name, data.nhi_type, data.owner_user_id, actor_id, request.state.request_id)
    return await nhi_service.get_non_human_identity(db, application.id)


@router.get("/mine", response_model=list[NonHumanIdentityResponse])
async def list_my_identities(actor: AuthenticatedUser = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    """The caller's own owned identities — self-service, gated only on being authenticated, same convention as
    GET /assignments/mine. No NHI_READ/NHIAdmin required."""
    return await nhi_service.list_my_non_human_identities(db, actor.directory_object_id)


@router.get("/notifications", response_model=list[NhiNotificationResponse])
async def list_notifications(_: AuthenticatedUser = Depends(nhi_read), db: AsyncSession = Depends(get_db)):
    """Reconciles against current reality on every call (see reconcile_nhi_notifications) — the one place that
    happens, same deliberate placement as SoD's own GET /sod/notifications."""
    return await nhi_notifications_service.list_nhi_notifications(db)


@router.post("/notifications/{notification_id}/read", status_code=204)
async def mark_notification_read(notification_id: UUID, _: AuthenticatedUser = Depends(nhi_read), db: AsyncSession = Depends(get_db)):
    await nhi_notifications_service.mark_nhi_notification_read(db, notification_id)
    return None


@router.post("/notifications/read-all", status_code=204)
async def mark_all_notifications_read(_: AuthenticatedUser = Depends(nhi_read), db: AsyncSession = Depends(get_db)):
    await nhi_notifications_service.mark_all_nhi_notifications_read(db)
    return None


@router.get("/notification-settings", response_model=NhiNotificationSettingsResponse)
async def get_notification_settings(_: AuthenticatedUser = Depends(nhi_read), db: AsyncSession = Depends(get_db)):
    return await nhi_notifications_service.get_nhi_notification_settings(db)


@router.patch("/notification-settings", response_model=NhiNotificationSettingsResponse)
async def update_notification_settings(data: NhiNotificationSettingsUpdateRequest, _: AuthenticatedUser = Depends(nhi_manage), db: AsyncSession = Depends(get_db)):
    return await nhi_notifications_service.update_nhi_notification_settings(db, data)


@router.post("/reviews", response_model=NhiReviewCampaignResponse, status_code=201)
async def create_review_campaign(data: NhiReviewCampaignCreate, request: Request, actor: AuthenticatedUser = Depends(nhi_manage), db: AsyncSession = Depends(get_db)):
    campaign = await nhi_reviews_service.create_campaign(db, data, actor.directory_object_id, request.state.request_id)
    return await _hydrate_review_campaign(db, campaign)


@router.get("/reviews", response_model=list[NhiReviewCampaignResponse])
async def list_review_campaigns(_: AuthenticatedUser = Depends(nhi_manage), db: AsyncSession = Depends(get_db)):
    campaigns = await nhi_reviews_service.list_campaigns(db)
    return [await _hydrate_review_campaign(db, campaign) for campaign in campaigns]


@router.get("/reviews/items/mine", response_model=list[NhiReviewItemResponse])
async def list_my_review_items(actor: AuthenticatedUser = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    """Pending review items across every campaign where the caller is the reviewer or fallback reviewer — self-
    service, same convention as GET /access-reviews/items/mine."""
    actor_id = await _resolve_internal_user_id(db, actor.directory_object_id)
    if actor_id is None:
        return []
    items = await nhi_reviews_service.list_my_review_items(db, actor_id)
    return [await _hydrate_review_item(db, item) for item in items]


@router.get("/{application_id}", response_model=NonHumanIdentityResponse)
async def get_nhi(application_id: UUID, _: AuthenticatedUser = Depends(nhi_read), db: AsyncSession = Depends(get_db)):
    return await nhi_service.get_non_human_identity(db, application_id)


@router.get("/{application_id}/risk-exceptions", response_model=list[NhiRiskExceptionResponse])
async def get_risk_exceptions(application_id: UUID, _: AuthenticatedUser = Depends(nhi_read), db: AsyncSession = Depends(get_db)):
    return await nhi_service.list_risk_exceptions(db, application_id)


@router.patch("/{application_id}/type", response_model=NonHumanIdentityResponse)
async def update_type(application_id: UUID, data: NhiTypeUpdateRequest, request: Request, actor: AuthenticatedUser = Depends(nhi_manage), db: AsyncSession = Depends(get_db)):
    actor_id = await _resolve_internal_user_id(db, actor.directory_object_id)
    await nhi_service.set_nhi_type(db, application_id, data.nhi_type, actor_id, request.state.request_id)
    return await nhi_service.get_non_human_identity(db, application_id)


@router.get("/{application_id}/activity", response_model=list[AuditLogResponse])
async def get_activity(application_id: UUID, _: AuthenticatedUser = Depends(nhi_read), db: AsyncSession = Depends(get_db)):
    entries = await nhi_service.get_activity(db, application_id)
    return [
        AuditLogResponse(
            id=entry.id, timestamp=entry.timestamp, actor_user_id=entry.actor_user_id, actor_display_name=hydrated["actor_display_name"],
            action=entry.action, target_type=entry.target_type, target_id=entry.target_id,
            target_user_display_name=hydrated["target_user_display_name"], target_user_email=hydrated["target_user_email"],
            provider_id=entry.provider_id, provider_name=hydrated["provider_name"], request_id=entry.request_id, result=entry.result, metadata=entry.metadata_json,
        )
        for entry, hydrated in entries
    ]


@router.get("/{application_id}/permissions", response_model=list[NhiPermissionResponse])
async def get_permissions(application_id: UUID, _: AuthenticatedUser = Depends(nhi_read), db: AsyncSession = Depends(get_db)):
    return await nhi_service.get_permissions(db, application_id)


@router.get("/{application_id}/directory-activity", response_model=list[NhiDirectoryActivityResponse])
async def get_directory_activity(application_id: UUID, _: AuthenticatedUser = Depends(nhi_read), db: AsyncSession = Depends(get_db)):
    return await nhi_service.get_directory_activity(db, application_id)


@router.post("/{application_id}/disable", response_model=NonHumanIdentityResponse)
async def disable_identity(application_id: UUID, request: Request, actor: AuthenticatedUser = Depends(nhi_manage), db: AsyncSession = Depends(get_db)):
    actor_id = await _resolve_internal_user_id(db, actor.directory_object_id)
    await nhi_service.disable_identity(db, application_id, actor_id, request.state.request_id)
    return await nhi_service.get_non_human_identity(db, application_id)


@router.post("/{application_id}/enable", response_model=NonHumanIdentityResponse)
async def enable_identity(application_id: UUID, request: Request, actor: AuthenticatedUser = Depends(nhi_manage), db: AsyncSession = Depends(get_db)):
    actor_id = await _resolve_internal_user_id(db, actor.directory_object_id)
    await nhi_service.enable_identity(db, application_id, actor_id, request.state.request_id)
    return await nhi_service.get_non_human_identity(db, application_id)


@router.post("/{application_id}/refresh", response_model=NonHumanIdentityResponse)
async def refresh_identity(application_id: UUID, request: Request, actor: AuthenticatedUser = Depends(nhi_manage), db: AsyncSession = Depends(get_db)):
    actor_id = await _resolve_internal_user_id(db, actor.directory_object_id)
    return await nhi_service.refresh_application_from_provider(db, application_id, actor_id, request.state.request_id)


@router.post("/{application_id}/owners", response_model=NonHumanIdentityResponse, status_code=201)
async def add_owner(application_id: UUID, data: NhiOwnerAssignRequest, request: Request, actor: AuthenticatedUser = Depends(nhi_manage), db: AsyncSession = Depends(get_db)):
    actor_id = await _resolve_internal_user_id(db, actor.directory_object_id)
    await nhi_service.assign_owner(db, application_id, data.user_id, actor_id, request.state.request_id)
    return await nhi_service.get_non_human_identity(db, application_id)


@router.delete("/{application_id}/owners/{user_id}", response_model=NonHumanIdentityResponse)
async def remove_owner(application_id: UUID, user_id: UUID, request: Request, actor: AuthenticatedUser = Depends(nhi_manage), db: AsyncSession = Depends(get_db)):
    actor_id = await _resolve_internal_user_id(db, actor.directory_object_id)
    await nhi_service.remove_owner(db, application_id, user_id, actor_id, request.state.request_id)
    return await nhi_service.get_non_human_identity(db, application_id)


@router.post("/{application_id}/risk-exceptions", response_model=NhiRiskExceptionResponse, status_code=201)
async def create_risk_exception(application_id: UUID, data: NhiRiskExceptionCreateRequest, request: Request, actor: AuthenticatedUser = Depends(nhi_manage), db: AsyncSession = Depends(get_db)):
    actor_id = await _resolve_internal_user_id(db, actor.directory_object_id)
    return await nhi_service.create_risk_exception(db, application_id, data.risk_type, data.justification, data.expires_at, actor_id, request.state.request_id)


@router.post("/{application_id}/risk-exceptions/{exception_id}/revoke", response_model=NhiRiskExceptionResponse)
async def revoke_risk_exception(application_id: UUID, exception_id: UUID, request: Request, actor: AuthenticatedUser = Depends(nhi_manage), db: AsyncSession = Depends(get_db)):
    actor_id = await _resolve_internal_user_id(db, actor.directory_object_id)
    return await nhi_service.revoke_risk_exception(db, application_id, exception_id, actor_id, request.state.request_id)


@router.get("/reviews/{campaign_id}/items", response_model=list[NhiReviewItemResponse])
async def list_review_campaign_items(campaign_id: UUID, _: AuthenticatedUser = Depends(nhi_manage), db: AsyncSession = Depends(get_db)):
    items = await nhi_reviews_service.list_campaign_items(db, campaign_id)
    return [await _hydrate_review_item(db, item) for item in items]


@router.post("/reviews/items/{item_id}/decide", response_model=NhiReviewItemResponse)
async def decide_review_item(item_id: UUID, data: NhiReviewItemDecide, request: Request, actor: AuthenticatedUser = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    item = await nhi_reviews_service.decide_item(db, item_id, data, actor.directory_object_id, actor.roles, request.state.request_id)
    return await _hydrate_review_item(db, item)
