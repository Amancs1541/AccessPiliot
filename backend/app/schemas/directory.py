from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    provider_id: UUID
    external_id: str
    email: str
    display_name: str
    given_name: Optional[str]
    surname: Optional[str]
    department: Optional[str]
    job_title: Optional[str]
    status: str
    employee_id: Optional[str] = None
    source: Optional[str] = None
    account_type: str = "NORMAL"
    linked_user_id: Optional[UUID] = None
    employee_category: Optional[str] = None
    manager_id: Optional[UUID] = None
    start_date: Optional[date] = None
    leaver_date: Optional[date] = None
    employment_type: Optional[str] = None
    last_synced_at: Optional[datetime]
    pending_attribute_change: bool = False


class UserAttributeUpdate(BaseModel):
    """Only department/job_title are editable here — the two fields birthright policies match on. Editing
    either triggers a real write to the identity's own provider (Entra/Okta) AND a birthright mover
    reconciliation (see app.services.birthright.reconcile_birthright_policies_for_user) — unless
    workflow_definition_id is set, in which case both are deferred until that workflow approves the change
    (see app.services.identity_attributes.apply_user_attribute_change)."""
    department: Optional[str] = Field(default=None, max_length=200)
    job_title: Optional[str] = Field(default=None, max_length=200)
    workflow_definition_id: Optional[UUID] = None


class GroupResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    external_id: str
    name: str
    description: Optional[str]
    is_privileged: bool
    status: str
    group_label: Optional[str] = None
    last_synced_at: Optional[datetime]


class GroupCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=255)
    description: Optional[str] = None
    mail_nickname: Optional[str] = Field(default=None, max_length=64)
    # STANDARD / PRIVILEGED (built-in), or a custom name from the GroupLabel admin list (see
    # app.services.group_labels) — a free string, never validated against that list (same "doesn't block an
    # out-of-list value" philosophy as User.department/Department). Set once at creation only; there is no
    # existing edit path for a group's other cosmetic fields (resource_code/naming_convention) to extend.
    group_label: Optional[str] = Field(default=None, max_length=100)


class RoleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    external_id: str
    name: str
    description: Optional[str]
    role_type: str
    is_privileged: bool
    status: str


class ApplicationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    external_id: str
    name: str
    status: str
    app_roles: Optional[list[dict[str, Any]]]
    last_synced_at: Optional[datetime]


class UserAccessItem(BaseModel):
    id: Optional[UUID] = None
    resource_type: str
    resource_display_name: Optional[str]
    status: str
    assignment_type: str
    expiration_time: Optional[datetime]
    package_name: Optional[str] = None
    source: str = "ACCESSPILOT"


class UserLicense(BaseModel):
    sku_id: str
    name: str


class UserAccessSummary(BaseModel):
    assignments: list[UserAccessItem]
    licenses: list[UserLicense]


class NamedPolicyRef(BaseModel):
    id: UUID
    name: str


class GroupAccessSummary(BaseModel):
    member_count: int
    active_assignment_count: int
    birthright_policies: list[NamedPolicyRef]
    sod_policies: list[NamedPolicyRef]
    access_packages: list[NamedPolicyRef]


class SyncRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    provider_id: UUID
    status: str
    started_at: datetime
    completed_at: Optional[datetime]
    users_processed: int
    groups_processed: int
    roles_processed: int
    errors_count: int
