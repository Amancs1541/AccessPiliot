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
