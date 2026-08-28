from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class OnboardingCsvUpload(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1, description="Raw CSV text, header row included.")


class OnboardingImportResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    filename: str
    status: str
    total_records: int
    created_count: int
    updated_count: int
    disabled_count: int
    no_change_count: int
    failed_count: int
    access_revoked_count: int
    access_revoke_failed_count: int
    real_accounts_provisioned_count: int
    birthright_assignments_created_count: int
    error_summary: Optional[dict[str, Any]]
    created_at: datetime
    completed_at: Optional[datetime]


class OnboardingImportRecordResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    row_number: int
    employee_id: str
    action: str
    error_message: Optional[str]
    raw_data: Optional[dict[str, Any]]
