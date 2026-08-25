from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.audit import AuditLogResponse
from app.security.auth import AuthenticatedUser, require_permission
from app.services.audit_read import list_audit_logs

router = APIRouter(tags=["audit"])
audit_read = require_permission("AUDIT_READ")


@router.get("/audit-logs", response_model=list[AuditLogResponse])
async def get_audit_logs(_: AuthenticatedUser = Depends(audit_read), db: AsyncSession = Depends(get_db)):
    return [
        AuditLogResponse(
            id=entry.id, timestamp=entry.timestamp, actor_user_id=entry.actor_user_id, actor_display_name=hydrated["actor_display_name"],
            action=entry.action, target_type=entry.target_type, target_id=entry.target_id, provider_id=entry.provider_id,
            provider_name=hydrated["provider_name"], request_id=entry.request_id, result=entry.result, metadata=entry.metadata_json,
            target_user_display_name=hydrated["target_user_display_name"], target_user_email=hydrated["target_user_email"],
        )
        for entry, hydrated in await list_audit_logs(db)
    ]
