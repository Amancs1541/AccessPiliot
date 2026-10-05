from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.assignments import AssignmentResponse, require_justification

ROLE_TYPES = ("BUSINESS", "IT", "APPLICATION", "DIRECTORY", "PRIVILEGED", "COMPOSITE")
ROLE_STATUSES = ("DRAFT", "ACTIVE", "DISABLED", "ARCHIVED")
RISK_LEVELS = ("LOW", "MEDIUM", "HIGH", "CRITICAL")


class BusinessRoleItemCreate(BaseModel):
    resource_type: str = Field(pattern="^(GROUP|ROLE|APPLICATION)$")
    resource_id: UUID
    app_role_external_id: Optional[str] = Field(default=None, max_length=100)
    it_role_label: Optional[str] = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def _validate_app_role(self) -> "BusinessRoleItemCreate":
        if self.resource_type == "APPLICATION":
            if not self.app_role_external_id:
                raise ValueError("app_role_external_id is required when resource_type is APPLICATION")
        else:
            self.app_role_external_id = None
        return self


class BusinessRoleItemResponse(BaseModel):
    id: UUID
    resource_type: str
    resource_id: UUID
    resource_display_name: Optional[str] = None
    app_role_external_id: Optional[str] = None
    it_role_label: Optional[str] = None
    provider_id: Optional[UUID] = None
    provider_name: Optional[str] = None
    resource_code: Optional[str] = None
    naming_convention: Optional[str] = None


class BusinessRoleOwnerInfo(BaseModel):
    user_id: UUID
    display_name: Optional[str] = None
    email: Optional[str] = None


class BusinessRoleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=2000)
    role_type: str = Field(default="BUSINESS", pattern="^(BUSINESS|IT|APPLICATION|DIRECTORY|PRIVILEGED|COMPOSITE)$")
    department: Optional[str] = Field(default=None, max_length=200)
    risk_level: str = Field(default="LOW", pattern="^(LOW|MEDIUM|HIGH|CRITICAL)$")
    is_privileged: bool = False
    owner_ids: list[UUID] = []
    items: list[BusinessRoleItemCreate] = []
    default_approver_id: Optional[UUID] = None
    default_fallback_approver_id: Optional[UUID] = None
    fallback_unlock_hours: Optional[int] = Field(default=None, gt=0)
    review_frequency_days: Optional[int] = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _validate_fallback_unlock(self) -> "BusinessRoleCreate":
        if self.fallback_unlock_hours is not None and self.default_fallback_approver_id is None:
            raise ValueError("fallback_unlock_hours requires default_fallback_approver_id to be set")
        return self


class BusinessRoleUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=2000)
    role_type: Optional[str] = Field(default=None, pattern="^(BUSINESS|IT|APPLICATION|DIRECTORY|PRIVILEGED|COMPOSITE)$")
    department: Optional[str] = Field(default=None, max_length=200)
    status: Optional[str] = Field(default=None, pattern="^(DRAFT|ACTIVE|DISABLED|ARCHIVED)$")
    risk_level: Optional[str] = Field(default=None, pattern="^(LOW|MEDIUM|HIGH|CRITICAL)$")
    is_privileged: Optional[bool] = None
    owner_ids: Optional[list[UUID]] = None
    items: Optional[list[BusinessRoleItemCreate]] = None
    default_approver_id: Optional[UUID] = None
    default_fallback_approver_id: Optional[UUID] = None
    fallback_unlock_hours: Optional[int] = Field(default=None, gt=0)
    review_frequency_days: Optional[int] = Field(default=None, gt=0)


class BusinessRoleResponse(BaseModel):
    id: UUID
    name: str
    description: Optional[str]
    role_type: str
    department: Optional[str]
    status: str
    risk_level: str
    is_privileged: bool
    items: list[BusinessRoleItemResponse]
    owners: list[BusinessRoleOwnerInfo] = []
    default_approver_id: Optional[UUID] = None
    default_fallback_approver_id: Optional[UUID] = None
    fallback_unlock_hours: Optional[int] = None
    review_frequency_days: Optional[int] = None
    assigned_user_count: int = 0
    created_at: datetime
    updated_at: datetime


class ResourceReferenceUpdate(BaseModel):
    """Admin-set, cosmetic reference fields on a raw Group/Role/Application — see models.Group.resource_code."""
    resource_code: Optional[str] = Field(default=None, max_length=100)
    naming_convention: Optional[str] = Field(default=None, max_length=255)


class BusinessRoleAssignCreate(BaseModel):
    """Admin direct-assign (BUSINESS_ROLE_MANAGE) — mirrors PackageAssignCreate's user_id branch exactly. An
    approver_id left unset means every mapped item lands ELIGIBLE with no approval step, same as assigning a
    package or a raw resource directly with no approver configured."""
    user_id: UUID
    assignment_type: str = Field(pattern="^(PERMANENT|TEMPORARY)$")
    start_time: Optional[datetime] = None
    expiration_time: Optional[datetime] = None
    approver_id: Optional[UUID] = None
    justification: str = Field(min_length=3, max_length=2000)

    @field_validator("justification")
    @classmethod
    def _validate_justification(cls, value: str) -> str:
        return require_justification(value)

    @model_validator(mode="after")
    def _validate_duration(self) -> "BusinessRoleAssignCreate":
        if self.assignment_type == "TEMPORARY":
            if self.expiration_time is None:
                raise ValueError("expiration_time is required for a temporary assignment")
            if self.start_time is not None and self.expiration_time <= self.start_time:
                raise ValueError("expiration_time must be after start_time")
        else:
            self.expiration_time = None
        return self


class BusinessRoleAssignItemResult(BaseModel):
    item_id: UUID
    resource_type: str
    resource_id: UUID
    status: str
    assignment: Optional[AssignmentResponse] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None


class BusinessRoleAssignResponse(BaseModel):
    role_id: UUID
    user_id: UUID
    user_display_name: Optional[str] = None
    role_assignment_id: UUID
    results: list[BusinessRoleAssignItemResult]


class RoleAssignmentBatch(BaseModel):
    """One role-assignment action's items, grouped — lets the Assignments and My Approvals lists show a Business
    Role grant as a single row instead of one row per mapped item. Mirrors PackageAssignmentBatch exactly."""
    role_assignment_id: UUID
    role_id: UUID
    role_name: str
    user_id: UUID
    assignment_ids: list[UUID]


class BusinessRoleOwnerRename(BaseModel):
    """Mirrors PackageOwnerRename exactly — the owner portal's only edit power besides removing an item."""
    name: str = Field(min_length=1, max_length=255)


class BusinessRoleTally(BaseModel):
    name: str
    count: int


class BusinessRoleAnalytics(BaseModel):
    """Plan Step 6 — live-computed counts, matching this app's own convention (AccessReview's dashboard, SoD's
    detective scan): never a stored/materialized snapshot, always read fresh."""
    total_roles: int = 0
    active_roles: int = 0
    draft_roles: int = 0
    disabled_roles: int = 0
    archived_roles: int = 0
    privileged_roles: int = 0
    roles_with_no_owner: int = 0
    roles_with_open_sod_conflicts: int = 0
    unmapped_entitlements: int = 0
    total_assigned_users: int = 0
    top_roles_by_holders: list[BusinessRoleTally] = []


class BusinessRoleHolder(BaseModel):
    """One person's standing with this Business Role — one row per role_assignment_id batch they hold (normally
    just one, unless they were assigned it more than once over time)."""
    user_id: UUID
    user_display_name: Optional[str] = None
    user_email: Optional[str] = None
    role_assignment_id: UUID
    assigned_at: datetime
    items: list[BusinessRoleAssignItemResult]
