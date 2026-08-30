from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SecuritySettingsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    blur_enabled: bool
    blur_after_minutes: int
    lock_enabled: bool
    lock_after_minutes: int


class SecuritySettingsUpdateRequest(BaseModel):
    blur_enabled: bool
    blur_after_minutes: int = Field(gt=0, le=120)
    lock_enabled: bool
    lock_after_minutes: int = Field(gt=0, le=120)
