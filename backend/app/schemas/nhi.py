from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


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
