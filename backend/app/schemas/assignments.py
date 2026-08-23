from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AssignmentCreate(BaseModel):
    user_id: UUID
    resource_type: str = Field(pattern="^(GROUP|ROLE)$")
    resource_id: UUID
    assignment_type: str = Field(pattern="^(PERMANENT|TEMPORARY)$")
    start_time: Optional[datetime] = None
    expiration_time: Optional[datetime] = None
    approver_id: Optional[UUID] = None
    justification: Optional[str] = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _validate_duration(self) -> "AssignmentCreate":
        if self.assignment_type == "TEMPORARY":
            if self.expiration_time is None:
                raise ValueError("expiration_time is required for a temporary assignment")
            if self.start_time is not None and self.expiration_time <= self.start_time:
                raise ValueError("expiration_time must be after start_time")
        else:
            self.expiration_time = None
        return self


class AssignmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    user_id: UUID
    user_display_name: Optional[str] = None
    resource_type: str
    resource_id: UUID
    resource_display_name: Optional[str] = None
    assignment_type: str
    status: str
    start_time: Optional[datetime]
    expiration_time: Optional[datetime]
    justification: Optional[str]
    requested_by: Optional[UUID]
    approved_by: Optional[UUID]
    activated_at: Optional[datetime]
    revoked_at: Optional[datetime]
    created_at: datetime
