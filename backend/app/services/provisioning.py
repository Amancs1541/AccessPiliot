from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import IdentityAccount, IdentityProvider, User
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


_DC_RE = re.compile(r"DC=([^,]+)", re.IGNORECASE)


def _domain_from_base_dn(base_dn: Optional[str]) -> Optional[str]:
    """Active Directory's base DN already IS its real DNS domain name — "DC=TeamDEV,DC=local" literally means
    the domain "TeamDEV.local". Used as AD's own default UPN suffix (see username_for_provider below) so a new AD
    account gets a real AD-domain UPN out of the box, never silently inheriting the Entra tenant's cloud domain
    just because nobody separately configured a "provisioning domain" for AD too — confirmed live this was
    happening: a joiner's AD account was created with userPrincipalName ending in the Entra tenant's
    onmicrosoft.com domain instead of the AD domain, which is wrong for an on-prem account."""
    if not base_dn:
        return None
    parts = _DC_RE.findall(base_dn)
    return ".".join(parts) if parts else None


def username_for_provider(provider: IdentityProvider, given_name: Optional[str], surname: Optional[str], fallback_email: str, override: Optional[str] = None) -> str:
    """The account's username in one IdP: an explicit override; else, when the IdP has a provisioning domain or
    naming convention, the naming-convention local part at that domain; else — for Active Directory specifically —
    the domain derived from its own base DN (see _domain_from_base_dn); else the fallback email as given (the
    original behavior, still correct for Entra/Okta, which have no equivalent "this IS my domain" field to derive
    from — a cloud tenant can have many verified domains, so borrowing the work email's own domain remains the
    right default there). Shared by Joiner (app.services.joiner._username_for) and assignment-time
    auto-provisioning (app.services.accounts.ensure_account_in_provider) so both pick the same username for the
    same person+provider."""
    if override and override.strip():
        return override.strip()
    ad_domain = _domain_from_base_dn(getattr(provider, "tenant_id", None)) if provider.type == "ACTIVE_DIRECTORY" else None
    if provider.provisioning_domain or provider.username_convention or ad_domain:
        domain = provider.provisioning_domain or ad_domain or (fallback_email.split("@", 1)[1] if "@" in fallback_email else "")
        local = build_username_local_part(provider.username_convention, given_name, surname, fallback_email)
        return f"{local}@{domain}" if domain else local
    return fallback_email


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
        row, _ = await upsert_user(session, provider.id, created.user)
        await record_audit(session, action="USER_PROVISIONED", target_type="USER", target_id=row.id, provider_id=provider.id, request_id=request_id, metadata={"source": "ONBOARDING", "email": email, "provisioned_upn": upn})
        return row
    except ProviderConflictError:
        matches = await connector.get_users(query=upn)
        existing = next((candidate for candidate in matches if candidate.email.lower() == upn.lower()), None)
        if existing is None:
            return None
        row, _ = await upsert_user(session, provider.id, existing)
        return row
    except GraphError:
        return None


async def provision_secondary_accounts(session: AsyncSession, person: User, *, department: Optional[str], job_title: Optional[str], request_id: str) -> list[IdentityAccount]:
    """The CSV/HR-onboarding counterpart to Joiner's own account-mapping auto-detection
    (app.services.joiner._resolve_wanted_targets, which calls the same required_providers_for_profile): a person
    synced in from an HR app only ever got a REAL account in the single primary provider (see
    provision_real_account above) — this additionally provisions a real account in every OTHER directory their
    department/job-title birthright access would need, so an HR-synced person ends up with the same
    multi-directory footprint a Joiner submission would have given them. Idempotent (skips a directory the person
    already has an account in) and best-effort per directory — one provider failing or not supporting
    provisioning yet never blocks another, never raises, mirrors provision_real_account's own graceful
    degradation."""
    from app.services.birthright import required_providers_for_profile

    needed_provider_ids = await required_providers_for_profile(session, department=department, job_title=job_title, status=person.status, email=person.email)
    if not needed_provider_ids:
        return []
    already_has = {person.provider_id} | {row.provider_id for row in (await session.scalars(select(IdentityAccount).where(IdentityAccount.user_id == person.id))).all()}
    created: list[IdentityAccount] = []
    for provider_id in needed_provider_ids:
        if provider_id in already_has:
            continue
        provider = await session.get(IdentityProvider, provider_id)
        if provider is None or provider.type == "CSV" or not provider.provision_joiners:
            continue
        username = username_for_provider(provider, person.given_name, person.surname, person.email)
        try:
            created_user = await _connector(provider).create_user(NewUserRequest(display_name=person.display_name, user_principal_name=username, mail_nickname=username.split("@")[0] or username, department=department, job_title=job_title, given_name=person.given_name, surname=person.surname, enabled=person.status == "ACTIVE"))
        except (ProviderConflictError, GraphError, NotImplementedError):
            continue
        account = IdentityAccount(user_id=person.id, provider_id=provider.id, external_id=created_user.user.external_id, username=username, status="ACTIVE" if person.status == "ACTIVE" else "DISABLED", provisioned_by="ONBOARDING")
        session.add(account)
        await session.flush()
        await record_audit(session, action="USER_PROVISIONED", target_type="USER", target_id=person.id, provider_id=provider.id, request_id=request_id, metadata={"source": "ONBOARDING_SECONDARY", "username": username})
        created.append(account)
    return created


async def sweep_missing_secondary_accounts(session: AsyncSession) -> int:
    """Worker entry point: the HR/CSV-onboarding counterpart to app.services.joiner.sweep_failed_joiner_targets.
    provision_secondary_accounts() above only ever runs as part of a sync for that specific person — fine if
    automatic HR sync is on, but a person synced once (a one-off CSV upload, or HR sync never scheduled) whose
    needed directory was unreachable at the time stays missing an account forever otherwise. Gated behind
    LifecycleSettings.secondary_account_retry_interval_minutes (NULL/unset = off, this sweep is a no-op) so, once
    set, every real/active person is rechecked on that interval regardless of whether/when their next HR sync
    happens — the same guarantee joiner_retry_interval_minutes already gives Joiner."""
    from app.services.lifecycle import get_lifecycle_settings

    settings = await get_lifecycle_settings(session)
    if not settings.secondary_account_retry_interval_minutes:
        return 0
    now = datetime.now(timezone.utc)
    if settings.last_secondary_account_retry_sweep_at is not None:
        last = settings.last_secondary_account_retry_sweep_at if settings.last_secondary_account_retry_sweep_at.tzinfo else settings.last_secondary_account_retry_sweep_at.replace(tzinfo=timezone.utc)
        if now < last + timedelta(minutes=settings.secondary_account_retry_interval_minutes):
            return 0
    settings.last_secondary_account_retry_sweep_at = now
    await session.commit()

    candidate_ids = list((await session.scalars(
        select(User.id).join(IdentityProvider, User.provider_id == IdentityProvider.id)
        .where(User.account_type == "NORMAL", User.status == "ACTIVE", IdentityProvider.type != "CSV")
    )).all())
    provisioned = 0
    for user_id in candidate_ids:
        person = await session.get(User, user_id)
        if person is None:
            continue
        created = await provision_secondary_accounts(session, person, department=person.department, job_title=person.job_title, request_id=f"secondary-account-sweep-{user_id}")
        if created:
            await session.commit()
        provisioned += len(created)
    return provisioned
