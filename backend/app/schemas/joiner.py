from __future__ import annotations

from datetime import date, datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


class JoinerTargetInput(BaseModel):
    provider_id: UUID
    username: Optional[str] = Field(default=None, max_length=320)  # optional per-IdP override of the generated username


class JoinerCreate(BaseModel):
    first_name: str = Field(min_length=1, max_length=120)
    last_name: str = Field(min_length=1, max_length=120)
    work_email: str = Field(min_length=3, max_length=320)
    employee_id: Optional[str] = Field(default=None, max_length=100)
    department: str = Field(min_length=1, max_length=200)
    job_title: Optional[str] = Field(default=None, max_length=200)
    manager_id: UUID
    employee_category: Optional[str] = Field(default=None, pattern="^(EMPLOYEE|MANAGER)$")
    employment_type: Optional[str] = Field(default=None, pattern="^(EMPLOYEE|CONTRACTOR|INTERN|OTHER)$")
    start_at: datetime
    leaver_date: Optional[date] = None
    targets: list[JoinerTargetInput] = Field(min_length=1)
    # Optional profile fields, passed straight through to each target connector's create_user — settable at
    # creation time only (see app.providers.base.NewUserRequest), never stored on the User row or editable later.
    office: Optional[str] = Field(default=None, max_length=200)
    company: Optional[str] = Field(default=None, max_length=200)
    mobile_phone: Optional[str] = Field(default=None, max_length=50)
    street_address: Optional[str] = Field(default=None, max_length=255)
    city: Optional[str] = Field(default=None, max_length=100)
    state: Optional[str] = Field(default=None, max_length=100)
    postal_code: Optional[str] = Field(default=None, max_length=20)
    country: Optional[str] = Field(default=None, max_length=100)
    description: Optional[str] = Field(default=None, max_length=500)

    @field_validator("work_email")
    @classmethod
    def _email(cls, value: str) -> str:
        value = value.strip()
        if "@" not in value or value.startswith("@") or value.endswith("@"):
            raise ValueError("Enter a valid work email address")
        return value

    @model_validator(mode="after")
    def _dates(self) -> "JoinerCreate":
        if self.leaver_date is not None and self.leaver_date < self.start_at.date():
            raise ValueError("The leaver date cannot be before the start date")
        return self


class JoinerAccountResult(BaseModel):
    provider_id: UUID
    provider_name: str
    username: str
    status: str  # CREATED | ENABLED | FAILED
    error: Optional[str] = None
    temporary_password: Optional[str] = None  # ONLY ever set on the create / retry response, never stored


class JoinerResponse(BaseModel):
    id: UUID
    user_id: Optional[UUID] = None
    display_name: str
    work_email: str
    employee_id: Optional[str] = None
    department: Optional[str] = None
    job_title: Optional[str] = None
    start_at: datetime
    leaver_date: Optional[date] = None
    status: str
    targets: list[JoinerAccountResult]
    created_at: datetime
    activated_at: Optional[datetime] = None


class JoinerTargetResponse(BaseModel):
    provider_id: UUID
    name: str
    provider_type: str
    status: str
    provision_joiners: bool
    provisioning_domain: Optional[str] = None
    username_convention: Optional[str] = None


class JoinerTargetUpdate(BaseModel):
    enabled: bool
