from __future__ import annotations

from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PortalAuthConfigureRequest(BaseModel):
    idp_type: str = Field(pattern="^(ENTRA|OKTA)$")
    tenant_id: Optional[str] = Field(default=None, max_length=200)
    client_id: Optional[str] = Field(default=None, max_length=255)
    authority: Optional[str] = Field(default=None, max_length=500)
    issuer: Optional[str] = Field(default=None, max_length=500)
    audience: Optional[str] = Field(default=None, max_length=500)
    scope: Optional[str] = Field(default=None, max_length=500)
    redirect_uri: Optional[str] = Field(default=None, max_length=500)
    breakglass_username: str = Field(min_length=3, max_length=100)
    breakglass_password: str = Field(min_length=12, max_length=200)


class PortalAuthConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    idp_type: str
    tenant_id: Optional[str]
    client_id: Optional[str]
    authority: Optional[str]
    issuer: Optional[str]
    audience: Optional[str]
    scope: Optional[str]
    redirect_uri: Optional[str]
    is_active: bool


class ActivatePortalAuthRequest(BaseModel):
    config_id: UUID
    test_token: str = Field(min_length=1, description="A real access token obtained by actually logging into the configured IDP, proving the configuration works end to end.")


class ActivatePortalAuthResponse(BaseModel):
    activated: bool
    idp_type: str


class BreakGlassLoginRequest(BaseModel):
    username: str
    password: str
    emergency_token: str = Field(min_length=1, description="The secret path segment from the console-generated emergency-access URL — checked before username/password.")


class BreakGlassLoginResponse(BaseModel):
    access_token: str


class PortalAuthConfigUpdateRequest(BaseModel):
    idp_type: str = Field(pattern="^(ENTRA|OKTA)$")
    tenant_id: Optional[str] = Field(default=None, max_length=200)
    client_id: Optional[str] = Field(default=None, max_length=255)
    authority: Optional[str] = Field(default=None, max_length=500)
    issuer: Optional[str] = Field(default=None, max_length=500)
    audience: Optional[str] = Field(default=None, max_length=500)
    scope: Optional[str] = Field(default=None, max_length=500)
    redirect_uri: Optional[str] = Field(default=None, max_length=500)


class PublicPortalAuthConfigResponse(BaseModel):
    configured: bool
    idp_type: Optional[str] = None
    tenant_id: Optional[str] = None
    client_id: Optional[str] = None
    authority: Optional[str] = None
    scope: Optional[str] = None
    redirect_uri: Optional[str] = None


class BreakGlassElevateResponse(BaseModel):
    access_token: str


class BreakglassPasswordRotateRequest(BaseModel):
    new_password: str = Field(min_length=12, max_length=200)
