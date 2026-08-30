from __future__ import annotations

import asyncio
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

import jwt
from jwt import PyJWKClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AccessPilotError
from app.models import BootstrapCredential, BreakGlassAccount, PortalAuthConfig
from app.schemas.portal_auth import PortalAuthConfigureRequest, PortalAuthConfigUpdateRequest
from app.security.credential_hashing import hash_password, verify_password
from app.services.audit import record_audit

BREAKGLASS_SESSION_TTL_HOURS = 8


async def get_active_portal_auth_config(session: AsyncSession) -> Optional[PortalAuthConfig]:
    return (await session.execute(select(PortalAuthConfig).where(PortalAuthConfig.is_active.is_(True)))).scalars().first()


async def create_pending_setup(session: AsyncSession, data: PortalAuthConfigureRequest) -> PortalAuthConfig:
    """Stores a pending (inactive) IDP config + break-glass account together — nothing here is live yet. Any
    abandoned attempt from a previous /configure call is cleaned up first, so retrying never leaves orphaned rows
    behind (an already-ACTIVE config/account, from a previous completed setup, is never touched here)."""
    old_pending_configs = (await session.execute(select(PortalAuthConfig).where(PortalAuthConfig.is_active.is_(False)))).scalars().all()
    for row in old_pending_configs:
        await session.delete(row)
    old_pending_breakglass = (await session.execute(select(BreakGlassAccount).where(BreakGlassAccount.is_active.is_(False)))).scalars().all()
    for row in old_pending_breakglass:
        await session.delete(row)
    await session.flush()

    config = PortalAuthConfig(idp_type=data.idp_type, tenant_id=data.tenant_id, client_id=data.client_id, authority=data.authority, issuer=data.issuer, audience=data.audience, scope=data.scope, redirect_uri=data.redirect_uri, is_active=False)
    session.add(config)
    session.add(BreakGlassAccount(username=data.breakglass_username, password_hash=hash_password(data.breakglass_password), session_secret=secrets.token_urlsafe(32), is_active=False))
    await session.commit()
    await session.refresh(config)
    return config


async def get_pending_config(session: AsyncSession, config_id: UUID) -> PortalAuthConfig:
    config = await session.get(PortalAuthConfig, config_id)
    if config is None:
        raise AccessPilotError("CONFIG_NOT_FOUND", "The pending configuration was not found.", 404)
    if config.is_active:
        raise AccessPilotError("ALREADY_ACTIVE", "This configuration has already been activated.", 409)
    return config


async def validate_token_against_config(token: str, config: PortalAuthConfig) -> dict:
    """Proves a pending config actually works by validating a REAL token obtained from actually logging into it —
    the same JWKS-based validation shape as decode_access_token, parameterized by a PortalAuthConfig row instead
    of env-var Settings. The JWKS fetch is offloaded to a thread with a hard timeout, exactly like
    decode_access_token's own fix earlier this session — this must never be allowed to block the event loop."""
    if config.idp_type == "ENTRA":
        if not config.authority:
            raise AccessPilotError("INCOMPLETE_CONFIG", "Authority is required for an Entra configuration.", 400)
        jwks_url = f"{config.authority.rstrip('/')}/discovery/v2.0/keys"
        issuer = config.issuer or f"{config.authority.rstrip('/')}/v2.0"
    elif config.idp_type == "OKTA":
        if not config.issuer:
            raise AccessPilotError("INCOMPLETE_CONFIG", "Issuer is required for an Okta configuration.", 400)
        jwks_url = f"{config.issuer.rstrip('/')}/v1/keys"
        issuer = config.issuer
    else:
        raise AccessPilotError("INVALID_IDP_TYPE", f"Unsupported IDP type: {config.idp_type}", 400)

    audience = config.audience or config.client_id
    if not audience:
        raise AccessPilotError("INCOMPLETE_CONFIG", "An audience or client id is required.", 400)

    try:
        jwks_client = PyJWKClient(jwks_url)
        signing_key = (await asyncio.wait_for(asyncio.to_thread(jwks_client.get_signing_key_from_jwt, token), timeout=10)).key
        claims = jwt.decode(token, signing_key, algorithms=["RS256"], audience=audience, issuer=issuer)
    except asyncio.TimeoutError as exc:
        raise AccessPilotError("VALIDATION_FAILED", "Timed out validating the test login.", 504) from exc
    except jwt.PyJWTError as exc:
        raise AccessPilotError("VALIDATION_FAILED", f"The test login could not be validated: {exc}", 400) from exc

    if config.tenant_id and claims.get("tid") and claims["tid"] != config.tenant_id:
        raise AccessPilotError("VALIDATION_FAILED", "The test login's tenant does not match the configured tenant.", 400)
    return claims


async def activate_portal_auth_config(session: AsyncSession, config_id: UUID, request_id: str) -> PortalAuthConfig:
    """Called only after validate_token_against_config has already proven the pending config works. Deactivates
    any other active config (defensive; there should never be more than one), activates every pending break-glass
    account, and permanently deletes the bootstrap credential — self-destructing it, exactly per the design."""
    config = await session.get(PortalAuthConfig, config_id)
    if config is None:
        raise AccessPilotError("CONFIG_NOT_FOUND", "The pending configuration was not found.", 404)

    others = (await session.execute(select(PortalAuthConfig).where(PortalAuthConfig.is_active.is_(True)))).scalars().all()
    for other in others:
        other.is_active = False
    config.is_active = True

    pending_breakglass = (await session.execute(select(BreakGlassAccount).where(BreakGlassAccount.is_active.is_(False)))).scalars().all()
    for account in pending_breakglass:
        account.is_active = True

    await session.execute(delete(BootstrapCredential))
    await record_audit(session, action="PORTAL_AUTH_CONFIG_ACTIVATED", target_type="PORTAL_AUTH_CONFIG", target_id=config.id, request_id=request_id, metadata={"idp_type": config.idp_type})
    await session.commit()
    await session.refresh(config)
    return config


async def verify_breakglass_login(session: AsyncSession, username: str, password: str, emergency_token: str, request_id: str) -> str:
    account = (await session.execute(select(BreakGlassAccount).where(BreakGlassAccount.username == username, BreakGlassAccount.is_active.is_(True)))).scalars().first()
    # The emergency-URL token is checked BEFORE the password, and both failures raise the identical generic error
    # — knowing a valid username/password alone (e.g. sniffed from this same endpoint's request shape) is not
    # enough without also knowing the console-generated secret path.
    token_ok = account is not None and account.emergency_path_token is not None and secrets.compare_digest(account.emergency_path_token, emergency_token)
    if not token_ok or account is None or not account.session_secret or not verify_password(password, account.password_hash):
        raise AccessPilotError("INVALID_CREDENTIALS", "Incorrect break-glass username or password.", 401)
    account.last_login_at = datetime.now(timezone.utc)
    await record_audit(session, action="BREAKGLASS_LOGIN", target_type="BREAKGLASS_ACCOUNT", target_id=account.id, request_id=request_id, metadata={"username": account.username})
    await session.commit()
    now = datetime.now(timezone.utc)
    payload = {"purpose": "breakglass", "sub": str(account.id), "iat": now, "exp": now + timedelta(hours=BREAKGLASS_SESSION_TTL_HOURS)}
    return jwt.encode(payload, account.session_secret, algorithm="HS256")


async def decode_breakglass_token(session: AsyncSession, token: str) -> Optional[tuple[BreakGlassAccount, bool]]:
    """Returns (account, elevated) if `token` is a valid, unexpired break-glass session token, else None (never
    raises — callers treat None as 'not a break-glass token', trying the normal real-IDP path instead/first).
    `elevated` distinguishes the default, narrowly-scoped AccessPilot.BreakGlassAdmin session from one that has
    gone through the explicit /breakglass-elevate confirm-click to become full AccessPilot.Admin. There should
    realistically be at most one active break-glass account at a time."""
    accounts = (await session.execute(select(BreakGlassAccount).where(BreakGlassAccount.is_active.is_(True)))).scalars().all()
    for account in accounts:
        if not account.session_secret:
            continue
        try:
            claims = jwt.decode(token, account.session_secret, algorithms=["HS256"])
        except jwt.PyJWTError:
            continue
        if claims.get("purpose") == "breakglass" and claims.get("sub") == str(account.id):
            return account, bool(claims.get("elevated", False))
    return None


async def elevate_breakglass_session(session: AsyncSession, token: str, request_id: str) -> str:
    """The explicit, single-click escalation from the default restricted AccessPilot.BreakGlassAdmin role to full
    AccessPilot.Admin — confirmed with the user that this needs no re-entered password, since holding a valid
    break-glass session is itself already the trust boundary. Mints a NEW token signed with the SAME
    session_secret (so it dies exactly when the account is deactivated/deleted, same self-destruct property as
    every other token in this file), just with an added `elevated` claim."""
    result = await decode_breakglass_token(session, token)
    if result is None:
        raise AccessPilotError("AUTHENTICATION_REQUIRED", "A valid break-glass session is required.", 401)
    account, _ = result
    await record_audit(session, action="BREAKGLASS_ELEVATED", target_type="BREAKGLASS_ACCOUNT", target_id=account.id, request_id=request_id, metadata={"username": account.username})
    await session.commit()
    now = datetime.now(timezone.utc)
    payload = {"purpose": "breakglass", "sub": str(account.id), "elevated": True, "iat": now, "exp": now + timedelta(hours=BREAKGLASS_SESSION_TTL_HOURS)}
    return jwt.encode(payload, account.session_secret, algorithm="HS256")


async def verify_emergency_path_token(session: AsyncSession, token: str) -> bool:
    """Public, unauthenticated check used only to decide whether the hidden /emergency-access/:token page shows
    the login form or a generic not-found page — the real second-factor check still happens again inside
    verify_breakglass_login itself. Loop-and-compare (not a raw SQL equality filter) for constant-time comparison,
    matching decode_breakglass_token's own defensive style."""
    accounts = (await session.execute(select(BreakGlassAccount).where(BreakGlassAccount.is_active.is_(True), BreakGlassAccount.emergency_path_token.isnot(None)))).scalars().all()
    return any(secrets.compare_digest(account.emergency_path_token, token) for account in accounts)


async def get_public_portal_auth_config(session: AsyncSession) -> dict:
    """Non-secret fields only — used by the frontend to dynamically bootstrap MSAL when no build-time Entra env
    vars are set, so real end-user login can work without a rebuild once an admin activates a config here."""
    config = await get_active_portal_auth_config(session)
    if config is None:
        return {"configured": False}
    return {"configured": True, "idp_type": config.idp_type, "tenant_id": config.tenant_id, "client_id": config.client_id, "authority": config.authority, "scope": config.scope, "redirect_uri": config.redirect_uri}


async def update_active_portal_auth_config(session: AsyncSession, data: PortalAuthConfigUpdateRequest, request_id: str) -> PortalAuthConfig:
    """Break-Glass-only recovery action: edits the ACTIVE config directly and takes effect immediately, with no
    re-validation test-login round-trip — a deliberate speed-over-safety-net choice confirmed with the user, since
    this exists specifically for when the configured IDP is already broken."""
    config = await get_active_portal_auth_config(session)
    if config is None:
        raise AccessPilotError("CONFIG_NOT_FOUND", "There is no active portal authentication configuration.", 404)
    config.idp_type = data.idp_type
    config.tenant_id = data.tenant_id
    config.client_id = data.client_id
    config.authority = data.authority
    config.issuer = data.issuer
    config.audience = data.audience
    config.scope = data.scope
    config.redirect_uri = data.redirect_uri
    await record_audit(session, action="PORTAL_AUTH_CONFIG_UPDATED_VIA_BREAKGLASS", target_type="PORTAL_AUTH_CONFIG", target_id=config.id, request_id=request_id, metadata={"idp_type": config.idp_type})
    await session.commit()
    await session.refresh(config)
    return config


async def rotate_breakglass_password(session: AsyncSession, account_id: UUID, new_password: str, request_id: str) -> None:
    account = await session.get(BreakGlassAccount, account_id)
    if account is None or not account.is_active:
        raise AccessPilotError("ACCOUNT_NOT_FOUND", "The break-glass account was not found.", 404)
    account.password_hash = hash_password(new_password)
    await record_audit(session, action="BREAKGLASS_PASSWORD_ROTATED", target_type="BREAKGLASS_ACCOUNT", target_id=account.id, request_id=request_id, metadata={"username": account.username})
    await session.commit()
