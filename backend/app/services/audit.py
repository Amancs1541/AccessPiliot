from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog


async def record_audit(session: AsyncSession, *, action: str, target_type: str, request_id: str, target_id: Optional[UUID] = None, provider_id: Optional[UUID] = None, actor_user_id: Optional[UUID] = None, result: str = "SUCCESS", metadata: Optional[dict[str, Any]] = None) -> None:
    session.add(AuditLog(action=action, target_type=target_type, target_id=target_id, provider_id=provider_id, actor_user_id=actor_user_id, request_id=request_id, result=result, metadata_json=metadata))
