from __future__ import annotations

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class NotificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    notification_type: str
    message: str
    link: Optional[str] = None
    read_at: Optional[datetime] = None
    created_at: datetime
