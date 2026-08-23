from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "AccessPilot"
    environment: Literal["development", "test", "staging", "production"] = "development"
    database_url: str = Field(..., min_length=1)
    redis_url: str | None = None
    entra_tenant_id: str | None = None
    entra_api_client_id: str | None = None
    entra_api_client_secret: str | None = None
    entra_authority: str | None = None
    entra_token_issuer: str | None = None
    entra_api_audience: str | None = None
    entra_api_scope: str | None = None
    graph_base_url: str = "https://graph.microsoft.com/v1.0"
    frontend_url: str = "http://localhost:5173"
    provider_mode: Literal["mock", "entra"] = "mock"
    provider_credential_key: str | None = None

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    @field_validator("frontend_url")
    @classmethod
    def validate_frontend_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("frontend_url must be an absolute HTTP URL")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
