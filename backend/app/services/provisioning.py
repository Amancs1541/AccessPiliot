from __future__ import annotations

import re
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import IdentityProvider, User
from app.providers.base import NewUserRequest, NormalizedDomain, ProviderConflictError
from app.providers.graph_client import GraphError
from app.services.audit import record_audit
from app.services.directory_sync import upsert_user
from app.services.provider_configuration import _connector

_SLUG_RE = re.compile(r"[^a-z0-9]")


def _slugify(value: str) -> str:
    return _SLUG_RE.sub("", value.lower())


def build_username_local_part(convention: Optional[str], given_name: Optional[str], surname: Optional[str], fallback_email: str) -> str:
    """The naming-convention engine: turns an Admin-configured template (`{first}.{last}`, `{f}{last}`, ...) plus
    a person's real name into the local part of a UPN/email. Falls back to the CSV row's own email local-part
    whenever there isn't enough information to apply the template — never raises, never blocks provisioning."""
    fallback = (fallback_email.split("@")[0] or fallback_email).lower()
    if not convention or not given_name or not surname:
        return fallback
    first, last = _slugify(given_name), _slugify(surname)
    if not first or not last:
        return fallback
    try:
        result = convention.format(first=first, last=last, f=first[:1], l=last[:1]).strip().lower()
    except (KeyError, IndexError, ValueError):
        return fallback
    return result or fallback


async def primary_identity_provider(session: AsyncSession) -> Optional[IdentityProvider]:
    """Same selection rule as the existing admin 'Add user' endpoint's `_primary_provider`
    (backend/app/api/v1/directory.py) — prefer the real ENTRA provider, else any other configured connector-backed
    provider. Never the CSV bookkeeping provider itself, which has no real connector behind it."""
    providers = (await session.execute(select(IdentityProvider).where(IdentityProvider.type != "CSV"))).scalars().all()
    entra = next((provider for provider in providers if provider.type == "ENTRA"), None)
    return entra or (providers[0] if providers else None)


async def list_provisioning_domains(session: AsyncSession) -> list[NormalizedDomain]:
    provider = await primary_identity_provider(session)
    if provider is None:
        return []
    return await _connector(provider).get_domains()


async def provision_real_account(session: AsyncSession, *, display_name: str, email: str, given_name: Optional[str] = None, surname: Optional[str] = None, department: Optional[str], job_title: Optional[str], request_id: str) -> Optional[User]:
    """Finds-or-creates a REAL account for a CSV-sourced identity — via Microsoft Graph in production, or
    MockProvider in dev/tests — reusing the exact same `connector.create_user()` + `upsert_user()` path the
    existing admin 'Add user' feature already uses. This is what lets a birthright policy grant REAL Group/Role/
    Application membership: `_grant_provider_access` needs a User row whose external_id is a genuine object id the
    connector recognizes, not the CSV row's employeeId.

    Mapping engine: when the provider has a configured `provisioning_domain`, the UPN is built as
    `{local_part}@{provisioning_domain}` instead of trusting the CSV row's raw email domain (which the IdP may
    reject as unverified) — `local_part` comes from `username_convention` when configured, else the CSV email's
    own local part. Both are OPT-IN: with neither configured, behavior is identical to before this feature existed
    (the CSV row's email is used exactly as given).

    Never raises — returns None if no real provider is configured, if Graph rejects the request (e.g. the email's
    domain isn't verified on the tenant), or on any other provider failure. Callers treat None as 'stays
    local-only', the same graceful degradation this codebase already uses for every other provider call."""
    provider = await primary_identity_provider(session)
    if provider is None:
        return None
    connector = _connector(provider)
    upn = email
    if provider.provisioning_domain:
        local_part = build_username_local_part(provider.username_convention, given_name, surname, email)
        upn = f"{local_part}@{provider.provisioning_domain}"
    mail_nickname = upn.split("@")[0] or upn
    try:
        created = await connector.create_user(NewUserRequest(display_name=display_name, user_principal_name=upn, mail_nickname=mail_nickname, department=department, job_title=job_title))
        row = await upsert_user(session, provider.id, created.user)
        await record_audit(session, action="USER_PROVISIONED", target_type="USER", target_id=row.id, provider_id=provider.id, request_id=request_id, metadata={"source": "ONBOARDING", "email": email, "provisioned_upn": upn})
        return row
    except ProviderConflictError:
        matches = await connector.get_users(query=upn)
        existing = next((candidate for candidate in matches if candidate.email.lower() == upn.lower()), None)
        if existing is None:
            return None
        return await upsert_user(session, provider.id, existing)
    except GraphError:
        return None
