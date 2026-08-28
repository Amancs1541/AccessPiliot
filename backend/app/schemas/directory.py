from __future__ import annotations

from datetime import datetime
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
    last_synced_at: Optional[datetime]


class UserCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=255)
    user_principal_name: str = Field(min_length=3, max_length=320)
    mail_nickname: Optional[str] = Field(default=None, max_length=64)
    department: Optional[str] = None
    job_title: Optional[str] = None


class UserCreateResponse(BaseModel):
    user: UserResponse
    temporary_password: Optional[str] = None


class GroupResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    external_id: str
    name: str
    description: Optional[str]
    is_privileged: bool
    status: str
    last_synced_at: Optional[datetime]


class GroupCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=255)
    description: Optional[str] = None
    mail_nickname: Optional[str] = Field(default=None, max_length=64)


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
