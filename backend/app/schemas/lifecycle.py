from __future__ import annotations

from datetime import date, datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class LifecycleOwnerInfo(BaseModel):
    user_id: UUID
    display_name: Optional[str] = None
    email: Optional[str] = None


class LifecycleEventResponse(BaseModel):
    id: UUID
    event_type: str
    source: str
    created_at: datetime
    user_id: UUID
    user_display_name: Optional[str] = None
    user_email: Optional[str] = None
    changes: dict = {}
    revoked_count: int = 0
    granted_count: int = 0
    privileged_flagged_count: int = 0
    review_campaign_id: Optional[UUID] = None
    review_campaign_name: Optional[str] = None
    review_status: Optional[str] = None
    review_reviewer_name: Optional[str] = None
    review_item_count: int = 0
    review_decided_count: int = 0
    review_note: Optional[str] = None
    notified: list[str] = []


class MoveScheduleCreate(BaseModel):
    user_id: UUID
    department: Optional[str] = Field(default=None, max_length=255)
    job_title: Optional[str] = Field(default=None, max_length=255)
    effective_at: datetime


class PendingMoveResponse(BaseModel):
    id: UUID
    user_id: UUID
    user_display_name: Optional[str] = None
    user_email: Optional[str] = None
    current_department: Optional[str] = None
    current_job_title: Optional[str] = None
    new_department: Optional[str] = None
    new_job_title: Optional[str] = None
    effective_at: datetime
    source: str
    status: str
    failure_reason: Optional[str] = None
    created_at: datetime
    applied_at: Optional[datetime] = None


class LifecycleSettingsResponse(BaseModel):
    mover_review_enabled: bool
    revoke_on_directory_disable: bool = True
    review_due_days: int
    lifecycle_owners: list[LifecycleOwnerInfo] = []


class LifecycleSettingsUpdate(BaseModel):
    mover_review_enabled: Optional[bool] = None
    revoke_on_directory_disable: Optional[bool] = None
    review_due_days: Optional[int] = Field(default=None, ge=1, le=365)
    lifecycle_owner_ids: Optional[list[UUID]] = None


EMPLOYMENT_TYPES = ("EMPLOYEE", "CONTRACTOR", "INTERN", "OTHER")


class LeaverPolicyBase(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    priority: int = Field(default=100, ge=0, le=999)
    scope_type: str = Field(default="ALL", pattern="^(ALL|DEPARTMENT|EMPLOYMENT_TYPE)$")
    scope_value: Optional[str] = Field(default=None, max_length=200)
    effective_time: str = Field(default="23:59", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    notify_days_before: list[int] = Field(default_factory=lambda: [7, 1])
    revoke_access: bool = True
    disable_accounts: bool = True
    disable_privileged_accounts: bool = True
    remove_group_memberships: bool = False
    delete_after_days: Optional[int] = Field(default=None, ge=1, le=3650)  # None = never delete accounts
    status: str = Field(default="ACTIVE", pattern="^(ACTIVE|DISABLED)$")


class LeaverPolicyCreate(LeaverPolicyBase):
    pass


class LeaverPolicyUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    priority: Optional[int] = Field(default=None, ge=0, le=999)
    scope_type: Optional[str] = Field(default=None, pattern="^(ALL|DEPARTMENT|EMPLOYMENT_TYPE)$")
    scope_value: Optional[str] = Field(default=None, max_length=200)
    effective_time: Optional[str] = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    notify_days_before: Optional[list[int]] = None
    revoke_access: Optional[bool] = None
    disable_accounts: Optional[bool] = None
    disable_privileged_accounts: Optional[bool] = None
    remove_group_memberships: Optional[bool] = None
    delete_after_days: Optional[int] = Field(default=None, ge=1, le=3650)
    clear_delete_after_days: bool = False
    status: Optional[str] = Field(default=None, pattern="^(ACTIVE|DISABLED)$")


class LeaverPolicyResponse(LeaverPolicyBase):
    priority: int  # the seeded Default is 1000, above the 0-999 range admins may choose
    id: UUID
    is_default: bool
    created_at: datetime
    updated_at: datetime


class PersonLifecycleUpdate(BaseModel):
    leaver_date: Optional[date] = None
    clear_leaver_date: bool = False
    employment_type: Optional[str] = Field(default=None, pattern="^(EMPLOYEE|CONTRACTOR|INTERN|OTHER)$")
    clear_employment_type: bool = False


class ScheduledLeaverResponse(BaseModel):
    user_id: UUID
    user_display_name: Optional[str] = None
    user_email: Optional[str] = None
    department: Optional[str] = None
    employment_type: Optional[str] = None
    leaver_date: date
    policy_name: str
    due_at: datetime
    status: str  # SCHEDULED | DUE


class PersonLifecycleResponse(BaseModel):
    user_id: UUID
    leaver_date: Optional[date] = None
    employment_type: Optional[str] = None
    policy_name: Optional[str] = None
    due_at: Optional[datetime] = None


class ReenableRequestCreate(BaseModel):
    reason: str = Field(min_length=10, max_length=1000)
    # Omitted/empty = every disabled account ("Enable in all IdPs"); a list = only those accounts (a single
    # account's own "Enable" click) -- approval then enables exactly this scope, nothing more.
    account_ids: Optional[list[UUID]] = None


class ReenableDecision(BaseModel):
    approve: bool
    note: Optional[str] = Field(default=None, max_length=1000)


class ReenableRequestResponse(BaseModel):
    id: UUID
    user_id: UUID
    user_display_name: Optional[str] = None
    user_email: Optional[str] = None
    requested_by: Optional[UUID] = None
    requested_by_name: Optional[str] = None
    reason: str
    status: str
    approvers: list[str] = []
    decided_by_name: Optional[str] = None
    decision_note: Optional[str] = None
    decided_at: Optional[datetime] = None
    created_at: datetime
    can_decide: bool = False
    scope_label: str = "All accounts"
    accounts_note: Optional[str] = None  # set on approval: what happened to each directory account


class LeaverStartRequest(BaseModel):
    justification: str = Field(min_length=10, max_length=1000)


class LeaverRequestResponse(BaseModel):
    id: UUID
    user_id: UUID
    user_display_name: Optional[str] = None
    user_email: Optional[str] = None
    requested_by: Optional[UUID] = None
    requested_by_name: Optional[str] = None
    justification: str
    status: str
    approvers: list[str] = []
    decided_by_name: Optional[str] = None
    decision_note: Optional[str] = None
    decided_at: Optional[datetime] = None
    created_at: datetime
    can_decide: bool = False
    outcome: Optional[str] = None
    accounts_note: Optional[str] = None  # what happened to each directory account when the request was started


class LeaverOverviewResponse(BaseModel):
    status: str
    leaver_processed_at: Optional[datetime] = None
    leaver_date: Optional[date] = None
    policy_name: Optional[str] = None
    pending_leaver_request: Optional[LeaverRequestResponse] = None
    pending_reenable_request: Optional[ReenableRequestResponse] = None
    recent_events: list[LifecycleEventResponse] = []
