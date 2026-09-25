from __future__ import annotations

from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class UserHierarchyUpdate(BaseModel):
    employee_category: Optional[str] = Field(default=None, pattern="^(EMPLOYEE|MANAGER)$")
    manager_id: Optional[UUID] = None
    # Explicit clear-flags, since None on the fields above is ambiguous between "leave unchanged" and "clear it"
    # in a PATCH — mirrors how the rest of this app's PATCH endpoints only ever touch a field the caller actually
    # sent (see BirthrightPolicyUpdate/GroupRoleMappingUpdate), just made explicit here since both real values
    # here are meaningfully different from "absent."
    clear_employee_category: bool = False
    clear_manager: bool = False


class UserHierarchyNode(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    display_name: str
    email: str
    status: str
    employee_category: Optional[str] = None
    manager_id: Optional[UUID] = None
