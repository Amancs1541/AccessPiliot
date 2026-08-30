from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import AccessPilotError, request_id as get_request_id
from app.db.session import get_db
from app.schemas.portal_auth import BreakGlassElevateResponse, BreakGlassLoginRequest, BreakGlassLoginResponse, PublicPortalAuthConfigResponse
from app.services.portal_auth import elevate_breakglass_session, get_public_portal_auth_config, verify_breakglass_login, verify_emergency_path_token

router = APIRouter(prefix="/auth", tags=["auth"])

# Deliberately separate from app.security.auth's HTTPBearer, same reasoning as setup.py's own _bearer: this only
# ever needs to prove "holds a currently-valid break-glass token" (elevated or not), never a real IDP-issued one.
_breakglass_bearer = HTTPBearer(auto_error=False)


@router.post("/breakglass-login", response_model=BreakGlassLoginResponse)
async def breakglass_login(data: BreakGlassLoginRequest, request: Request, db: AsyncSession = Depends(get_db)):
    """Public — a break-glass account is dormant until setup activates it, and is meant purely as an emergency
    recovery path into the portal if the configured real IDP itself is what's broken, never a normal login.
    `emergency_token` (the secret path segment from the console-generated URL) is required alongside username/
    password — this endpoint's own existence is otherwise discoverable via browser network tools, so the token
    check here is a real second factor, not just a UI convenience gating the login form."""
    token = await verify_breakglass_login(db, data.username, data.password, data.emergency_token, get_request_id(request))
    return BreakGlassLoginResponse(access_token=token)


@router.get("/emergency-access/{token}/verify")
async def verify_emergency_access(token: str, db: AsyncSession = Depends(get_db)):
    """Public. Backs the hidden /emergency-access/:token frontend page's decision to show the login form or a
    generic not-found page. A wrong/guessed token 404s via the exact same generic handler as any nonexistent
    route (see app.core.errors.http_error_handler) — indistinguishable from a route that was never registered."""
    if not await verify_emergency_path_token(db, token):
        raise StarletteHTTPException(status_code=404)
    return {"valid": True}


@router.post("/breakglass-elevate", response_model=BreakGlassElevateResponse)
async def breakglass_elevate(request: Request, credentials: HTTPAuthorizationCredentials = Depends(_breakglass_bearer), db: AsyncSession = Depends(get_db)):
    """The explicit, single-click escalation from the default restricted AccessPilot.BreakGlassAdmin role to full
    AccessPilot.Admin — requires only an already-valid break-glass session token, no re-entered password."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AccessPilotError("AUTHENTICATION_REQUIRED", "A valid break-glass session is required.", 401)
    token = await elevate_breakglass_session(db, credentials.credentials, get_request_id(request))
    return BreakGlassElevateResponse(access_token=token)


@router.get("/portal-config", response_model=PublicPortalAuthConfigResponse)
async def portal_config(db: AsyncSession = Depends(get_db)):
    """Public, non-secret fields only. Lets the frontend dynamically bootstrap MSAL from the active
    PortalAuthConfig when no build-time VITE_ENTRA_* env vars are set, instead of requiring a rebuild."""
    return PublicPortalAuthConfigResponse(**await get_public_portal_auth_config(db))
