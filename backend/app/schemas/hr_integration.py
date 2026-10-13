from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class HrIntegrationSettingsResponse(BaseModel):
    enabled: bool
    auth_method: str = "SESSION"
    base_url: Optional[str] = None
    username: Optional[str] = None
    credential_configured: bool = False
    api_key_configured: bool = False
    sync_interval_minutes: Optional[int] = None
    last_sync_at: Optional[datetime] = None
    last_sync_status: Optional[str] = None
    last_sync_error: Optional[str] = None


class HrIntegrationSettingsUpdate(BaseModel):
    enabled: Optional[bool] = None
    auth_method: Optional[str] = Field(default=None, pattern="^(SESSION|API_KEY)$")
    base_url: Optional[str] = Field(default=None, max_length=500)
    username: Optional[str] = Field(default=None, max_length=255)
    password: Optional[str] = Field(default=None, max_length=500)
    api_key: Optional[str] = Field(default=None, max_length=500)
    sync_interval_minutes: Optional[int] = Field(default=None, ge=5, le=10080)
    clear_sync_interval_minutes: bool = False


class HrSyncResult(BaseModel):
    status: str
    error: Optional[str] = None
    import_id: Optional[str] = None


class HrSyncTriggerTokenResponse(BaseModel):
    token: str


class HrSyncTriggerTokenSummary(BaseModel):
    configured: bool
    created_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None
