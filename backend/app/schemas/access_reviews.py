from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCOPE_TYPES = ("ALL", "RESOURCE_TYPE", "SPECIFIC_RESOURCE", "MULTIPLE_RESOURCES", "USER", "ACCOUNT_TYPE", "INACTIVE_USERS", "MOVER")


class ScopeTargetItem(BaseModel):
    """One entry in a MULTIPLE_RESOURCES campaign's target list — e.g. {GROUP, <id>} and {PACKAGE, <id>} can sit
    side by side in the same campaign, mixing resource types freely."""
    resource_type: str = Field(pattern="^(GROUP|ROLE|APPLICATION|PACKAGE|BUSINESS_ROLE)$")
    resource_id: UUID


class AccessReviewCampaignCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: Optional[str] = None
    scope_type: str = Field(pattern="^(ALL|RESOURCE_TYPE|SPECIFIC_RESOURCE|MULTIPLE_RESOURCES|USER|ACCOUNT_TYPE|INACTIVE_USERS|MOVER)$")
    scope_resource_type: Optional[str] = Field(default=None, pattern="^(GROUP|ROLE|APPLICATION|PACKAGE|BUSINESS_ROLE)$")
    scope_resource_id: Optional[UUID] = None
    scope_targets: Optional[list[ScopeTargetItem]] = None
    scope_user_id: Optional[UUID] = None
    scope_account_type: Optional[str] = Field(default=None, pattern="^(PU|TU)$")
    scope_inactive_days: Optional[int] = Field(default=None, gt=0)
    reviewer_id: UUID
    fallback_reviewer_id: Optional[UUID] = None
    fallback_unlock_hours: Optional[int] = Field(default=None, gt=0)
    due_at: datetime
    on_no_response: str = Field(default="REVOKE", pattern=r"^(REVOKE|KEEP)$")
    # Recurrence: NULL/omitted means a one-time campaign, unchanged from before this field existed. A positive
    # value means "when this campaign completes, automatically create the next one due this many days later,
    # with the same scope/reviewer" — see app.services.access_reviews._maybe_spawn_recurrence.
    frequency_days: Optional[int] = Field(default=None, gt=0)
    # Fixed-calendar recurrence: a new campaign STARTS on this day of the month at this time (app timezone).
    schedule_day_of_month: Optional[int] = Field(default=None, ge=1, le=31)
    schedule_time: Optional[str] = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    schedule_every_months: Optional[int] = Field(default=None, ge=1, le=12)
    schedule_due_days: Optional[int] = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _validate_schedule(self) -> "AccessReviewCampaignCreate":
        has_schedule = self.schedule_day_of_month is not None or self.schedule_time is not None
        if has_schedule and (self.schedule_day_of_month is None or self.schedule_time is None):
            raise ValueError("schedule_day_of_month and schedule_time must be provided together")
        if has_schedule and self.frequency_days is not None:
            raise ValueError("Choose either frequency_days (repeat after completion) or a fixed schedule, not both")
        return self

    @model_validator(mode="after")
    def _validate_scope(self) -> "AccessReviewCampaignCreate":
        if self.scope_type == "RESOURCE_TYPE" and not self.scope_resource_type:
            raise ValueError("scope_resource_type is required when scope_type is RESOURCE_TYPE")
        if self.scope_type == "SPECIFIC_RESOURCE" and not (self.scope_resource_type and self.scope_resource_id):
            raise ValueError("scope_resource_type and scope_resource_id are required when scope_type is SPECIFIC_RESOURCE")
        if self.scope_type == "MULTIPLE_RESOURCES" and not self.scope_targets:
            raise ValueError("scope_targets must have at least one entry when scope_type is MULTIPLE_RESOURCES")
        if self.scope_type in ("USER", "MOVER") and not self.scope_user_id:
            raise ValueError("scope_user_id is required when scope_type is USER or MOVER")
        if self.scope_type == "ACCOUNT_TYPE" and not self.scope_account_type:
            raise ValueError("scope_account_type is required when scope_type is ACCOUNT_TYPE")
        if self.scope_type == "INACTIVE_USERS" and not self.scope_inactive_days:
            raise ValueError("scope_inactive_days is required when scope_type is INACTIVE_USERS")
        return self


class AccessReviewCampaignUpdate(BaseModel):
    """Deliberately does NOT include any scope_* field — a campaign's roster is a one-time snapshot taken at
    creation (see AccessReviewCampaign's own docstring); changing scope after the fact would leave already-
    snapshotted items silently mismatched with a scope they were never actually taken from. Editable fields are
    exactly the campaign's own metadata: who's reviewing it, its fallback arrangement, its deadline, and its
    name/description. Only reaches an ACTIVE campaign — a COMPLETED/CANCELLED one is a closed historical record."""
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = None
    reviewer_id: Optional[UUID] = None
    fallback_reviewer_id: Optional[UUID] = None
    clear_fallback_reviewer: bool = False
    fallback_unlock_hours: Optional[int] = Field(default=None, gt=0)
    due_at: Optional[datetime] = None
    frequency_days: Optional[int] = Field(default=None, gt=0)
    clear_frequency: bool = False
    schedule_day_of_month: Optional[int] = Field(default=None, ge=1, le=31)
    schedule_time: Optional[str] = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    schedule_every_months: Optional[int] = Field(default=None, ge=1, le=12)
    schedule_due_days: Optional[int] = Field(default=None, gt=0)
    clear_schedule: bool = False


class ScopeTargetResolved(BaseModel):
    resource_type: str
    resource_id: UUID
    resource_display_name: Optional[str] = None


class AccessReviewCampaignResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    description: Optional[str]
    scope_type: str
    scope_resource_type: Optional[str]
    scope_resource_id: Optional[UUID]
    scope_targets: Optional[list[ScopeTargetResolved]] = None
    scope_user_id: Optional[UUID]
    scope_account_type: Optional[str]
    scope_inactive_days: Optional[int] = None
    reviewer_id: UUID
    reviewer_display_name: Optional[str] = None
    fallback_reviewer_id: Optional[UUID]
    fallback_reviewer_display_name: Optional[str] = None
    fallback_unlock_hours: Optional[int]
    status: str
    due_at: datetime
    on_no_response: str = "REVOKE"
    frequency_days: Optional[int] = None
    schedule_day_of_month: Optional[int] = None
    schedule_time: Optional[str] = None
    schedule_every_months: Optional[int] = None
    schedule_due_days: Optional[int] = None
    next_run_at: Optional[datetime] = None
    parent_campaign_id: Optional[UUID] = None
    created_by: Optional[UUID]
    created_at: datetime
    completed_at: Optional[datetime]
    item_count: int = 0
    decided_count: int = 0
    approved_count: int = 0
    revoked_count: int = 0  # a reviewer revoked it
    auto_revoked_count: int = 0  # removed because the deadline passed / campaign closed undecided


class AccessReviewItemDecide(BaseModel):
    decision: str = Field(pattern="^(APPROVED|REVOKED)$")
    justification: str = Field(min_length=3, max_length=2000)


class AccessReviewItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    campaign_id: UUID
    campaign_name: Optional[str] = None
    assignment_id: UUID
    user_id: UUID
    user_display_name: Optional[str] = None
    user_email: Optional[str] = None
    granted_via: Optional[str] = None
    package_id: Optional[UUID] = None
    package_name: Optional[str] = None
    business_role_id: Optional[UUID] = None
    business_role_name: Optional[str] = None
    resource_type: str
    resource_id: UUID
    resource_display_name: Optional[str] = None
    app_role_external_id: Optional[str]
    assignment_status_at_snapshot: str
    decision: str
    decided_by: Optional[UUID]
    decided_by_display_name: Optional[str] = None
    decided_at: Optional[datetime]
    justification: Optional[str]
    created_at: datetime


class ResourceTally(BaseModel):
    name: str
    count: int


class AccessReviewDashboard(BaseModel):
    """Live-computed summary for the Access Review Dashboard panel — nothing here is stored, it's recomputed on
    every read the same way the main Dashboard's other live panels are (see app.services.access_reviews.
    get_dashboard_summary). Deliberately has no risk/"high risk users" field — this app has no sign-in-risk data
    source to back one honestly; see AccessReview.md."""
    total_campaigns: int
    active_campaigns: int
    completed_campaigns: int
    recurring_campaigns: int
    total_items: int
    pending_items: int
    approved_items: int
    revoked_items: int  # revoked by a reviewer
    auto_revoked_items: int = 0  # removed at the deadline / on close with no decision
    top_groups: list[ResourceTally]
    top_applications: list[ResourceTally]
