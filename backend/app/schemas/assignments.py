from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def require_justification(value: str) -> str:
    """Shared rule: a justification must be real text, not just whitespace or a token filler."""
    stripped = value.strip()
    if len(stripped) < 3:
        raise ValueError("A justification of at least 3 characters is required.")
    return stripped


class AssignmentCreate(BaseModel):
    user_id: UUID
    resource_type: str = Field(pattern="^(GROUP|ROLE|APPLICATION)$")
    resource_id: UUID
    app_role_external_id: Optional[str] = Field(default=None, max_length=100)
    assignment_type: str = Field(pattern="^(PERMANENT|TEMPORARY)$")
    start_time: Optional[datetime] = None
    expiration_time: Optional[datetime] = None
    approver_id: Optional[UUID] = None
    fallback_approver_id: Optional[UUID] = None
    fallback_unlock_hours: Optional[int] = Field(default=None, gt=0)
    bypass_activation: bool = False
    # Only meaningful alongside bypass_activation (the only branch of create_assignment that checks SoD) — the
    # existing mandatory `justification` field below doubles as the override's justification, no second field
    # needed. Reachable only via the Admin-only ASSIGNMENT_CREATE endpoint, so no separate role check is needed
    # here the way activate_assignment's override needs one for its non-admin self-service callers.
    override_sod: bool = False
    justification: str = Field(min_length=3, max_length=2000)

    @field_validator("justification")
    @classmethod
    def _validate_justification(cls, value: str) -> str:
        return require_justification(value)

    @model_validator(mode="after")
    def _validate_fallback_unlock(self) -> "AssignmentCreate":
        if self.fallback_unlock_hours is not None and self.fallback_approver_id is None:
            raise ValueError("fallback_unlock_hours requires fallback_approver_id to be set")
        return self

    @model_validator(mode="after")
    def _validate_bypass_activation(self) -> "AssignmentCreate":
        if self.bypass_activation and (self.approver_id is not None or self.fallback_approver_id is not None):
            raise ValueError("bypass_activation cannot be combined with an approver or fallback approver — it grants access directly, with no approval step")
        return self

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

    @model_validator(mode="after")
    def _validate_app_role(self) -> "AssignmentCreate":
        if self.resource_type == "APPLICATION":
            if not self.app_role_external_id:
                raise ValueError("app_role_external_id is required when resource_type is APPLICATION")
        else:
            self.app_role_external_id = None
        return self


class AssignmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    user_id: UUID
    user_display_name: Optional[str] = None
    resource_type: str
    resource_id: UUID
    resource_display_name: Optional[str] = None
    app_role_external_id: Optional[str] = None
    assignment_type: str
    status: str
    start_time: Optional[datetime]
    expiration_time: Optional[datetime]
    justification: Optional[str]
    requested_by: Optional[UUID]
    approved_by: Optional[UUID]
    fallback_approver_id: Optional[UUID] = None
    fallback_unlock_at: Optional[datetime] = None
    bypass_activation: bool = False
    activated_at: Optional[datetime]
    revoked_at: Optional[datetime]
    created_at: datetime
    package_name: Optional[str] = None
    # Display-only: set when this ELIGIBLE/ACTIVE assignment depends on a currently-live SoD exception to stay
    # allowed — lets the frontend show the real ceiling instead of "No activation deadline" when one exists. The
    # real enforcement gate is always the live check at activation time, never this field.
    sod_exception_expires_at: Optional[datetime] = None


class AssignmentActivate(BaseModel):
    duration_hours: float = Field(gt=0)
    justification: str = Field(min_length=3, max_length=2000)
    # Only ever honored server-side when the caller is an Admin (self-service end users always get a hard block
    # on a genuine SoD conflict) — see activate_assignment(), which is the only place that can know who's calling.
    override_sod: bool = False

    @field_validator("justification")
    @classmethod
    def _validate_justification(cls, value: str) -> str:
        return require_justification(value)


class AssignmentApprove(BaseModel):
    justification: str = Field(min_length=3, max_length=2000)

    @field_validator("justification")
    @classmethod
    def _validate_justification(cls, value: str) -> str:
        return require_justification(value)


class AssignmentRevoke(BaseModel):
    justification: str = Field(min_length=3, max_length=2000)

    @field_validator("justification")
    @classmethod
    def _validate_justification(cls, value: str) -> str:
        return require_justification(value)
