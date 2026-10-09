from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

RISK_TIERS = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}


class EntitlementCatalogEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    resource_type: str
    resource_id: UUID
    app_role_external_id: Optional[str] = None
    resource_display_name: str
    description: Optional[str] = None
    risk_tier: str
    owner_id: Optional[UUID] = None
    owner_display_name: Optional[str] = None
    updated_at: datetime


class EntitlementCatalogEntryUpdate(BaseModel):
    description: Optional[str] = None
    risk_tier: Optional[str] = None
    owner_id: Optional[UUID] = None

    @field_validator("risk_tier")
    @classmethod
    def validate_risk_tier(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in RISK_TIERS:
            raise ValueError(f"risk_tier must be one of {sorted(RISK_TIERS)}")
        return value
