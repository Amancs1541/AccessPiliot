from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DepartmentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        return value.strip()


class DepartmentManagerUpdate(BaseModel):
    """None clears the department's manager; a UUID sets it — PATCH /policies/departments/{id}/manager."""
    manager_id: Optional[UUID] = None


class DepartmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    manager_id: Optional[UUID] = None
    manager_display_name: Optional[str] = None
    created_at: datetime
