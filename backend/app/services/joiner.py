from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AccessPilotError
from app.models import IdentityAccount, IdentityProvider, JoinerRequest, User
from app.providers.base import NewUserRequest, ProviderConflictError
from app.providers.graph_client import GraphError
from app.schemas.joiner import JoinerAccountResult, JoinerCreate, JoinerResponse, JoinerTargetResponse
from app.services.accounts import ensure_primary_account
from app.services.assignments import _resolve_internal_user_id
from app.services.audit import record_audit
from app.services.directory_sync import upsert_user
from app.services.notifications import create_notification
from app.services.provider_configuration import _connector
from app.services.provisioning import build_username_local_part

logger = logging.getLogger("accesspilot.joiner")

IMMEDIATE_SLACK = timedelta(minutes=1)  # a start time within a minute of now (or in the past) means "start now"


# ---------------------------------------------------------------- IdP targets

async def _real_providers(session: AsyncSession) -> list[IdentityProvider]:
    providers = list((await session.scalars(select(IdentityProvider).where(IdentityProvider.type != "CSV").order_by(IdentityProvider.name))).all())
    return sorted(providers, key=lambda p: (p.type != "ENTRA", p.name))  # Entra first: it becomes the person's primary account


async def list_targets(session: AsyncSession) -> list[JoinerTargetResponse]:
    return [JoinerTargetResponse(provider_id=p.id, name=p.name, provider_type=p.type, status=p.status, provision_joiners=p.provision_joiners, provisioning_domain=p.provisioning_domain, username_convention=p.username_convention) for p in await _real_providers(session)]


async def set_target_enabled(session: AsyncSession, provider_id: UUID, enabled: bool, request_id: str) -> list[JoinerTargetResponse]:
    provider = await session.get(IdentityProvider, provider_id)
    if provider is None or provider.type == "CSV":
        raise AccessPilotError("PROVIDER_NOT_FOUND", "That directory was not found.", 404)
    provider.provision_joiners = enabled
    await record_audit(session, action="JOINER_TARGET_UPDATED", target_type="PROVIDER", target_id=provider.id, provider_id=provider.id, request_id=request_id, metadata={"provision_joiners": enabled})
    await session.commit()
    return await list_targets(session)


def _username_for(provider: IdentityProvider, first: str, last: str, work_email: str, override: Optional[str]) -> str:
    """The joiner's username in one IdP: an explicit override; else, when the IdP has a provisioning domain, the
    naming-convention local part at that domain (same engine CSV onboarding uses); else the work email as given."""
    if override and override.strip():
        return override.strip()
    # The provider's provisioning policy (Providers page) decides the account name at creation time: its domain if
    # set, else the work email's own domain; its naming convention if set, else the work email's local part.
    if provider.provisioning_domain or provider.username_convention:
        domain = provider.provisioning_domain or (work_email.split("@", 1)[1] if "@" in work_email else "")
        local = build_username_local_part(provider.username_convention, first, last, work_email)
        return f"{local}@{domain}" if domain else local
    return work_email


# ---------------------------------------------------------------- creation

def _to_response(joiner: JoinerRequest, passwords: Optional[dict] = None) -> JoinerResponse:
    passwords = passwords or {}
    targets = [JoinerAccountResult(provider_id=UUID(t["provider_id"]), provider_name=t["provider_name"], username=t["username"], status=t["status"], error=t.get("error"), temporary_password=passwords.get(t["provider_id"])) for t in (joiner.targets or [])]
    return JoinerResponse(id=joiner.id, user_id=joiner.user_id, display_name=f"{joiner.first_name} {joiner.last_name}", work_email=joiner.work_email, employee_id=joiner.employee_id, department=joiner.department, job_title=joiner.job_title, start_at=joiner.start_at, leaver_date=joiner.leaver_date, status=joiner.status, targets=targets, created_at=joiner.created_at, activated_at=joiner.activated_at)


async def _create_accounts(session: AsyncSession, joiner: JoinerRequest, wanted: list[tuple[IdentityProvider, str]], *, enabled: bool) -> dict:
    """Creates the joiner's account in each given IdP (disabled unless `enabled`), recording per-IdP success or
    failure on joiner.targets and the person's IdentityAccount rows. The FIRST success creates the person's User row
    (that account becomes the primary one). Returns {provider_id: temporary_password} — shown once, never stored."""
    display_name = f"{joiner.first_name} {joiner.last_name}"
    passwords: dict = {}
    targets = [t for t in (joiner.targets or []) if t["status"] != "FAILED" or t["provider_id"] not in {str(p.id) for p, _ in wanted}]
    for provider, username in wanted:
        entry = {"provider_id": str(provider.id), "provider_name": provider.name, "username": username, "status": "FAILED", "error": None, "external_id": None, "account_id": None}
        try:
            created = await _connector(provider).create_user(NewUserRequest(display_name=display_name, user_principal_name=username, mail_nickname=username.split("@")[0] or username, department=joiner.department, job_title=joiner.job_title, given_name=joiner.first_name, surname=joiner.last_name, employee_id=joiner.employee_id, enabled=enabled))
        except ProviderConflictError as exc:
            entry["error"] = f"An account with this username already exists ({exc})"
        except GraphError as exc:
            entry["error"] = f"{exc.code}: {exc.message}"
        else:
            person = await session.get(User, joiner.user_id) if joiner.user_id else None
            if person is None:
                person, _ = await upsert_user(session, provider.id, created.user)
                person.given_name, person.surname = joiner.first_name, joiner.last_name
                person.employee_id, person.employee_category, person.employment_type = joiner.employee_id, joiner.employee_category, joiner.employment_type
                person.manager_id, person.leaver_date, person.start_date = joiner.manager_id, joiner.leaver_date, joiner.start_at.date()
                person.source, person.status = "JOINER", ("ACTIVE" if enabled else "DISABLED")
                await session.flush()
                joiner.user_id = person.id
                account = await ensure_primary_account(session, person)
                account.provisioned_by = "JOINER"
            else:
                account = IdentityAccount(user_id=person.id, provider_id=provider.id, external_id=created.user.external_id, username=username, status="ACTIVE" if enabled else "DISABLED", provisioned_by="JOINER")
                session.add(account)
                await session.flush()
            account.username, account.status = username, ("ACTIVE" if enabled else "DISABLED")
            entry.update(status="ENABLED" if enabled else "CREATED", external_id=created.user.external_id, account_id=str(account.id))
            if created.temporary_password:
                passwords[str(provider.id)] = created.temporary_password
        targets = [t for t in targets if t["provider_id"] != entry["provider_id"]] + [entry]
    joiner.targets = targets
    return passwords


async def create_joiner(session: AsyncSession, data: JoinerCreate, actor_subject: str, request_id: str) -> tuple[JoinerResponse, dict]:
    """Submits a joiner: validates, creates the account in every chosen IdP (DISABLED), creates the person, and — if
    the start time is now or past — activates immediately; otherwise a worker activates at start_at. Returns the
    response with each account's one-time temporary password."""
    if data.employee_id and (await session.scalars(select(User.id).where(User.employee_id == data.employee_id))).first() is not None:
        raise AccessPilotError("EMPLOYEE_ID_TAKEN", "Someone with this employee ID already exists.", 409)
    if await session.get(User, data.manager_id) is None:
        raise AccessPilotError("USER_NOT_FOUND", "The selected manager was not found.", 404)
    providers = {p.id: p for p in await _real_providers(session)}
    wanted: list[tuple[IdentityProvider, str]] = []
    for target in data.targets:
        provider = providers.get(target.provider_id)
        if provider is None:
            raise AccessPilotError("PROVIDER_NOT_FOUND", "One of the selected directories was not found (or has no real directory behind it).", 404)
        if not provider.provision_joiners:
            raise AccessPilotError("VALIDATION_ERROR", f"{provider.name} is switched off for joiner accounts.", 422)
        wanted.append((provider, _username_for(provider, data.first_name.strip(), data.last_name.strip(), data.work_email, target.username)))
    wanted.sort(key=lambda pair: (pair[0].type != "ENTRA", pair[0].name))

    start_at = data.start_at if data.start_at.tzinfo else data.start_at.replace(tzinfo=timezone.utc)
    actor_id = await _resolve_internal_user_id(session, actor_subject)
    joiner = JoinerRequest(first_name=data.first_name.strip(), last_name=data.last_name.strip(), work_email=data.work_email, employee_id=data.employee_id, department=data.department.strip(), job_title=(data.job_title or "").strip() or None, manager_id=data.manager_id, employee_category=data.employee_category, employment_type=data.employment_type, start_at=start_at, leaver_date=data.leaver_date, status="SCHEDULED", targets=[], created_by=actor_id)
    session.add(joiner)
    await session.flush()
    passwords = await _create_accounts(session, joiner, wanted, enabled=False)
    if joiner.user_id is None:
        errors = "; ".join(f"{t['provider_name']}: {t['error']}" for t in joiner.targets)
        await session.rollback()
        raise AccessPilotError("JOINER_PROVISIONING_FAILED", f"No account could be created in any selected directory. {errors}", 502)
    await _push_manager(session, joiner)
    # Audit/FYI only — the manager isn't approving anything, just being told a joiner now reports to them. Fires
    # at submission (not only on activation, which activate_joiner's own LIFECYCLE_JOINER notification already
    # covers separately) so a future-dated joiner's manager hears about it right away, not only on the start date.
    await create_notification(session, joiner.manager_id, "JOINER_SUBMITTED", f"{joiner.first_name} {joiner.last_name} has been submitted as a joiner, starting {start_at.strftime('%Y-%m-%d')} — you are their manager.", link="/admin/joiners")
    await record_audit(session, action="JOINER_CREATED", target_type="USER", target_id=joiner.user_id, actor_user_id=actor_id, request_id=request_id, metadata={"start_at": start_at.isoformat(), "targets": [{"provider": t["provider_name"], "status": t["status"]} for t in joiner.targets]})
    await session.commit()
    if start_at <= datetime.now(timezone.utc) + IMMEDIATE_SLACK:
        await activate_joiner(session, joiner.id, request_id)
    await session.refresh(joiner)
    return _to_response(joiner, passwords), passwords


async def _manager_external_id(session: AsyncSession, manager_id: UUID, provider_id: UUID) -> Optional[str]:
    """The manager's account id in a given directory: their own row if that is where they live, else a linked account."""
    manager = await session.get(User, manager_id)
    if manager is None:
        return None
    if manager.provider_id == provider_id:
        return manager.external_id
    account = (await session.scalars(select(IdentityAccount).where(IdentityAccount.user_id == manager_id, IdentityAccount.provider_id == provider_id))).first()
    return account.external_id if account else None


async def _push_manager(session: AsyncSession, joiner: JoinerRequest) -> None:
    """Best effort: set the directory-side manager on each created account. A directory that cannot do it, or a
    manager with no account there, is skipped — the AccessPilot manager link is what matters and is already saved."""
    if joiner.manager_id is None:
        return
    targets = [dict(t) for t in (joiner.targets or [])]
    for entry in targets:
        if entry.get("status") != "CREATED":
            continue
        provider = await session.get(IdentityProvider, UUID(entry["provider_id"]))
        manager_ext = await _manager_external_id(session, joiner.manager_id, provider.id)
        if manager_ext is None:
            entry["manager_pushed"] = False
            continue
        try:
            await _connector(provider).set_user_manager(entry["external_id"], manager_ext)
            entry["manager_pushed"] = True
        except (NotImplementedError, GraphError) as exc:
            entry["manager_pushed"] = False
            logger.warning("Manager not pushed to %s: %s", provider.name, exc)
    joiner.targets = targets


# ---------------------------------------------------------------- activation (start date)

async def activate_joiner(session: AsyncSession, joiner_id: UUID, request_id: str) -> None:
    """Start date reached: enable every created account, mark the person ACTIVE, grant their birthright access
    (eligible, as for any joiner), record the JOINER event and tell the manager / lifecycle owners."""
    from app.services.birthright import evaluate_birthright_policies
    from app.services.lifecycle import _notify_lifecycle, get_lifecycle_settings, record_joiner

    joiner = await session.get(JoinerRequest, joiner_id)
    if joiner is None or joiner.status not in ("SCHEDULED", "PARTIAL") or joiner.user_id is None:
        return
    person_id, name = joiner.user_id, f"{joiner.first_name} {joiner.last_name}"
    targets = [dict(t) for t in (joiner.targets or [])]
    for entry in targets:
        if entry["status"] != "CREATED":
            continue
        provider = await session.get(IdentityProvider, UUID(entry["provider_id"]))
        try:
            await _connector(provider).set_user_enabled(entry["external_id"], True)
        except GraphError as exc:
            entry["error"] = f"Could not enable: {exc.code}: {exc.message}"
            continue
        entry["status"], entry["error"] = "ENABLED", None
        account = await session.get(IdentityAccount, UUID(entry["account_id"])) if entry.get("account_id") else None
        if account is not None:
            account.status = "ACTIVE"
    joiner.targets = targets
    any_enabled = any(t["status"] == "ENABLED" for t in targets)
    all_ok = all(t["status"] == "ENABLED" for t in targets)
    person = await session.get(User, person_id)
    if any_enabled:
        person.status = "ACTIVE"
    joiner.status = "ACTIVE" if all_ok else "PARTIAL"
    joiner.activated_at = datetime.now(timezone.utc) if any_enabled else joiner.activated_at
    await session.commit()
    if not any_enabled:
        return
    outcome = {"granted": await evaluate_birthright_policies(session, person_id, "system:lifecycle", request_id)}
    await record_joiner(session, person_id, "JOINER", outcome, request_id)
    person = await session.get(User, person_id)
    settings = await get_lifecycle_settings(session)
    await _notify_lifecycle(session, person, settings, "LIFECYCLE_JOINER", f"{name} has started ({person.department or 'no department'}): {len([t for t in targets if t['status'] == 'ENABLED'])} account(s) enabled, {len(outcome['granted'])} access item(s) made eligible.")
    await session.commit()


async def sweep_joiners(session: AsyncSession) -> int:
    """Worker entry point: activates every SCHEDULED joiner whose start time has arrived."""
    now = datetime.now(timezone.utc)
    due_ids = list((await session.scalars(select(JoinerRequest.id).where(JoinerRequest.status == "SCHEDULED", JoinerRequest.start_at <= now).order_by(JoinerRequest.start_at))).all())
    activated = 0
    for joiner_id in due_ids:
        try:
            await activate_joiner(session, joiner_id, f"joiner-{joiner_id}")
            activated += 1
        except AccessPilotError as exc:
            logger.warning("Joiner activation failed for %s: %s", joiner_id, exc)
            await session.rollback()
    return activated


# ---------------------------------------------------------------- retry / cancel / list

async def retry_joiner(session: AsyncSession, joiner_id: UUID, request_id: str) -> tuple[JoinerResponse, dict]:
    """Creates the accounts that failed the first time. If the start date has already passed the new accounts are
    enabled straight away."""
    joiner = await session.get(JoinerRequest, joiner_id)
    if joiner is None:
        raise AccessPilotError("JOINER_NOT_FOUND", "The joiner was not found.", 404)
    if joiner.status == "CANCELLED":
        raise AccessPilotError("REQUEST_ALREADY_PROCESSED", "This joiner was cancelled.", 409)
    failed = [t for t in (joiner.targets or []) if t["status"] == "FAILED"]
    if not failed:
        raise AccessPilotError("VALIDATION_ERROR", "Nothing failed for this joiner.", 422)
    providers = {str(p.id): p for p in await _real_providers(session)}
    wanted = [(providers[t["provider_id"]], t["username"]) for t in failed if t["provider_id"] in providers]
    start_at = joiner.start_at if joiner.start_at.tzinfo else joiner.start_at.replace(tzinfo=timezone.utc)  # SQLite hands back naive values
    started = joiner.status in ("ACTIVE", "PARTIAL") or start_at <= datetime.now(timezone.utc) + IMMEDIATE_SLACK
    passwords = await _create_accounts(session, joiner, wanted, enabled=False)  # activate_joiner enables them once the start has passed
    await session.commit()
    if started:
        if joiner.status == "SCHEDULED":
            joiner.status = "PARTIAL"
        await activate_joiner(session, joiner.id, request_id)
    await session.refresh(joiner)
    return _to_response(joiner, passwords), passwords


async def cancel_joiner(session: AsyncSession, joiner_id: UUID, actor_subject: str, request_id: str, *, delete_accounts: bool = False) -> JoinerResponse:
    joiner = await session.get(JoinerRequest, joiner_id)
    if joiner is None:
        raise AccessPilotError("JOINER_NOT_FOUND", "The joiner was not found.", 404)
    if joiner.status != "SCHEDULED":
        raise AccessPilotError("REQUEST_ALREADY_PROCESSED", "Only a joiner that has not started yet can be cancelled.", 409)
    joiner.status = "CANCELLED"
    deleted: list[str] = []
    if delete_accounts:
        # Irreversible in the directory (Entra keeps a deleted user recoverable for 30 days). Per-IdP, no rollback.
        targets = [dict(t) for t in (joiner.targets or [])]
        for entry in targets:
            if entry.get("status") not in ("CREATED", "ENABLED") or not entry.get("external_id"):
                continue
            provider = await session.get(IdentityProvider, UUID(entry["provider_id"]))
            try:
                await _connector(provider).delete_user(entry["external_id"])
            except (NotImplementedError, GraphError) as exc:
                entry["error"] = f"Could not delete: {getattr(exc, 'message', exc)}"
                continue
            entry["status"], entry["error"] = "DELETED", None
            deleted.append(provider.name)
            account = await session.get(IdentityAccount, UUID(entry["account_id"])) if entry.get("account_id") else None
            if account is not None:
                account.status = "DELETED"
        joiner.targets = targets
    await record_audit(session, action="JOINER_CANCELLED", target_type="USER", target_id=joiner.user_id, actor_user_id=await _resolve_internal_user_id(session, actor_subject), request_id=request_id, metadata={"note": "accounts deleted in: " + ", ".join(deleted) if delete_accounts else "accounts remain (disabled) in the directories", "deleted_in": deleted})
    await session.commit()
    await session.refresh(joiner)
    return _to_response(joiner)


async def list_joiners(session: AsyncSession) -> list[JoinerResponse]:
    return [_to_response(j) for j in (await session.scalars(select(JoinerRequest).order_by(JoinerRequest.start_at.desc()))).all()]
