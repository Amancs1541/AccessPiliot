from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SecuritySettingsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    blur_enabled: bool
    blur_after_minutes: int
    lock_enabled: bool
    lock_after_minutes: int
    logout_enabled: bool
    logout_after_minutes: int
    timezone: str


class SecuritySettingsUpdateRequest(BaseModel):
    blur_enabled: bool
    blur_after_minutes: int = Field(gt=0, le=120)
    lock_enabled: bool
    lock_after_minutes: int = Field(gt=0, le=120)
    logout_enabled: bool
    logout_after_minutes: int = Field(gt=0, le=480)
    timezone: str = Field(min_length=1, max_length=50)

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f'"{value}" is not a recognized IANA timezone (e.g. "Europe/Berlin", "UTC").') from exc
        return value
