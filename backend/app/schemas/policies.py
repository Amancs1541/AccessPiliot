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
    resource_type: str = Field(pattern="^(GROUP|ROLE|APPLICATION|PACKAGE|BUSINESS_ROLE)$")
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
    """match_field/match_value/resource_type/resource_id/app_role_external_id are None for an advanced
    (JSON-authored, is_advanced=True) policy — its real condition/action shape lives behind
    GET /policies/birthright/{id}/json instead. conditions_count/actions_count let the plain list view show a
    useful summary ("3 conditions", "2 actions") without fetching that JSON for every row."""
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    match_field: Optional[str] = None
    match_value: Optional[str] = None
    resource_type: Optional[str] = None
    resource_id: Optional[UUID] = None
    app_role_external_id: Optional[str] = None
    assignment_type: str
    status: str
    external_policy_id: Optional[str] = None
    is_advanced: bool = False
    conditions_count: int = 0
    actions_count: int = 0
    reconciliation_enabled: bool = True
    # Set only on the response to a create/update: what the immediate re-check of everyone affected did.
    recheck: Optional[dict] = None
    # Soft, non-blocking notices on create/update (e.g. a department nobody has) — the policy is saved regardless.
    warnings: list[str] = []
    created_at: datetime
    updated_at: datetime


class BirthrightEvaluationResult(BaseModel):
    user_id: UUID
    matched_policies: int
    assignments_created: list[UUID]


class BirthrightConditionJson(BaseModel):
    field: str
    operator: str
    value: str


class BirthrightActionJson(BaseModel):
    action: str = "ASSIGN"
    resourceType: str
    resource: str
    appRoleExternalId: Optional[str] = None
    assignmentType: str = "PERMANENT"


class BirthrightActionResolution(BaseModel):
    """One action's answer to 'does this resource name actually exist, and what does it resolve to' — the
    JSON editor's 'Check resources' button shows this per action before the admin commits to saving, since
    resolution otherwise only happens (and only surfaces as a hard save-time error) at POST/PUT .../json time."""
    resourceType: str
    resource: str
    found: bool
    resolvedId: Optional[UUID] = None
    resolvedName: Optional[str] = None
    error: Optional[str] = None


class BirthrightResolveActionsRequest(BaseModel):
    actions: list[BirthrightActionJson] = Field(min_length=1)


class BirthrightResolveActionsResponse(BaseModel):
    results: list[BirthrightActionResolution]


class BirthrightRuleJson(BaseModel):
    operator: str = "AND"
    conditions: list[BirthrightConditionJson]


class BirthrightScopeJson(BaseModel):
    identityType: Optional[str] = None


class BirthrightReconciliationJson(BaseModel):
    enabled: bool = True
    removeWhenConditionFails: bool = True


class BirthrightAuditJson(BaseModel):
    enabled: bool = True


class BirthrightPolicyJson(BaseModel):
    """The wire shape for the JSON create/view/edit endpoints — matches the schema an Admin can hand-author
    directly. `policyId` is a cosmetic business identifier (auto-generated from the internal id when omitted);
    the real primary key is always `id`, present on responses so the frontend has something stable to PUT back
    to regardless of whether policyId was ever set. `policyType` must be "BIRTHRIGHT" — this endpoint doesn't
    support any other policy type. `audit.enabled` is accepted for schema fidelity but can never actually be
    turned off — AccessPilot audits every policy-driven grant/revoke unconditionally (see app.services.audit);
    setting it to false is rejected outright rather than silently ignored."""
    id: Optional[UUID] = None
    policyId: Optional[str] = None
    policyType: str = "BIRTHRIGHT"
    name: str = Field(min_length=1, max_length=255)
    status: str = Field(default="ACTIVE", pattern="^(ACTIVE|DISABLED)$")
    scope: BirthrightScopeJson = Field(default_factory=BirthrightScopeJson)
    rule: BirthrightRuleJson
    actions: list[BirthrightActionJson] = Field(min_length=1)
    reconciliation: BirthrightReconciliationJson = Field(default_factory=BirthrightReconciliationJson)
    audit: BirthrightAuditJson = Field(default_factory=BirthrightAuditJson)


class GroupRoleMappingCreate(BaseModel):
    source_group_id: UUID
    resource_type: str = Field(pattern="^(ROLE|APPLICATION|BUSINESS_ROLE)$")
    resource_id: UUID
    app_role_external_id: Optional[str] = Field(default=None, max_length=100)
    assignment_type: str = Field(default="PERMANENT", pattern="^(PERMANENT|TEMPORARY)$")


class GroupRoleMappingUpdate(BaseModel):
    status: Optional[str] = Field(default=None, pattern="^(ACTIVE|DISABLED)$")


class GroupRoleMappingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    source_group_id: UUID
    source_group_name: str
    resource_type: str
    resource_id: UUID
    resource_display_name: str
    app_role_external_id: Optional[str]
    assignment_type: str
    status: str
    created_at: datetime
    updated_at: datetime


class GroupRoleMappingEvaluationResult(BaseModel):
    user_id: UUID
    matched_mappings: int
    assignments_created: list[UUID]
