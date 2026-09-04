from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SodPolicyEntityCreate(BaseModel):
    conflict_side: str = Field(pattern="^(A|B)$")
    entity_type: str = Field(pattern="^(GROUP|ROLE|APPLICATION|PACKAGE)$")
    entity_id: UUID
    app_role_external_id: Optional[str] = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def _validate_app_role(self) -> "SodPolicyEntityCreate":
        if self.entity_type == "APPLICATION":
            if not self.app_role_external_id:
                raise ValueError("app_role_external_id is required when entity_type is APPLICATION")
        else:
            self.app_role_external_id = None
        return self


class SodPolicyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=2000)
    severity: str = Field(default="MEDIUM", pattern="^(LOW|MEDIUM|HIGH|CRITICAL)$")
    entities: list[SodPolicyEntityCreate]

    @model_validator(mode="after")
    def _validate_both_sides_present(self) -> "SodPolicyCreate":
        sides = {entity.conflict_side for entity in self.entities}
        if "A" not in sides or "B" not in sides:
            raise ValueError("A SoD policy needs at least one entity on side A and at least one on side B")
        return self

    @model_validator(mode="after")
    def _validate_no_entity_on_both_sides(self) -> "SodPolicyCreate":
        """The same real entitlement (GROUP/ROLE/PACKAGE by id, or the exact APPLICATION+role pair) can never
        appear on both sides — that would make the rule fire for literally every holder of that one entitlement,
        the same "baseline access" failure mode as putting AccessPilot's own login role in a rule, but caused by
        the rule's own shape instead of a bad entity choice."""
        by_key: dict[tuple[str, str, Optional[str]], set[str]] = {}
        for entity in self.entities:
            key = (entity.entity_type, str(entity.entity_id), entity.app_role_external_id)
            by_key.setdefault(key, set()).add(entity.conflict_side)
        conflicting = [key for key, sides in by_key.items() if len(sides) > 1]
        if conflicting:
            raise ValueError("The same entity cannot appear on both side A and side B of the same policy — that would make the rule fire for every holder of it")
        return self


class SodPolicyUpdate(SodPolicyCreate):
    status: str = Field(default="ACTIVE", pattern="^(ACTIVE|DISABLED)$")


class SodPolicyEntityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    conflict_side: str
    entity_type: str
    entity_id: UUID
    entity_display_name: Optional[str] = None
    app_role_external_id: Optional[str] = None
    entity_resolved: bool = True


class SodPolicyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    description: Optional[str]
    severity: str
    status: str
    entities: list[SodPolicyEntityResponse]
    created_at: datetime
    updated_at: datetime


class SodViolationHolding(BaseModel):
    # assignment_id is None for a holding detected only via direct-in-Entra data (e.g. a real group membership
    # with no corresponding AccessPilot AccessAssignment row) — source distinguishes the two.
    assignment_id: Optional[UUID] = None
    resource_type: str
    resource_id: UUID
    resource_display_name: Optional[str] = None
    app_role_external_id: Optional[str] = None
    source: str = "ACCESSPILOT"


class SodViolation(BaseModel):
    policy_id: UUID
    policy_name: str
    severity: str
    user_id: UUID
    user_display_name: Optional[str] = None
    side_a_holdings: list[SodViolationHolding]
    side_b_holdings: list[SodViolationHolding]
    # A currently-active, time-boxed risk acceptance for this exact (policy, user) pair — see SodException.
    # When true, this violation is real but deliberately tolerated, not something needing action.
    exception_active: bool = False
    exception_expires_at: Optional[datetime] = None


class SodCheckRequest(BaseModel):
    user_id: Optional[UUID] = None
    resource_type: str = Field(pattern="^(GROUP|ROLE|APPLICATION)$")
    resource_id: UUID
    app_role_external_id: Optional[str] = None


class SodCheckResponse(BaseModel):
    conflicts: list[SodPolicyResponse]


class SodExceptionCreate(BaseModel):
    sod_policy_id: UUID
    user_id: UUID
    justification: str = Field(min_length=3, max_length=2000)
    expires_at: datetime

    @model_validator(mode="after")
    def _validate_expires_in_future(self) -> "SodExceptionCreate":
        from datetime import timezone
        expires = self.expires_at if self.expires_at.tzinfo else self.expires_at.replace(tzinfo=timezone.utc)
        # Compared against a fresh now() at validation time — deliberately no upper bound on how far out this can
        # be set (a policy decision left to whoever's granting it), only that it can't already be in the past,
        # which would create an exception that's inert the instant it's saved.
        from datetime import datetime as _dt
        if expires <= _dt.now(timezone.utc):
            raise ValueError("expires_at must be in the future")
        return self


class SodExceptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    sod_policy_id: UUID
    policy_name: Optional[str] = None
    user_id: UUID
    user_display_name: Optional[str] = None
    user_email: Optional[str] = None
    justification: str
    granted_by: Optional[UUID] = None
    granted_by_display_name: Optional[str] = None
    expires_at: datetime
    revoked_at: Optional[datetime] = None
    is_active: bool
    created_at: datetime


class SodNotificationSettingsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    notify_on_new_violation: bool
    notify_on_exception_expiring: bool
    exception_expiring_warning_days: int
    notify_on_exception_requested: bool
    cooldown_enabled: bool
    cooldown_hours: int


class SodNotificationSettingsUpdateRequest(BaseModel):
    notify_on_new_violation: bool
    notify_on_exception_expiring: bool
    exception_expiring_warning_days: int = Field(gt=0, le=90)
    notify_on_exception_requested: bool
    cooldown_enabled: bool
    cooldown_hours: int = Field(gt=0, le=720)


class SodNotificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    notification_type: str
    sod_policy_id: Optional[UUID] = None
    policy_name: Optional[str] = None
    user_id: Optional[UUID] = None
    user_display_name: Optional[str] = None
    message: str
    read_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    created_at: datetime


class SodExceptionRequestCreate(BaseModel):
    sod_policy_id: UUID
    user_id: UUID
    justification: str = Field(min_length=3, max_length=2000)
    resource_type: str = Field(pattern="^(GROUP|ROLE|APPLICATION)$")
    resource_id: UUID
    app_role_external_id: Optional[str] = None
    # The rest of the originally-blocked AssignmentCreate's shape, so a grant can recreate it faithfully
    # (including routing through the same approver) instead of only ever landing on a bare ELIGIBLE row.
    approver_id: Optional[UUID] = None
    fallback_approver_id: Optional[UUID] = None
    fallback_unlock_hours: Optional[int] = Field(default=None, gt=0)
    assignment_type: str = Field(default="PERMANENT", pattern="^(PERMANENT|TEMPORARY)$")
    expiration_time: Optional[datetime] = None


class SodExceptionRequestGrant(BaseModel):
    expires_at: datetime

    @model_validator(mode="after")
    def _validate_expires_in_future(self) -> "SodExceptionRequestGrant":
        from datetime import datetime as _dt, timezone as _tz
        expires = self.expires_at if self.expires_at.tzinfo else self.expires_at.replace(tzinfo=_tz.utc)
        if expires <= _dt.now(_tz.utc):
            raise ValueError("expires_at must be in the future")
        return self


class SodExceptionRequestDeny(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=2000)


class SodExceptionRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    sod_policy_id: UUID
    policy_name: Optional[str] = None
    user_id: UUID
    user_display_name: Optional[str] = None
    requested_by: Optional[UUID] = None
    requested_by_display_name: Optional[str] = None
    justification: str
    resource_type: str
    resource_id: UUID
    resource_display_name: Optional[str] = None
    app_role_external_id: Optional[str] = None
    approver_id: Optional[UUID] = None
    approver_display_name: Optional[str] = None
    assignment_type: str
    expiration_time: Optional[datetime] = None
    status: str
    decided_by: Optional[UUID] = None
    decided_by_display_name: Optional[str] = None
    decided_at: Optional[datetime] = None
    denial_reason: Optional[str] = None
    sod_exception_id: Optional[UUID] = None
    created_at: datetime
