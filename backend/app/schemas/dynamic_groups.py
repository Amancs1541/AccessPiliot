from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.policies import BirthrightRuleJson


class DynamicGroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=2000)
    group_label: Optional[str] = Field(default=None, max_length=100)
    rule: BirthrightRuleJson


class DynamicGroupUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=2000)
    group_label: Optional[str] = Field(default=None, max_length=100)
    rule: Optional[BirthrightRuleJson] = None
    status: Optional[str] = Field(default=None, pattern="^(ACTIVE|DISABLED)$")


class DynamicGroupTargetSyncRequest(BaseModel):
    mode: str = Field(pattern="^(OFF|ELIGIBLE|ACTIVE)$")


class DynamicGroupTargetResponse(BaseModel):
    id: UUID
    provider_id: UUID
    provider_name: str
    provider_type: str
    sync_mode: str
    group_id: Optional[UUID] = None
    group_name: Optional[str] = None
    group_external_id: Optional[str] = None


class DynamicGroupResponse(BaseModel):
    id: UUID
    name: str
    description: Optional[str] = None
    group_label: Optional[str] = None
    rule: BirthrightRuleJson
    status: str
    created_at: datetime
    updated_at: datetime
    targets: list[DynamicGroupTargetResponse] = []


class DynamicGroupSyncResult(BaseModel):
    granted: int = 0
    revoked: int = 0
    promoted: int = 0
    demoted: int = 0
    target: DynamicGroupTargetResponse
