from __future__ import annotations

from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class GroupOwnerInfo(BaseModel):
    user_id: UUID
    display_name: Optional[str] = None
    email: Optional[str] = None


class GroupOwnersUpdate(BaseModel):
    user_ids: list[UUID] = []


class GroupOwnerSelfServiceUpdate(BaseModel):
    """The group owner portal's only edit power — cosmetic fields alone, mirroring the narrow scope of
    owner_rename_package/owner_rename_business_role. Fields left unset are left untouched."""
    description: Optional[str] = None
    group_label: Optional[str] = None
