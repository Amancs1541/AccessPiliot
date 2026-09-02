import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.v1.routers_placeholder import router as placeholder_router
from app.core.config import get_settings
from app.db.session import get_db
from app.models import User
from app.security.auth import AuthenticatedUser, require_permission
from app.api.v1.providers import router as providers_router
from app.api.v1.directory import router as directory_router
from app.api.v1.assignments import router as assignments_router
from app.api.v1.audit import router as audit_router
from app.api.v1.packages import router as packages_router
from app.api.v1.onboarding import router as onboarding_router
from app.api.v1.policies import router as policies_router
from app.api.v1.setup import router as setup_router
from app.api.v1.auth import router as auth_router
from app.api.v1.breakglass_console import router as breakglass_console_router
from app.api.v1.security_settings import router as security_settings_router
from app.api.v1.branding import router as branding_router
from app.api.v1.sod import router as sod_router

logger = logging.getLogger("accesspilot.api.v1")
router = APIRouter(prefix="/api/v1")
router.include_router(directory_router)
router.include_router(assignments_router)
router.include_router(audit_router)
router.include_router(packages_router)
router.include_router(onboarding_router)
router.include_router(policies_router)
router.include_router(setup_router)
router.include_router(auth_router)
router.include_router(breakglass_console_router)
router.include_router(security_settings_router)
router.include_router(branding_router)
router.include_router(sod_router)
router.include_router(placeholder_router)
router.include_router(providers_router)

@router.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "AccessPilot"}

@router.get("/me", tags=["current-user"])
async def current_user(request: Request, user: AuthenticatedUser = Depends(require_permission("ME_READ")), db: AsyncSession = Depends(get_db)) -> dict:
    if get_settings().environment == "development":
        logger.info(
            "ME RESPONSE\nuser_id: %s\nroles: %s",
            user.claims.get("oid"),
            list(user.roles),
            extra={"request_id": getattr(request.state, "request_id", "-")},
        )
    # Best-effort enrichment from the local directory record (Entra-synced or CSV-onboarded) matching this
    # caller's real object id — powers the Profile page's department/job-title/employee-ID fields. A
    # Break-Glass session's subject never matches a real external_id, so this simply finds nothing for it.
    directory_record = (await db.execute(select(User).where(User.external_id == user.directory_object_id))).scalars().first()
    return {
        "id": user.subject, "displayName": user.display_name, "email": user.email, "tenantId": user.tenant_id, "roles": list(user.roles),
        "department": directory_record.department if directory_record else None,
        "jobTitle": directory_record.job_title if directory_record else None,
        "employeeId": directory_record.employee_id if directory_record else None,
    }
