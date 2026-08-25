from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AuditLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    timestamp: datetime
    actor_user_id: Optional[UUID]
    actor_display_name: Optional[str] = None
    action: str
    target_type: str
    target_id: Optional[UUID]
    target_user_display_name: Optional[str] = None
    target_user_email: Optional[str] = None
    provider_id: Optional[UUID]
    provider_name: Optional[str] = None
    request_id: str
    result: str
    metadata: Optional[dict[str, Any]] = None
