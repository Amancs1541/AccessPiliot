from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AccessPilotError
from app.models import IdentityAccount, IdentityProvider, User
from app.providers.base import NewUserRequest, ProviderConflictError
from app.providers.graph_client import GraphError
from app.schemas.accounts import AccountActionResult, IdentityAccountResponse, PersonAccountsActionResponse
from app.services.assignments import _resolve_internal_user_id
from app.services.audit import record_audit
from app.services.provider_configuration import _connector
from app.services.provisioning import username_for_provider


async def _get_user(session: AsyncSession, user_id: UUID) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise AccessPilotError("USER_NOT_FOUND", "The user was not found.", 404)
    return user


async def ensure_primary_account(session: AsyncSession, user: User) -> Optional[IdentityAccount]:
    """Mirrors the person's own (provider, external_id) as their PRIMARY account row — created lazily the first
    time it is needed (the 0056 migration backfilled existing users, this covers anyone created since) and kept in
    step with the User row's status/email, which directory sync owns. CSV bookkeeping identities have no real
    directory account and get none."""
    provider = await session.get(IdentityProvider, user.provider_id)
    if provider is None or provider.type == "CSV":
        return None
    account = (await session.scalars(select(IdentityAccount).where(IdentityAccount.provider_id == user.provider_id, IdentityAccount.external_id == user.external_id))).first()
    status = user.status if user.status in ("ACTIVE", "DISABLED") else "ACTIVE"
    if account is None:
        account = IdentityAccount(user_id=user.id, provider_id=user.provider_id, external_id=user.external_id, username=user.email, status=status, provisioned_by="SYNC")
        session.add(account)
    elif account.status != "DELETED":  # a deleted account never comes back to life through a sync mirror
        account.user_id, account.username, account.status = user.id, user.email, status
    await session.flush()
    return account


def _is_primary(user: User, account: IdentityAccount) -> bool:
    return account.provider_id == user.provider_id and account.external_id == user.external_id


async def ensure_account_in_provider(session: AsyncSession, user: User, provider_id: UUID, actor_subject: str, request_id: str) -> IdentityAccount:
    """Returns the person's account in `provider_id`, auto-provisioning a real one there on the spot if they don't
    have one yet. This is what makes a grant land correctly when the resource being granted (a Group/Role/
    Application) lives in a DIFFERENT directory than the person's primary account — e.g. an AD-sourced group
    assigned to someone whose primary account is Entra. Without this, the grant would be attempted against the
    wrong directory using the wrong external_id (see app.services.assignments, which calls this before every real
    grant). Mirrors the (provider_id match -> else linked IdentityAccount) lookup
    app.services.joiner._manager_external_id already does, but creates on miss instead of giving up."""
    if user.provider_id == provider_id:
        return await ensure_primary_account(session, user)
    account = (await session.scalars(select(IdentityAccount).where(IdentityAccount.user_id == user.id, IdentityAccount.provider_id == provider_id))).first()
    if account is not None:
        return account
    provider = await session.get(IdentityProvider, provider_id)
    if provider is None or provider.type == "CSV":
        raise AccessPilotError("PROVIDER_NOT_FOUND", "This directory has no real connector behind it.", 404)
    if not provider.provision_joiners:
        raise AccessPilotError("PROVIDER_PROVISIONING_DISABLED", f"{provider.name} is switched off for new-account provisioning (Providers page), so access there can't be auto-created for {user.display_name}.", 422)
    username = username_for_provider(provider, user.given_name, user.surname, user.email)
    try:
        created = await _connector(provider).create_user(NewUserRequest(display_name=user.display_name, user_principal_name=username, mail_nickname=username.split("@")[0] or username, department=user.department, job_title=user.job_title, given_name=user.given_name, surname=user.surname, employee_id=user.employee_id, enabled=True))
    except ProviderConflictError as exc:
        raise AccessPilotError("ACCOUNT_USERNAME_TAKEN", f"An account with the username {username} already exists in {provider.name} ({exc}).", 409) from exc
    except GraphError as exc:
        raise AccessPilotError(exc.code, exc.message, exc.status_code) from exc
    account = IdentityAccount(user_id=user.id, provider_id=provider.id, external_id=created.user.external_id, username=username, status="ACTIVE", provisioned_by="ASSIGNMENT")
    session.add(account)
    await session.flush()
    actor_id = await _resolve_internal_user_id(session, actor_subject)
    await record_audit(session, action="ACCOUNT_AUTO_PROVISIONED", target_type="USER", target_id=user.id, provider_id=provider.id, actor_user_id=actor_id, request_id=request_id, metadata={"provider": provider.name, "username": username, "reason": "assignment_activation"})
    return account


async def _account_responses(session: AsyncSession, user: User) -> list[IdentityAccountResponse]:
    accounts = list((await session.scalars(select(IdentityAccount).where(IdentityAccount.user_id == user.id).order_by(IdentityAccount.created_at))).all())
    responses: list[IdentityAccountResponse] = []
    for account in accounts:
        provider = await session.get(IdentityProvider, account.provider_id)
        responses.append(IdentityAccountResponse(
            id=account.id, provider_id=account.provider_id, provider_name=provider.name if provider else "Unknown", provider_type=provider.type if provider else "UNKNOWN",
            external_id=account.external_id, username=account.username, status=account.status, provisioned_by=account.provisioned_by, is_primary=_is_primary(user, account), created_at=account.created_at,
        ))
    responses.sort(key=lambda r: (not r.is_primary, r.provider_name))
    return responses


async def list_accounts(session: AsyncSession, user_id: UUID) -> list[IdentityAccountResponse]:
    user = await _get_user(session, user_id)
    await ensure_primary_account(session, user)
    await session.commit()
    return await _account_responses(session, user)


async def set_account_enabled(session: AsyncSession, account_id: UUID, enabled: bool, actor_subject: str, request_id: str) -> IdentityAccount:
    """Real, consequential: flips this ONE account's accountEnabled in its own directory (Entra/Okta) — never just a
    local flag. If it is the person's primary account the User row's status follows."""
    account = await session.get(IdentityAccount, account_id)
    if account is None:
        raise AccessPilotError("ACCOUNT_NOT_FOUND", "The account was not found.", 404)
    user = await _get_user(session, account.user_id)
    provider = await session.get(IdentityProvider, account.provider_id)
    if provider is None or provider.type == "CSV":
        raise AccessPilotError("PROVIDER_NOT_FOUND", "This account has no real directory behind it.", 404)
    try:
        await _connector(provider).set_user_enabled(account.external_id, enabled)
    except GraphError as exc:
        raise AccessPilotError(exc.code, exc.message, exc.status_code) from exc
    account.status = "ACTIVE" if enabled else "DISABLED"
    if _is_primary(user, account):
        user.status = account.status
    actor_id = await _resolve_internal_user_id(session, actor_subject)
    await record_audit(session, action="ACCOUNT_ENABLED" if enabled else "ACCOUNT_DISABLED", target_type="USER", target_id=user.id, provider_id=provider.id, actor_user_id=actor_id, request_id=request_id, metadata={"provider": provider.name, "account": account.username or account.external_id})
    await session.commit()
    await session.refresh(account)
    return account


async def set_person_enabled(session: AsyncSession, user_id: UUID, enabled: bool, actor_subject: str, request_id: str, *, force: bool = False, skip_provider_ids: Optional[set] = None) -> PersonAccountsActionResponse:
    """Disable (or re-enable) the person's account in EVERY connected IdP. Each IdP is attempted independently and
    reported separately — one directory being down never blocks the others, and an account already in the target
    state is skipped. Accounts only: this does not revoke AccessPilot access (that is the leaver process)."""
    user = await _get_user(session, user_id)
    actor_id = await _resolve_internal_user_id(session, actor_subject)
    if not enabled and actor_id is not None and actor_id == user.id:
        raise AccessPilotError("VALIDATION_ERROR", "You cannot disable your own account.", 400)
    await ensure_primary_account(session, user)
    await session.commit()
    target = "ACTIVE" if enabled else "DISABLED"
    # Plain snapshots so nothing below depends on ORM state that another commit may have refreshed.
    person_id = user.id
    snapshot: list[tuple[UUID, str, str]] = []
    for account in (await session.scalars(select(IdentityAccount).where(IdentityAccount.user_id == person_id).order_by(IdentityAccount.created_at))).all():
        if skip_provider_ids and account.provider_id in skip_provider_ids:
            continue  # e.g. the directory that just reported this person disabled — nothing to do there
        provider = await session.get(IdentityProvider, account.provider_id)
        snapshot.append((account.id, account.status, provider.name if provider else "Unknown"))
    results: list[AccountActionResult] = []
    for account_id, status, name in snapshot:
        if status == target and not force:  # force: AccessPilot's own status can run ahead of the directory (e.g. a CSV termination)
            results.append(AccountActionResult(account_id=account_id, provider_name=name, ok=True, already=True))
            continue
        try:
            await set_account_enabled(session, account_id, enabled, actor_subject, request_id)
            results.append(AccountActionResult(account_id=account_id, provider_name=name, ok=True))
        except AccessPilotError as exc:
            # No rollback: set_account_enabled raises before it changes anything, and a rollback here would also throw
            # away the CALLER's uncommitted work (the CSV import commit, the leaver runner) and expire its objects.
            results.append(AccountActionResult(account_id=account_id, provider_name=name, ok=False, error=exc.message))
    await record_audit(session, action="ACCOUNTS_ENABLED_ALL" if enabled else "ACCOUNTS_DISABLED_ALL", target_type="USER", target_id=person_id, actor_user_id=actor_id, request_id=request_id, metadata={"results": [{"provider": r.provider_name, "ok": r.ok, "already": r.already, "error": r.error} for r in results]})
    await session.commit()
    user = await _get_user(session, user_id)
    return PersonAccountsActionResponse(user_status=user.status, results=results, accounts=await _account_responses(session, user))
