"""Phase 1 of the Active Directory connector plan: agent connectivity only. A small on-prem agent process calls
OUT to AccessPilot over HTTPS (never the reverse) because on-prem AD's LDAP port is normally unreachable from
outside the corporate network, unlike Entra/Okta's directly-reachable cloud APIs — the same pattern Entra Connect
/ Okta's AD agent use. This phase proves that channel works: issuing the agent its own credential and recording
its heartbeats. No LDAP/AD read or write happens anywhere in this file yet — that's a later phase."""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AccessPilotError
from app.models import IdentityProvider
from app.security.credential_hashing import hash_password, verify_password
from app.services.audit import record_audit


async def _get_ad_provider(session: AsyncSession, provider_id: UUID) -> IdentityProvider:
    provider = await session.get(IdentityProvider, provider_id)
    if provider is None:
        raise AccessPilotError("PROVIDER_NOT_FOUND", "The provider was not found.", 404)
    if provider.type != "ACTIVE_DIRECTORY":
        raise AccessPilotError("PROVIDER_NOT_ACTIVE_DIRECTORY", "The connectivity agent only applies to an Active Directory provider.", 409)
    return provider


async def generate_agent_key(session: AsyncSession, provider_id: UUID, request_id: str) -> str:
    """Issues a fresh agent API key, invalidating any previous one — the plaintext is returned exactly once and
    never stored; only its PBKDF2 hash is kept (see security.credential_hashing), same one-time-reveal convention
    as the bootstrap admin credential (app.services.bootstrap)."""
    provider = await _get_ad_provider(session, provider_id)
    api_key = secrets.token_urlsafe(32)
    provider.agent_api_key_hash = hash_password(api_key)
    provider.agent_last_seen_at = None  # a regenerated key means the old agent process is no longer trusted
    await record_audit(session, action="PROVIDER_AGENT_KEY_GENERATED", target_type="PROVIDER", target_id=provider.id, provider_id=provider.id, request_id=request_id)
    await session.commit()
    return api_key


async def record_heartbeat(session: AsyncSession, provider_id: UUID, api_key: str) -> IdentityProvider:
    """Called by the agent itself, not an interactive admin — authenticated by the per-provider API key, not a
    user session. Updates only the liveness timestamp; nothing about directory data changes in this phase."""
    provider = await _get_ad_provider(session, provider_id)
    if not provider.agent_api_key_hash or not verify_password(api_key, provider.agent_api_key_hash):
        raise AccessPilotError("INVALID_AGENT_KEY", "This agent key is invalid or has been regenerated.", 401)
    provider.agent_last_seen_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(provider)
    return provider
