from __future__ import annotations

import re
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Deliberately a plain regex, not pydantic's EmailStr — that needs the email-validator package, a new dependency
# for validating one optional admin-entered field. Sane-practical, not full RFC 5322: good enough to catch a
# typo'd address without rejecting anything a real mail system would actually accept.
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SecuritySettingsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    blur_enabled: bool
    blur_after_minutes: int
    lock_enabled: bool
    lock_after_minutes: int
    logout_enabled: bool
    logout_after_minutes: int
    timezone: str
    support_contact_email: Optional[str] = None


class SecuritySettingsUpdateRequest(BaseModel):
    blur_enabled: bool
    blur_after_minutes: int = Field(gt=0, le=120)
    lock_enabled: bool
    lock_after_minutes: int = Field(gt=0, le=120)
    logout_enabled: bool
    logout_after_minutes: int = Field(gt=0, le=480)
    timezone: str = Field(min_length=1, max_length=50)
    support_contact_email: Optional[str] = Field(default=None, max_length=255)

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f'"{value}" is not a recognized IANA timezone (e.g. "Europe/Berlin", "UTC").') from exc
        return value

    @field_validator("support_contact_email")
    @classmethod
    def _validate_support_contact_email(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None
        if not _EMAIL_PATTERN.match(stripped):
            raise ValueError(f'"{stripped}" does not look like a valid email address.')
        return stripped


class SupportContactResponse(BaseModel):
    support_contact_email: Optional[str] = None
