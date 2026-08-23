from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class ProviderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    provider_type: str = Field(pattern="^(ENTRA|MOCK)$")
    tenant_id: str = Field(min_length=1, max_length=200)
    client_id: Optional[str] = None
    authority: Optional[HttpUrl] = None
    api_audience: Optional[str] = None
    api_scope: Optional[str] = None
    redirect_uri_metadata: Optional[dict[str, Any]] = None
    configuration_ref: Optional[str] = None


class ProviderUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    status: Optional[str] = Field(default=None, pattern="^(CONFIGURED|CONNECTED|ERROR|DISABLED)$")
    tenant_id: Optional[str] = None
    client_id: Optional[str] = None
    authority: Optional[HttpUrl] = None
    api_audience: Optional[str] = None
    api_scope: Optional[str] = None
    redirect_uri_metadata: Optional[dict[str, Any]] = None
    configuration_ref: Optional[str] = None
    sync_interval_minutes: Optional[int] = Field(default=None, ge=1, le=10080)


class ProviderCredentialUpdate(BaseModel):
    graph_client_id: Optional[str] = Field(default=None, min_length=1, max_length=255)
    graph_client_secret: str = Field(min_length=1, max_length=4000)


class ProviderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    provider_type: str = Field(validation_alias="type")
    status: str
    tenant_id: str
    organization_url: Optional[str]
    client_id: Optional[str]
    authority: Optional[str]
    api_audience: Optional[str]
    api_scope: Optional[str]
    redirect_uri_metadata: Optional[dict[str, Any]]
    configuration_ref: Optional[str]
    graph_client_id: Optional[str]
    credential_configured: bool
    sync_interval_minutes: Optional[int]
    last_sync_at: Optional[datetime]
