from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PrivilegedAccountPolicyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    account_type: str
    default_approver_id: Optional[UUID]
    default_approver_display_name: Optional[str] = None
    approval_required: bool


class PrivilegedAccountPolicyUpdate(BaseModel):
    default_approver_id: Optional[UUID] = None


class PrivilegedAccountRequestCreate(BaseModel):
    account_type: str = Field(pattern="^(PU|TU)$")
    justification: str = Field(min_length=3, max_length=2000)


class PrivilegedAccountRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    requester_id: UUID
    requester_display_name: Optional[str] = None
    account_type: str
    status: str
    approver_id: Optional[UUID]
    approver_display_name: Optional[str] = None
    justification: Optional[str]
    provisioned_user_id: Optional[UUID]
    provisioned_user_display_name: Optional[str] = None
    failure_reason: Optional[str]
    created_at: datetime
    decided_at: Optional[datetime]


class PrivilegedAccountDecision(BaseModel):
    justification: str = Field(min_length=3, max_length=2000)


class LinkedAccountResponse(BaseModel):
    id: UUID
    display_name: str
    email: str
    account_type: str
    status: str


class SetEnabledRequest(BaseModel):
    enabled: bool


class PrivilegedAccountActivitySummary(BaseModel):
    id: UUID
    display_name: str
    email: str
    account_type: str
    status: str
    linked_user_id: Optional[UUID]
    linked_user_display_name: Optional[str] = None
    created_at: datetime
    # None + sign_in_data_available=False means "unknown" (lookup failed/unsupported — e.g. Okta/Mock, or
    # AuditLog.Read.All not granted on this tenant); None + sign_in_data_available=True means "known — never
    # signed in". The UI must be able to tell these apart, not show a blank either way.
    last_sign_in_at: Optional[datetime] = None
    last_non_interactive_sign_in_at: Optional[datetime] = None
    sign_in_data_available: bool = False
    event_count: int = 0
    last_activity_at: Optional[datetime] = None
