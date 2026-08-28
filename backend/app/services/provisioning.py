from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import IdentityProvider, User
from app.providers.base import NewUserRequest, ProviderConflictError
from app.providers.graph_client import GraphError
from app.services.audit import record_audit
from app.services.directory_sync import upsert_user
from app.services.provider_configuration import _connector


async def primary_identity_provider(session: AsyncSession) -> Optional[IdentityProvider]:
    """Same selection rule as the existing admin 'Add user' endpoint's `_primary_provider`
    (backend/app/api/v1/directory.py) — prefer the real ENTRA provider, else any other configured connector-backed
    provider. Never the CSV bookkeeping provider itself, which has no real connector behind it."""
    providers = (await session.execute(select(IdentityProvider).where(IdentityProvider.type != "CSV"))).scalars().all()
    entra = next((provider for provider in providers if provider.type == "ENTRA"), None)
    return entra or (providers[0] if providers else None)


async def provision_real_account(session: AsyncSession, *, display_name: str, email: str, department: Optional[str], job_title: Optional[str], request_id: str) -> Optional[User]:
    """Finds-or-creates a REAL account for a CSV-sourced identity — via Microsoft Graph in production, or
    MockProvider in dev/tests — reusing the exact same `connector.create_user()` + `upsert_user()` path the
    existing admin 'Add user' feature already uses. This is what lets a birthright policy grant REAL Group/Role/
    Application membership: `_grant_provider_access` needs a User row whose external_id is a genuine object id the
    connector recognizes, not the CSV row's employeeId.

    Never raises — returns None if no real provider is configured, if Graph rejects the request (e.g. the email's
    domain isn't verified on the tenant), or on any other provider failure. Callers treat None as 'stays
    local-only', the same graceful degradation this codebase already uses for every other provider call."""
    provider = await primary_identity_provider(session)
    if provider is None:
        return None
    connector = _connector(provider)
    mail_nickname = email.split("@")[0] or email
    try:
        created = await connector.create_user(NewUserRequest(display_name=display_name, user_principal_name=email, mail_nickname=mail_nickname, department=department, job_title=job_title))
        row = await upsert_user(session, provider.id, created.user)
        await record_audit(session, action="USER_PROVISIONED", target_type="USER", target_id=row.id, provider_id=provider.id, request_id=request_id, metadata={"source": "ONBOARDING", "email": email})
        return row
    except ProviderConflictError:
        matches = await connector.get_users(query=email)
        existing = next((candidate for candidate in matches if candidate.email.lower() == email.lower()), None)
        if existing is None:
            return None
        return await upsert_user(session, provider.id, existing)
    except GraphError:
        return None
