from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

ALLOWED_USERNAME_TOKENS = {"first", "last", "f", "l"}


def _validate_username_convention(value: Optional[str]) -> Optional[str]:
    if value is None or value == "":
        return None
    try:
        value.format(first="a", last="b", f="a", l="b")
    except (KeyError, IndexError, ValueError) as exc:
        raise ValueError(f"Invalid naming convention — only {{first}}, {{last}}, {{f}}, {{l}} tokens are allowed.") from exc
    return value


class ProviderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    provider_type: str = Field(pattern="^(ENTRA|OKTA|MOCK|ACTIVE_DIRECTORY)$")
    tenant_id: str = Field(min_length=1, max_length=200)
    organization_url: Optional[str] = Field(default=None, max_length=500)
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
    organization_url: Optional[str] = Field(default=None, max_length=500)
    client_id: Optional[str] = None
    authority: Optional[HttpUrl] = None
    api_audience: Optional[str] = None
    api_scope: Optional[str] = None
    redirect_uri_metadata: Optional[dict[str, Any]] = None
    configuration_ref: Optional[str] = None
    sync_interval_minutes: Optional[int] = Field(default=None, ge=1, le=10080)
    max_self_activation_hours: Optional[int] = Field(default=None, ge=1, le=8760)
    provisioning_domain: Optional[str] = Field(default=None, max_length=255)
    username_convention: Optional[str] = Field(default=None, max_length=100)

    @field_validator("username_convention")
    @classmethod
    def _check_username_convention(cls, value: Optional[str]) -> Optional[str]:
        return _validate_username_convention(value)


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
    max_self_activation_hours: int
    provisioning_domain: Optional[str] = None
    username_convention: Optional[str] = None
    last_sync_at: Optional[datetime]
    # AD agent connectivity (Phase 1 — see app.services.agent). None/False for every ENTRA/OKTA/MOCK provider,
    # which never have an agent at all. agent_connected is a plain recency check (heartbeat seen within the last
    # few missed-heartbeat intervals), not a stored status — matches this app's "recomputed on read, never
    # cached" convention for anything that's just a view of current state.
    agent_configured: bool = False
    agent_last_seen_at: Optional[datetime] = None
    agent_connected: bool = False


class DomainResponse(BaseModel):
    name: str
    is_verified: bool
    is_default: bool


class AgentKeyResponse(BaseModel):
    """The agent's plaintext API key — returned exactly once, at generation time, never stored or shown again
    (only its PBKDF2 hash is kept). Same one-time-reveal convention as the bootstrap admin credential."""
    api_key: str
