from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AccessPilotError
from app.models import AccessAssignment, AuditLog, IdentityProvider, PrivilegedAccountPolicy, PrivilegedAccountRequest, User
from app.providers.base import NewUserRequest, ProviderConflictError
from app.providers.entra import EntraProvider
from app.providers.graph_client import GraphError
from app.services.assignments import _resolve_internal_user_id
from app.services.audit import record_audit
from app.services.audit_read import _hydrate_entry
from app.services.directory_sync import upsert_user
from app.services.provider_configuration import _connector
from app.services.provisioning import build_username_local_part, primary_identity_provider

logger = logging.getLogger("accesspilot.privileged_accounts")

VALID_ACCOUNT_TYPES = ("PU", "TU")


async def get_or_create_policy(session: AsyncSession, account_type: str) -> PrivilegedAccountPolicy:
    """Singleton-per-account-type — same get-or-create-on-first-read convention SecuritySettings uses for its
    one row, just keyed by account_type instead of being a true singleton. A freshly-created row has no default
    approver, i.e. 'auto-provision immediately' — every deployment starts unaffected until an Admin opts into
    requiring approval."""
    policy = (await session.execute(select(PrivilegedAccountPolicy).where(PrivilegedAccountPolicy.account_type == account_type))).scalar_one_or_none()
    if policy is None:
        policy = PrivilegedAccountPolicy(account_type=account_type)
        session.add(policy)
        await session.commit()
        await session.refresh(policy)
    return policy


async def update_policy(session: AsyncSession, account_type: str, default_approver_id: Optional[UUID], request_id: str) -> PrivilegedAccountPolicy:
    policy = await get_or_create_policy(session, account_type)
    policy.default_approver_id = default_approver_id
    await record_audit(session, action="PRIVILEGED_ACCOUNT_POLICY_UPDATED", target_type="PRIVILEGED_ACCOUNT_POLICY", target_id=policy.id, request_id=request_id, metadata={"account_type": account_type, "default_approver_id": str(default_approver_id) if default_approver_id else None})
    await session.commit()
    await session.refresh(policy)
    return policy


async def _provision_pu_tu_account(session: AsyncSession, real_user: User, account_type: str, request_id: str) -> User:
    """Real account creation via the exact same connector.create_user() + upsert_user() path CSV onboarding's
    provisioning already uses (see app.services.provisioning.provision_real_account) — reusing the already-built
    username-convention engine for the local part, just prefixed PU_/TU_. No mailbox needs no special handling:
    it's the natural result of never assigning an Exchange-enabling license to this account, which AccessPilot
    doesn't do for anyone today."""
    provider = await primary_identity_provider(session)
    if provider is None:
        raise AccessPilotError("PROVIDER_NOT_FOUND", "No identity provider is configured.", 404)
    connector = _connector(provider)
    prefix = "PU_" if account_type == "PU" else "TU_"
    local_part = prefix + build_username_local_part(provider.username_convention, real_user.given_name, real_user.surname, real_user.email)
    domain = provider.provisioning_domain or (real_user.email.split("@")[-1] if "@" in real_user.email else "")
    upn = f"{local_part}@{domain}" if domain else local_part
    # PU_<name>/TU_<name> — the same prefix used for the UPN's local part, applied to the display name too, so
    # the account is recognizable as PU/TU at a glance anywhere its name is shown (user lists, audit entries,
    # Entra itself) rather than only inferable from its email.
    display_name = f"{prefix}{real_user.display_name}"
    try:
        created = await connector.create_user(NewUserRequest(display_name=display_name, user_principal_name=upn, mail_nickname=local_part, department=real_user.department, job_title=None))
    except ProviderConflictError as exc:
        raise AccessPilotError("USER_ALREADY_EXISTS", str(exc), 409) from exc
    except GraphError as exc:
        raise AccessPilotError(exc.code, exc.message, exc.status_code) from exc
    row, _ = await upsert_user(session, provider.id, created.user)
    row.account_type = account_type
    row.linked_user_id = real_user.id
    await session.flush()
    await record_audit(session, action="PRIVILEGED_ACCOUNT_CREATED", target_type="USER", target_id=row.id, provider_id=provider.id, request_id=request_id, metadata={"account_type": account_type, "linked_user_id": str(real_user.id), "upn": upn})
    return row


async def _provision_and_finalize(session: AsyncSession, request_row: PrivilegedAccountRequest, request_id: str) -> None:
    real_user = await session.get(User, request_row.requester_id)
    if real_user is None:
        request_row.status = "FAILED"
        request_row.failure_reason = "The requesting user no longer exists."
    else:
        try:
            provisioned = await _provision_pu_tu_account(session, real_user, request_row.account_type, request_id)
            request_row.status = "PROVISIONED"
            request_row.provisioned_user_id = provisioned.id
        except AccessPilotError as exc:
            request_row.status = "FAILED"
            request_row.failure_reason = exc.message
    request_row.decided_at = datetime.now(timezone.utc)
    await record_audit(session, action="PRIVILEGED_ACCOUNT_PROVISIONED" if request_row.status == "PROVISIONED" else "PRIVILEGED_ACCOUNT_PROVISIONING_FAILED", target_type="PRIVILEGED_ACCOUNT_REQUEST", target_id=request_row.id, request_id=request_id, metadata={"account_type": request_row.account_type, "reason": request_row.failure_reason})
    await session.commit()
    await session.refresh(request_row)


async def create_request(session: AsyncSession, requester_id: UUID, account_type: str, justification: str, request_id: str) -> PrivilegedAccountRequest:
    policy = await get_or_create_policy(session, account_type)
    request_row = PrivilegedAccountRequest(requester_id=requester_id, account_type=account_type, justification=justification, approver_id=policy.default_approver_id, status="PENDING_APPROVAL")
    session.add(request_row)
    await session.flush()
    await record_audit(session, action="PRIVILEGED_ACCOUNT_REQUESTED", target_type="PRIVILEGED_ACCOUNT_REQUEST", target_id=request_row.id, request_id=request_id, actor_user_id=requester_id, metadata={"account_type": account_type})
    await session.commit()
    if policy.default_approver_id is None:
        # No approver configured on this account type's policy — the diagram's "ASAP" branch: provision for
        # real in this same action, no human decision needed.
        await _provision_and_finalize(session, request_row, request_id)
    await session.refresh(request_row)
    return request_row


async def _get_request(session: AsyncSession, request_id_: UUID) -> PrivilegedAccountRequest:
    request_row = await session.get(PrivilegedAccountRequest, request_id_)
    if request_row is None:
        raise AccessPilotError("PRIVILEGED_ACCOUNT_REQUEST_NOT_FOUND", "The request was not found.", 404)
    return request_row


async def _authorize_decision(session: AsyncSession, request_row: PrivilegedAccountRequest, actor_subject: str, actor_roles: tuple[str, ...]) -> Optional[UUID]:
    actor_id = await _resolve_internal_user_id(session, actor_subject)
    if "AccessPilot.Admin" in actor_roles:
        return actor_id
    if actor_id is not None and actor_id == request_row.approver_id:
        return actor_id
    raise AccessPilotError("ACCESS_DENIED", "Only the designated approver or an administrator can decide this request.", 403)


async def approve_request(session: AsyncSession, request_id_: UUID, actor_subject: str, actor_roles: tuple[str, ...], request_id: str) -> PrivilegedAccountRequest:
    request_row = await _get_request(session, request_id_)
    if request_row.status != "PENDING_APPROVAL":
        raise AccessPilotError("REQUEST_ALREADY_PROCESSED", "This request has already been decided.", 409)
    await _authorize_decision(session, request_row, actor_subject, actor_roles)
    await _provision_and_finalize(session, request_row, request_id)
    return request_row


async def reject_request(session: AsyncSession, request_id_: UUID, actor_subject: str, actor_roles: tuple[str, ...], justification: str, request_id: str) -> PrivilegedAccountRequest:
    request_row = await _get_request(session, request_id_)
    if request_row.status != "PENDING_APPROVAL":
        raise AccessPilotError("REQUEST_ALREADY_PROCESSED", "This request has already been decided.", 409)
    actor_id = await _authorize_decision(session, request_row, actor_subject, actor_roles)
    request_row.status = "REJECTED"
    request_row.failure_reason = justification
    request_row.decided_at = datetime.now(timezone.utc)
    await record_audit(session, action="PRIVILEGED_ACCOUNT_REQUEST_REJECTED", target_type="PRIVILEGED_ACCOUNT_REQUEST", target_id=request_row.id, request_id=request_id, actor_user_id=actor_id, metadata={"justification": justification})
    await session.commit()
    await session.refresh(request_row)
    return request_row


async def list_requests(session: AsyncSession, requester_id: Optional[UUID] = None) -> list[PrivilegedAccountRequest]:
    statement = select(PrivilegedAccountRequest).order_by(PrivilegedAccountRequest.created_at.desc())
    if requester_id is not None:
        statement = statement.where(PrivilegedAccountRequest.requester_id == requester_id)
    return list((await session.execute(statement)).scalars().all())


async def list_linked_accounts(session: AsyncSession, user_id: UUID) -> list[User]:
    return list((await session.execute(select(User).where(User.linked_user_id == user_id))).scalars().all())


async def set_account_enabled(session: AsyncSession, user_id: UUID, enabled: bool, actor_subject: str, request_id: str) -> User:
    """Real, consequential: flips the account's own accountEnabled in Entra/Okta — never just a local flag.
    Restricted to PU/TU accounts on purpose; a NORMAL user's enable/disable belongs to the directory sync /
    onboarding leaver flow, not this admin action."""
    user = await session.get(User, user_id)
    if user is None:
        raise AccessPilotError("USER_NOT_FOUND", "The user was not found.", 404)
    if user.account_type == "NORMAL":
        raise AccessPilotError("VALIDATION_ERROR", "Only Privileged/Test accounts can be enabled or disabled here.", 400)
    provider = await session.get(IdentityProvider, user.provider_id)
    if provider is None:
        raise AccessPilotError("PROVIDER_NOT_FOUND", "The provider for this account was not found.", 404)
    try:
        await _connector(provider).set_user_enabled(user.external_id, enabled)
    except GraphError as exc:
        raise AccessPilotError(exc.code, exc.message, exc.status_code) from exc
    user.status = "ACTIVE" if enabled else "DISABLED"
    actor_id = await _resolve_internal_user_id(session, actor_subject)
    await record_audit(session, action="PRIVILEGED_ACCOUNT_ENABLED" if enabled else "PRIVILEGED_ACCOUNT_DISABLED", target_type="USER", target_id=user.id, provider_id=provider.id, request_id=request_id, actor_user_id=actor_id)
    await session.commit()
    await session.refresh(user)
    return user


async def disable_linked_accounts_for_leaver(session: AsyncSession, real_user_id: UUID, actor_subject: str, request_id: str) -> int:
    """Leaver safety: a terminated real user's PU/TU shadow accounts must never survive their departure
    unnoticed. Called from the same CSV-onboarding leaver flow that already disables the real user and revokes
    their assignments (app.services.onboarding). One account's provider failure never blocks the others."""
    linked = await list_linked_accounts(session, real_user_id)
    disabled = 0
    for account in linked:
        if account.status == "DISABLED":
            continue
        try:
            await set_account_enabled(session, account.id, False, actor_subject, request_id)
            disabled += 1
        except AccessPilotError:
            continue
    return disabled


async def _live_sign_in_activity(connector, external_id: str) -> tuple[Optional[str], Optional[str], bool]:
    """Best-effort live read of a PU/TU account's last-sign-in timestamps from Entra — same "on-demand, never
    blocks the caller" convention as get_user_licenses/get_user_app_role_assignments elsewhere in this app.
    Needs AuditLog.Read.All in ADDITION to the already-granted User.Read.All — NOT confirmed granted on this
    tenant as of 2026-09-23 (see reference-entra-permissions memory), so a permission-denied GraphError is
    expected and handled here, not treated as a bug. The third element distinguishes "we don't know" (lookup
    failed/unsupported, e.g. Okta/Mock or a missing permission) from "we know — this account has never signed
    in" (a real None from Entra itself), so the UI never has to guess which one it's showing."""
    if not isinstance(connector, EntraProvider):
        return None, None, False
    try:
        activity = await connector.get_user_sign_in_activity(external_id)
    except GraphError as exc:
        logger.warning("Sign-in activity lookup failed for %s (likely AuditLog.Read.All not granted): %s", external_id, exc)
        return None, None, False
    if activity is None:
        return None, None, True
    return activity.get("last_sign_in_at"), activity.get("last_non_interactive_sign_in_at"), True


async def _account_audit_conditions(session: AsyncSession, account_id: UUID) -> list:
    """Every AuditLog row that is genuinely about this one PU/TU account, spread across three different
    target_types (see the actions this module and set_account_enabled/create_assignment record): USER rows
    target the account directly (creation, enable/disable); PRIVILEGED_ACCOUNT_REQUEST rows target the request
    that provisioned it (found via provisioned_user_id, not stored on the account itself); ASSIGNMENT rows target
    whatever manual grants this account holds. Shared by both the activity summary's count/last-activity and the
    full timeline, so the two can never disagree about what counts."""
    conditions = [and_(AuditLog.target_type == "USER", AuditLog.target_id == account_id)]
    request_ids = list((await session.scalars(select(PrivilegedAccountRequest.id).where(PrivilegedAccountRequest.provisioned_user_id == account_id))).all())
    if request_ids:
        conditions.append(and_(AuditLog.target_type == "PRIVILEGED_ACCOUNT_REQUEST", AuditLog.target_id.in_(request_ids)))
    assignment_ids = list((await session.scalars(select(AccessAssignment.id).where(AccessAssignment.user_id == account_id))).all())
    if assignment_ids:
        conditions.append(and_(AuditLog.target_type == "ASSIGNMENT", AuditLog.target_id.in_(assignment_ids)))
    return conditions


async def get_privileged_account_timeline(session: AsyncSession, account_id: UUID, limit: int = 200) -> list[tuple[AuditLog, dict]]:
    """Every audit-recorded event for this one PU/TU account, newest first — creation, every enable/disable, and
    any manual access grant/activation/revocation, merged into one chronological feed (see
    _account_audit_conditions for what's included). Hydrated the same way every other audit feed in this app is
    (see app.services.audit_read._hydrate_entry, already reused cross-module by services/soc.py)."""
    conditions = await _account_audit_conditions(session, account_id)
    entries = list((await session.scalars(select(AuditLog).where(or_(*conditions)).order_by(AuditLog.timestamp.desc()).limit(limit))).all())
    return [(entry, await _hydrate_entry(session, entry)) for entry in entries]


async def list_privileged_account_activity(session: AsyncSession):
    """One row per PU/TU account (newest-created first) for the Admin monitoring tab: who it's linked to, when it
    was created, its last real Entra sign-in (best-effort — see _live_sign_in_activity), and a count/last-seen of
    everything AccessPilot itself has ever recorded about it. Deliberately not paginated: PU/TU accounts are
    always a small, deliberately-provisioned set, never a bulk-synced population."""
    from app.schemas.privileged_accounts import PrivilegedAccountActivitySummary

    accounts = list((await session.scalars(select(User).where(User.account_type != "NORMAL").order_by(User.created_at.desc()))).all())
    results: list[PrivilegedAccountActivitySummary] = []
    for account in accounts:
        provider = await session.get(IdentityProvider, account.provider_id)
        connector = _connector(provider) if provider else None
        last_sign_in, last_non_interactive, sign_in_available = await _live_sign_in_activity(connector, account.external_id) if connector else (None, None, False)
        linked = await session.get(User, account.linked_user_id) if account.linked_user_id else None
        conditions = await _account_audit_conditions(session, account.id)
        event_count, last_activity_at = (await session.execute(select(func.count(AuditLog.id), func.max(AuditLog.timestamp)).where(or_(*conditions)))).one()
        results.append(PrivilegedAccountActivitySummary(
            id=account.id, display_name=account.display_name, email=account.email, account_type=account.account_type, status=account.status,
            linked_user_id=account.linked_user_id, linked_user_display_name=linked.display_name if linked else None,
            created_at=account.created_at, last_sign_in_at=last_sign_in, last_non_interactive_sign_in_at=last_non_interactive,
            sign_in_data_available=sign_in_available, event_count=event_count or 0, last_activity_at=last_activity_at,
        ))
    return results
