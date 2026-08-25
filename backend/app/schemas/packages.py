from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.assignments import AssignmentResponse, require_justification


class PackageItemCreate(BaseModel):
    resource_type: str = Field(pattern="^(GROUP|ROLE|APPLICATION)$")
    resource_id: UUID
    app_role_external_id: Optional[str] = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def _validate_app_role(self) -> "PackageItemCreate":
        if self.resource_type == "APPLICATION":
            if not self.app_role_external_id:
                raise ValueError("app_role_external_id is required when resource_type is APPLICATION")
        else:
            self.app_role_external_id = None
        return self


class PackageItemResponse(BaseModel):
    id: UUID
    resource_type: str
    resource_id: UUID
    resource_display_name: Optional[str] = None
    app_role_external_id: Optional[str] = None


class PackageEligibilityPrincipalInput(BaseModel):
    principal_type: str = Field(pattern="^(USER|GROUP)$")
    principal_id: UUID


class PackageCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=2000)
    items: list[PackageItemCreate] = Field(min_length=1)
    principals: list[PackageEligibilityPrincipalInput] = []
    default_approver_id: Optional[UUID] = None
    default_fallback_approver_id: Optional[UUID] = None
    fallback_unlock_hours: Optional[int] = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _validate_fallback_unlock(self) -> "PackageCreate":
        if self.fallback_unlock_hours is not None and self.default_fallback_approver_id is None:
            raise ValueError("fallback_unlock_hours requires default_fallback_approver_id to be set")
        return self


class PackageEligibilityPrincipal(BaseModel):
    principal_type: str = Field(pattern="^(USER|GROUP)$")
    principal_id: UUID
    display_name: Optional[str] = None


class PackageResponse(BaseModel):
    id: UUID
    name: str
    description: Optional[str]
    status: str
    items: list[PackageItemResponse]
    default_approver_id: Optional[UUID] = None
    default_fallback_approver_id: Optional[UUID] = None
    fallback_unlock_hours: Optional[int] = None
    eligible_principals: list[PackageEligibilityPrincipal] = []
    created_at: datetime


class PackageUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=2000)
    items: Optional[list[PackageItemCreate]] = Field(default=None, min_length=1)


class PackageEligibilityUpdate(BaseModel):
    principals: list[PackageEligibilityPrincipalInput] = []
    default_approver_id: Optional[UUID] = None
    default_fallback_approver_id: Optional[UUID] = None
    fallback_unlock_hours: Optional[int] = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _validate_fallback_unlock(self) -> "PackageEligibilityUpdate":
        if self.fallback_unlock_hours is not None and self.default_fallback_approver_id is None:
            raise ValueError("fallback_unlock_hours requires default_fallback_approver_id to be set")
        return self


class PackageRequestCreate(BaseModel):
    assignment_type: str = Field(pattern="^(PERMANENT|TEMPORARY)$")
    start_time: Optional[datetime] = None
    expiration_time: Optional[datetime] = None
    justification: str = Field(min_length=3, max_length=2000)

    @field_validator("justification")
    @classmethod
    def _validate_justification(cls, value: str) -> str:
        return require_justification(value)

    @model_validator(mode="after")
    def _validate_duration(self) -> "PackageRequestCreate":
        if self.assignment_type == "TEMPORARY":
            if self.expiration_time is None:
                raise ValueError("expiration_time is required for a temporary assignment")
            if self.start_time is not None and self.expiration_time <= self.start_time:
                raise ValueError("expiration_time must be after start_time")
        else:
            self.expiration_time = None
        return self


class PackageAssignCreate(BaseModel):
    user_id: Optional[UUID] = None
    group_id: Optional[UUID] = None
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
    def _validate_duration(self) -> "PackageAssignCreate":
        if self.assignment_type == "TEMPORARY":
            if self.expiration_time is None:
                raise ValueError("expiration_time is required for a temporary assignment")
            if self.start_time is not None and self.expiration_time <= self.start_time:
                raise ValueError("expiration_time must be after start_time")
        else:
            self.expiration_time = None
        return self

    @model_validator(mode="after")
    def _validate_target(self) -> "PackageAssignCreate":
        if bool(self.user_id) == bool(self.group_id):
            raise ValueError("Provide exactly one of user_id or group_id")
        return self


class PackageAssignItemResult(BaseModel):
    package_item_id: UUID
    resource_type: str
    resource_id: UUID
    status: str
    assignment: Optional[AssignmentResponse] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None


class PackageAssignMemberResult(BaseModel):
    user_id: UUID
    user_display_name: Optional[str] = None
    package_assignment_id: UUID
    results: list[PackageAssignItemResult]


class PackageAssignResponse(BaseModel):
    package_id: UUID
    members: list[PackageAssignMemberResult]


class PackageAssignmentBatch(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    package_assignment_id: UUID
    package_id: UUID
    package_name: str
    user_id: UUID
    assignment_ids: list[UUID]
