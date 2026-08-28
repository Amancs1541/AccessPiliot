from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

MATCH_FIELDS = ("department", "job_title")


class BirthrightPolicyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    match_field: str = Field(pattern="^(department|job_title)$")
    match_value: str = Field(min_length=1, max_length=255)
    resource_type: str = Field(pattern="^(GROUP|ROLE|APPLICATION)$")
    resource_id: UUID
    app_role_external_id: Optional[str] = Field(default=None, max_length=100)
    assignment_type: str = Field(default="PERMANENT", pattern="^(PERMANENT|TEMPORARY)$")

    @field_validator("match_value")
    @classmethod
    def strip_match_value(cls, value: str) -> str:
        return value.strip()


class BirthrightPolicyUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    match_value: Optional[str] = Field(default=None, min_length=1, max_length=255)
    status: Optional[str] = Field(default=None, pattern="^(ACTIVE|DISABLED)$")


class BirthrightPolicyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    match_field: str
    match_value: str
    resource_type: str
    resource_id: UUID
    app_role_external_id: Optional[str]
    assignment_type: str
    status: str
    created_at: datetime
    updated_at: datetime


class BirthrightEvaluationResult(BaseModel):
    user_id: UUID
    matched_policies: int
    assignments_created: list[UUID]
