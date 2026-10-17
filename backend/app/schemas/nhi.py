from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class NhiOwnerResponse(BaseModel):
    user_id: UUID
    display_name: str
    email: str


class NhiCredentialResponse(BaseModel):
    credential_type: str
    display_name: Optional[str] = None
    expires_at: Optional[datetime] = None


class NonHumanIdentityResponse(BaseModel):
    id: UUID
    provider_id: UUID
    provider_name: str
    provider_type: str
    external_id: str
    name: str
    status: str
    nhi_type: str
    nhi_type_overridden: bool
    credential_expires_at: Optional[datetime] = None
    credentials: list[NhiCredentialResponse] = []
    owners: list[NhiOwnerResponse]
    risk_flags: list[str]
    last_synced_at: Optional[datetime] = None


class NhiPermissionResponse(BaseModel):
    resource_display_name: str
    role_name: str


class NhiDirectoryActivityResponse(BaseModel):
    timestamp: datetime
    activity: str
    actor: Optional[str] = None
    result: str
    detail: Optional[str] = None


class NhiSummaryResponse(BaseModel):
    total: int
    no_owner: int
    credential_expiring_soon: int
    credential_expired: int
    by_type: dict[str, int]


class NhiOwnerAssignRequest(BaseModel):
    user_id: UUID


class NhiTypeUpdateRequest(BaseModel):
    nhi_type: str = Field(pattern="^(SERVICE_PRINCIPAL|MANAGED_IDENTITY|OKTA_SERVICE_APP|AI_AGENT|API|BOT|OTHER)$")


class NhiRiskExceptionCreateRequest(BaseModel):
    risk_type: str = Field(pattern="^(NO_OWNER|CREDENTIAL_EXPIRED|CREDENTIAL_EXPIRING_SOON)$")
    justification: str = Field(min_length=1, max_length=2000)
    expires_at: datetime


class NhiRiskExceptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    application_id: UUID
    risk_type: str
    justification: str
    approved_by: Optional[UUID] = None
    expires_at: datetime
    revoked_at: Optional[datetime] = None
    created_at: datetime


class NhiRegisterRequest(BaseModel):
    provider_id: UUID
    display_name: str = Field(min_length=1, max_length=255)
    nhi_type: str = Field(pattern="^(SERVICE_PRINCIPAL|MANAGED_IDENTITY|OKTA_SERVICE_APP|AI_AGENT|API|BOT|OTHER)$")
    # Mandatory — a brand-new NHI should never start life already flagged NO_OWNER (see app.services.nhi.register_new_identity).
    owner_user_id: UUID


class NhiNotificationSettingsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    notify_on_no_owner: bool
    notify_on_credential_expiring: bool
    credential_expiring_warning_days: int
    notify_on_credential_expired: bool


class NhiNotificationSettingsUpdateRequest(BaseModel):
    notify_on_no_owner: bool
    notify_on_credential_expiring: bool
    credential_expiring_warning_days: int = Field(gt=0, le=365)
    notify_on_credential_expired: bool


class NhiNotificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    notification_type: str
    application_id: UUID
    application_name: Optional[str] = None
    owner_user_id: Optional[UUID] = None
    owner_display_name: Optional[str] = None
    message: str
    read_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    created_at: datetime


class NhiReviewCampaignCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    # Exactly one of reviewer_id / workflow_definition_id — see _validate_reviewer_xor_workflow below. Mirrors
    # AccessReviewCampaignCreate's identical split exactly.
    reviewer_id: Optional[UUID] = None
    fallback_reviewer_id: Optional[UUID] = None
    fallback_unlock_hours: Optional[int] = Field(default=None, gt=0)
    workflow_definition_id: Optional[UUID] = None
    due_at: datetime
    # NULL/omitted means a one-time campaign. A positive value means "when this campaign completes, automatically
    # create the next one due this many days later" — same recurrence shape as AccessReviewCampaign.frequency_days.
    frequency_days: Optional[int] = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _validate_reviewer_xor_workflow(self) -> "NhiReviewCampaignCreate":
        if (self.reviewer_id is None) == (self.workflow_definition_id is None):
            raise ValueError("Provide exactly one of reviewer_id or workflow_definition_id")
        if self.workflow_definition_id is not None and (self.fallback_reviewer_id is not None or self.fallback_unlock_hours is not None):
            raise ValueError("workflow_definition_id cannot be combined with fallback_reviewer_id/fallback_unlock_hours — the workflow has its own per-stage fallback/escalation")
        return self


class NhiReviewCampaignResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    reviewer_id: Optional[UUID] = None
    reviewer_display_name: Optional[str] = None
    fallback_reviewer_id: Optional[UUID] = None
    fallback_reviewer_display_name: Optional[str] = None
    fallback_unlock_hours: Optional[int] = None
    workflow_definition_id: Optional[UUID] = None
    workflow_definition_name: Optional[str] = None
    status: str
    due_at: datetime
    frequency_days: Optional[int] = None
    parent_campaign_id: Optional[UUID] = None
    created_by: Optional[UUID] = None
    created_at: datetime
    completed_at: Optional[datetime] = None
    item_count: int = 0
    pending_count: int = 0


class NhiReviewItemDecide(BaseModel):
    decision: str = Field(pattern="^(CONFIRMED|OWNER_REMOVED)$")


class NhiReviewItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    campaign_id: UUID
    campaign_name: Optional[str] = None
    application_id: UUID
    application_name: Optional[str] = None
    owner_user_id: UUID
    owner_display_name: Optional[str] = None
    owner_email: Optional[str] = None
    decision: str
    decided_by: Optional[UUID] = None
    decided_at: Optional[datetime] = None
    workflow_instance_id: Optional[UUID] = None
    created_at: datetime
