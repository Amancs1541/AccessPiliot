from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AccessPilotError, request_id as get_request_id
from app.db.session import get_db
from app.models import BootstrapCredential
from app.schemas.portal_auth import ActivatePortalAuthRequest, ActivatePortalAuthResponse, PortalAuthConfigResponse, PortalAuthConfigureRequest
from app.services.bootstrap import decode_setup_session, portal_setup_is_needed, verify_bootstrap_login
from app.services.portal_auth import activate_portal_auth_config, create_pending_setup, get_pending_config, validate_token_against_config

router = APIRouter(prefix="/setup", tags=["setup"])

# A deliberately separate HTTPBearer instance from app.security.auth's — this whole module is a self-contained
# AuthN system for the portal's own first-run setup, independent of the real IDP-based auth it exists to bootstrap.
_bearer = HTTPBearer(auto_error=False)


class SetupStatusResponse(BaseModel):
    needs_setup: bool


class BootstrapLoginRequest(BaseModel):
    username: str
    password: str


class BootstrapLoginResponse(BaseModel):
    setup_token: str


async def require_setup_session(credentials: HTTPAuthorizationCredentials = Depends(_bearer), db: AsyncSession = Depends(get_db)) -> BootstrapCredential:
    """Capability-limited: proves ONLY that the caller completed bootstrap login. Later setup steps (configure/
    callback) hang off this dependency — it can never be satisfied by a real IDP-issued token (different signing
    key, different algorithm, different claim shape), and it stops working entirely the moment setup completes."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AccessPilotError("AUTHENTICATION_REQUIRED", "A setup session is required.", 401)
    return await decode_setup_session(db, credentials.credentials)


@router.get("/status", response_model=SetupStatusResponse)
async def status(db: AsyncSession = Depends(get_db)):
    return SetupStatusResponse(needs_setup=await portal_setup_is_needed(db))


@router.post("/bootstrap-login", response_model=BootstrapLoginResponse)
async def bootstrap_login(data: BootstrapLoginRequest, db: AsyncSession = Depends(get_db)):
    token = await verify_bootstrap_login(db, data.username, data.password)
    return BootstrapLoginResponse(setup_token=token)


@router.post("/configure", response_model=PortalAuthConfigResponse)
async def configure(data: PortalAuthConfigureRequest, _: BootstrapCredential = Depends(require_setup_session), db: AsyncSession = Depends(get_db)):
    return await create_pending_setup(db, data)


@router.post("/activate", response_model=ActivatePortalAuthResponse)
async def activate(data: ActivatePortalAuthRequest, request: Request, _: BootstrapCredential = Depends(require_setup_session), db: AsyncSession = Depends(get_db)):
    """Requires BOTH a still-valid setup session (proves this browser started the flow via bootstrap login) AND a
    real access token obtained by actually logging into the pending IDP config (proves the config genuinely
    works) — belt and suspenders before anything is activated or the bootstrap credential is destroyed."""
    config = await get_pending_config(db, data.config_id)
    await validate_token_against_config(data.test_token, config)
    activated = await activate_portal_auth_config(db, config.id, get_request_id(request))
    return ActivatePortalAuthResponse(activated=True, idp_type=activated.idp_type)
