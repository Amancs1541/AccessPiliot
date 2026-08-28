import logging

from fastapi import APIRouter, Depends, Request
from app.api.v1.routers_placeholder import router as placeholder_router
from app.core.config import get_settings
from app.security.auth import AuthenticatedUser, require_permission
from app.api.v1.providers import router as providers_router
from app.api.v1.directory import router as directory_router
from app.api.v1.assignments import router as assignments_router
from app.api.v1.audit import router as audit_router
from app.api.v1.packages import router as packages_router
from app.api.v1.onboarding import router as onboarding_router
from app.api.v1.policies import router as policies_router

logger = logging.getLogger("accesspilot.api.v1")
router = APIRouter(prefix="/api/v1")
router.include_router(directory_router)
router.include_router(assignments_router)
router.include_router(audit_router)
router.include_router(packages_router)
router.include_router(onboarding_router)
router.include_router(policies_router)
router.include_router(placeholder_router)
router.include_router(providers_router)

@router.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "AccessPilot"}

@router.get("/me", tags=["current-user"])
async def current_user(request: Request, user: AuthenticatedUser = Depends(require_permission("ME_READ"))) -> dict:
    if get_settings().environment == "development":
        logger.info(
            "ME RESPONSE\nuser_id: %s\nroles: %s",
            user.claims.get("oid"),
            list(user.roles),
            extra={"request_id": getattr(request.state, "request_id", "-")},
        )
    return {"id": user.subject, "displayName": user.display_name, "email": user.email, "tenantId": user.tenant_id, "roles": list(user.roles)}
