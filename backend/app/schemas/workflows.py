from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.assignments import require_justification

WORKFLOW_DEFINITION_STATUSES = ("DRAFT", "ACTIVE", "DISABLED")
APPROVAL_MODES = ("ANY_OF", "ALL_OF")
CONDITION_OPERATORS = ("EQUALS", "NOT_EQUALS")
INSTANCE_STATUSES = ("PENDING", "APPROVED", "REJECTED", "CANCELLED")
STAGE_STATUSES = ("PENDING", "SKIPPED", "APPROVED", "REJECTED", "CANCELLED")


class WorkflowStageCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    approval_mode: str = Field(default="ANY_OF", pattern="^(ANY_OF|ALL_OF)$")
    approver_user_ids: list[UUID] = Field(min_length=1)
    fallback_approver_ids: Optional[list[UUID]] = None
    escalate_after_hours: Optional[int] = Field(default=None, gt=0)
    condition_field: Optional[str] = Field(default=None, max_length=50)
    condition_operator: Optional[str] = Field(default=None, pattern="^(EQUALS|NOT_EQUALS)$")
    condition_value: Optional[str] = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def _validate(self) -> "WorkflowStageCreate":
        self.approver_user_ids = list(dict.fromkeys(self.approver_user_ids))
        if self.fallback_approver_ids is not None:
            self.fallback_approver_ids = list(dict.fromkeys(self.fallback_approver_ids)) or None
        if self.fallback_approver_ids and self.escalate_after_hours is None:
            raise ValueError("escalate_after_hours is required when fallback_approver_ids is set — otherwise the fallback could never actually act")
        if self.escalate_after_hours is not None and not self.fallback_approver_ids:
            raise ValueError("escalate_after_hours requires at least one fallback_approver_id")
        condition_parts = [self.condition_field, self.condition_operator, self.condition_value]
        if any(condition_parts) and not all(condition_parts):
            raise ValueError("A stage condition needs condition_field, condition_operator, and condition_value all set together, or none of them")
        return self


class WorkflowDefinitionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=2000)
    stages: list[WorkflowStageCreate] = Field(min_length=1)


class WorkflowDefinitionUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=2000)
    status: Optional[str] = Field(default=None, pattern="^(DRAFT|ACTIVE|DISABLED)$")
    stages: Optional[list[WorkflowStageCreate]] = Field(default=None, min_length=1)


class WorkflowApproverInfo(BaseModel):
    user_id: UUID
    display_name: Optional[str] = None


class WorkflowStageDefinitionResponse(BaseModel):
    id: UUID
    stage_number: int
    name: str
    approval_mode: str
    approvers: list[WorkflowApproverInfo]
    fallback_approvers: list[WorkflowApproverInfo] = []
    escalate_after_hours: Optional[int] = None
    condition_field: Optional[str] = None
    condition_operator: Optional[str] = None
    condition_value: Optional[str] = None


class WorkflowDefinitionResponse(BaseModel):
    id: UUID
    name: str
    description: Optional[str]
    status: str
    stages: list[WorkflowStageDefinitionResponse]
    in_flight_count: int = 0
    created_at: datetime
    updated_at: datetime


class WorkflowRequestCreate(BaseModel):
    workflow_definition_id: UUID
    title: str = Field(min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=2000)
    justification: str = Field(min_length=3, max_length=2000)
    payload: dict[str, str] = Field(default_factory=dict)

    @field_validator("justification")
    @classmethod
    def _validate_justification(cls, value: str) -> str:
        return require_justification(value)


class WorkflowDecideRequest(BaseModel):
    decision: str = Field(pattern="^(APPROVED|REJECTED)$")
    justification: str = Field(min_length=3, max_length=2000)

    @field_validator("justification")
    @classmethod
    def _validate_justification(cls, value: str) -> str:
        return require_justification(value)


class WorkflowStageDecisionInfo(BaseModel):
    decided_by: UUID
    decided_by_display_name: Optional[str] = None
    decision: str
    justification: Optional[str] = None
    decided_at: datetime


class WorkflowStageInstanceResponse(BaseModel):
    id: UUID
    stage_number: int
    name: str
    approval_mode: str
    required_approvers: list[WorkflowApproverInfo]
    status: str
    escalates_at: Optional[datetime] = None
    escalated_at: Optional[datetime] = None
    decisions: list[WorkflowStageDecisionInfo] = []
    created_at: datetime
    completed_at: Optional[datetime] = None


class WorkflowRequestResponse(BaseModel):
    id: UUID
    workflow_definition_id: UUID
    workflow_definition_name: str
    title: str
    description: Optional[str]
    justification: Optional[str]
    # Set only for an ASSIGNMENT-subject instance that's one item of a multi-item Package/Business-Role assignment
    # (each item gets its own independent WorkflowInstance — see create_assignment — but they share this id) so
    # the frontend can bundle them into one decide action instead of showing N unrelated-looking cards.
    batch_id: Optional[UUID] = None
    batch_label: Optional[str] = None
    payload: dict = {}
    requested_by: UUID
    requested_by_display_name: Optional[str] = None
    instance_status: str
    current_stage_number: int
    stages: list[WorkflowStageInstanceResponse]
    can_decide: bool = False
    created_at: datetime
    completed_at: Optional[datetime] = None
