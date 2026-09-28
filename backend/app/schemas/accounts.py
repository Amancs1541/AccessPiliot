from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class IdentityAccountResponse(BaseModel):
    id: UUID
    provider_id: UUID
    provider_name: str
    provider_type: str
    external_id: str
    username: Optional[str] = None
    status: str
    provisioned_by: str
    is_primary: bool
    created_at: datetime


class AccountEnabledRequest(BaseModel):
    enabled: bool


class AccountActionResult(BaseModel):
    account_id: UUID
    provider_name: str
    ok: bool
    already: bool = False
    error: Optional[str] = None


class PersonAccountsActionResponse(BaseModel):
    user_status: str
    results: list[AccountActionResult]
    accounts: list[IdentityAccountResponse]
