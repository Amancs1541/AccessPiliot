from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AccessPilotError
from app.models import BootstrapCredential, PortalAuthConfig
from app.security.credential_hashing import hash_password, verify_password

SETUP_TOKEN_TTL_SECONDS = 900  # 15 minutes


async def portal_setup_is_needed(session: AsyncSession) -> bool:
    """False the instant EITHER an env-var-configured Entra login already works (today's default, unchanged —
    this is what keeps every existing AccessPilot deployment, this one included, completely unaffected) OR a
    PortalAuthConfig has been activated through the new setup flow. Only True for a genuinely fresh install with
    no portal login IDP configured anywhere."""
    settings = get_settings()
    if settings.entra_tenant_id and settings.entra_authority:
        return False
    active = (await session.execute(select(PortalAuthConfig).where(PortalAuthConfig.is_active.is_(True)))).scalars().first()
    return active is None


async def get_bootstrap_credential(session: AsyncSession) -> Optional[BootstrapCredential]:
    return (await session.execute(select(BootstrapCredential))).scalars().first()


async def ensure_bootstrap_credential(session: AsyncSession) -> Optional[str]:
    """Called once at startup. If setup is needed and no bootstrap credential exists yet, generates one and
    returns the PLAINTEXT password — the only time it is ever available — so the caller can log it. Returns None
    if setup isn't needed, or a credential already exists (idempotent across restarts, so a restart mid-setup
    never invalidates the credential the admin was just given)."""
    if not await portal_setup_is_needed(session):
        return None
    if await get_bootstrap_credential(session) is not None:
        return None
    password = secrets.token_urlsafe(18)
    session_secret = secrets.token_urlsafe(32)
    session.add(BootstrapCredential(username="admin", password_hash=hash_password(password), session_secret=session_secret))
    await session.commit()
    return password


async def verify_bootstrap_login(session: AsyncSession, username: str, password: str) -> str:
    """Returns a short-lived, capability-limited setup-session token on success."""
    credential = await get_bootstrap_credential(session)
    if credential is None or credential.username != username or not verify_password(password, credential.password_hash):
        raise AccessPilotError("INVALID_CREDENTIALS", "Incorrect bootstrap username or password.", 401)
    now = datetime.now(timezone.utc)
    payload = {"purpose": "setup", "sub": str(credential.id), "iat": now, "exp": now + timedelta(seconds=SETUP_TOKEN_TTL_SECONDS)}
    return jwt.encode(payload, credential.session_secret, algorithm="HS256")


async def decode_setup_session(session: AsyncSession, token: str) -> BootstrapCredential:
    """Validates a setup-session token. Self-expiring by construction beyond its own TTL: once the bootstrap
    credential row is deleted (setup completed), its session_secret is gone, so every previously-issued token
    instantly and permanently fails to verify — no separate revocation bookkeeping needed."""
    credential = await get_bootstrap_credential(session)
    if credential is None:
        raise AccessPilotError("AUTHENTICATION_REQUIRED", "Setup has already been completed.", 401)
    try:
        claims = jwt.decode(token, credential.session_secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise AccessPilotError("AUTHENTICATION_REQUIRED", "Invalid or expired setup session.", 401) from exc
    if claims.get("purpose") != "setup" or claims.get("sub") != str(credential.id):
        raise AccessPilotError("AUTHENTICATION_REQUIRED", "Invalid setup session.", 401)
    return credential
