"""What happens AFTER the leaver process: enabling a leaver's accounts again needs a written reason and an approval
(manager, else the lifecycle owners), and the leaver policy can delete their accounts from every IdP N days later."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AccessPilotError
from app.models import IdentityAccount, IdentityProvider, ReenableRequest, User
from app.providers.graph_client import GraphError
from app.schemas.lifecycle import ReenableRequestCreate, ReenableRequestResponse
from app.services.accounts import ensure_primary_account, set_account_enabled, set_person_enabled
from app.services.assignments import _resolve_internal_user_id
from app.services.audit import record_audit
from app.services.notifications import create_notification
from app.services.provider_configuration import _connector

logger = logging.getLogger("accesspilot.leaver_followup")


def is_post_leaver(user: User) -> bool:
    """True while the person is still in the disabled, left state. If they show ACTIVE again -- an approved
    re-enable, or someone re-enabled them directly in the directory (see handle_leaver_reactivated) -- the old
    leaver_processed_at timestamp is stale and no longer blocks a fresh leaver request or an enable."""
    return user.leaver_processed_at is not None and user.status in ("DISABLED", "DELETED")


async def guard_enable(session: AsyncSession, user_id: UUID) -> None:
    """Called by the enable endpoints: after the leaver process the accounts can only come back through an approved request."""
    from app.models import LeaverRequest

    if (await session.scalars(select(LeaverRequest.id).where(LeaverRequest.user_id == user_id, LeaverRequest.status == "PENDING"))).first() is not None:
        raise AccessPilotError("LEAVER_PENDING_APPROVAL", "A leaver request for this person is waiting for the manager's approval. The account stays disabled until it is decided.", 409)
    user = await session.get(User, user_id)
    if user is not None and is_post_leaver(user):
        raise AccessPilotError("LEAVER_REENABLE_APPROVAL_REQUIRED", "This person has left. To enable their account again you need a valid reason and their manager's approval.", 409)


async def _approver_ids(session: AsyncSession, user: User, requester_id: Optional[UUID]) -> list[UUID]:
    from app.services.lifecycle import get_lifecycle_settings

    manager = await session.get(User, user.manager_id) if user.manager_id else None
    if manager is not None and manager.id not in (user.id, requester_id) and manager.status == "ACTIVE":
        return [manager.id]
    settings = await get_lifecycle_settings(session)
    owners = [UUID(str(o)) for o in (settings.lifecycle_owner_ids or [])]
    return [o for o in owners if o not in (user.id, requester_id)]


async def _response(session: AsyncSession, row: ReenableRequest, viewer_id: Optional[UUID] = None, viewer_is_admin: bool = False, accounts_note: Optional[str] = None) -> ReenableRequestResponse:
    person = await session.get(User, row.user_id)
    requester = await session.get(User, row.requested_by) if row.requested_by else None
    decider = await session.get(User, row.decided_by) if row.decided_by else None
    approvers = []
    for raw in row.approver_ids or []:
        approver = await session.get(User, UUID(str(raw)))
        if approver is not None:
            approvers.append(approver.display_name)
    approver_ids = {str(a) for a in (row.approver_ids or [])}
    # The requester is blocked from deciding their own request only when a real, named approver exists (a
    # manager or a lifecycle owner) — see the matching comment in decide()/decide_leaver_request().
    is_requester_blocked = viewer_id is not None and viewer_id == row.requested_by and bool(row.approver_ids)
    can_decide = row.status == "PENDING" and not is_requester_blocked and ((viewer_id is not None and str(viewer_id) in approver_ids) or viewer_is_admin)
    scope_label = "All accounts"
    if row.account_ids:
        names = []
        for raw in row.account_ids:
            account = await session.get(IdentityAccount, UUID(str(raw)))
            provider = await session.get(IdentityProvider, account.provider_id) if account else None
            if provider is not None:
                names.append(provider.name)
        scope_label = " + ".join(names) if names else "Selected account"
    return ReenableRequestResponse(
        id=row.id, user_id=row.user_id, user_display_name=person.display_name if person else None, user_email=person.email if person else None,
        requested_by=row.requested_by, requested_by_name=requester.display_name if requester else None, reason=row.reason, status=row.status, approvers=approvers,
        decided_by_name=decider.display_name if decider else None, decision_note=row.decision_note, decided_at=row.decided_at, created_at=row.created_at,
        can_decide=can_decide, scope_label=scope_label, accounts_note=accounts_note,
    )


async def create_request(session: AsyncSession, user_id: UUID, data: ReenableRequestCreate, actor_subject: str, request_id: str) -> ReenableRequestResponse:
    user = await session.get(User, user_id)
    if user is None:
        raise AccessPilotError("USER_NOT_FOUND", "The user was not found.", 404)
    if not is_post_leaver(user):
        raise AccessPilotError("VALIDATION_ERROR", "This person has not been through the leaver process; enable their account directly.", 422)
    if user.accounts_deleted_at is not None:
        raise AccessPilotError("ACCOUNTS_DELETED", "This person's accounts were deleted from the directories and cannot be enabled again. Create them as a new joiner.", 409)
    if (await session.scalars(select(ReenableRequest.id).where(ReenableRequest.user_id == user_id, ReenableRequest.status == "PENDING"))).first() is not None:
        raise AccessPilotError("REQUEST_ALREADY_EXISTS", "There is already a pending request to enable this person's account.", 409)
    account_ids: Optional[list[str]] = None
    scope_note = "every account"
    if data.account_ids:
        accounts = [await session.get(IdentityAccount, account_id) for account_id in data.account_ids]
        if any(account is None or account.user_id != user_id for account in accounts):
            raise AccessPilotError("ACCOUNT_NOT_FOUND", "One of the selected accounts was not found.", 404)
        account_ids = [str(account_id) for account_id in data.account_ids]
        names = []
        for account in accounts:
            provider = await session.get(IdentityProvider, account.provider_id)
            names.append(provider.name if provider else "an account")
        scope_note = " + ".join(names)
    requester_id = await _resolve_internal_user_id(session, actor_subject)
    approvers = await _approver_ids(session, user, requester_id)
    row = ReenableRequest(user_id=user_id, requested_by=requester_id, reason=data.reason.strip(), status="PENDING", approver_ids=[str(a) for a in approvers], account_ids=account_ids)
    session.add(row)
    await session.flush()
    await record_audit(session, action="LEAVER_REENABLE_REQUESTED", target_type="USER", target_id=user_id, actor_user_id=requester_id, request_id=request_id, metadata={"reason": row.reason, "approvers": [str(a) for a in approvers], "scope": scope_note})
    for approver_id in approvers:
        await create_notification(session, approver_id, "REENABLE_REQUESTED", f"Approval needed: enable {scope_note} for {user.display_name} again (they left). Reason: {row.reason}", "/access-reviews/mine")
    await session.commit()
    await session.refresh(row)
    return await _response(session, row, requester_id, False)


async def list_requests(session: AsyncSession, status: Optional[str], viewer_subject: str, viewer_is_admin: bool, *, only_mine: bool = False) -> list[ReenableRequestResponse]:
    viewer_id = await _resolve_internal_user_id(session, viewer_subject)
    stmt = select(ReenableRequest).order_by(ReenableRequest.created_at.desc())
    if status:
        stmt = stmt.where(ReenableRequest.status == status)
    responses = [await _response(session, row, viewer_id, viewer_is_admin) for row in (await session.scalars(stmt)).all()]
    if only_mine:  # what this viewer can decide right now (their own reports as manager / lifecycle owner duties)
        responses = [r for r in responses if r.can_decide]
    return responses


async def decide(session: AsyncSession, request_row_id: UUID, approve: bool, note: Optional[str], actor_subject: str, viewer_is_admin: bool, request_id: str) -> ReenableRequestResponse:
    row = await session.get(ReenableRequest, request_row_id)
    if row is None:
        raise AccessPilotError("REQUEST_NOT_FOUND", "The request was not found.", 404)
    if row.status != "PENDING":
        raise AccessPilotError("REQUEST_ALREADY_PROCESSED", "This request was already decided.", 409)
    decider_id = await _resolve_internal_user_id(session, actor_subject)
    # Self-approval is blocked only when there's a real, named approver to defer to (a manager or a lifecycle
    # owner) — when nobody could be identified (no manager, no other owner configured) the request already reads
    # as "Admins" in the UI, and whoever started it is an admin themselves (starting one needs
    # IDENTITY_ACCOUNT_MANAGE, an Admin-only permission), so blocking them too would leave it permanently stuck.
    if decider_id is not None and decider_id == row.requested_by and row.approver_ids:
        raise AccessPilotError("ACCESS_DENIED", "You cannot approve your own request.", 403)
    if not viewer_is_admin and (decider_id is None or str(decider_id) not in {str(a) for a in (row.approver_ids or [])}):
        raise AccessPilotError("ACCESS_DENIED", "Only the person's manager (or a lifecycle owner) can decide this request.", 403)
    user = await session.get(User, row.user_id)
    person_id, person_name, requester_id = user.id, user.display_name, row.requested_by
    accounts_note = None
    if approve:
        if user.accounts_deleted_at is not None:
            raise AccessPilotError("ACCOUNTS_DELETED", "This person's accounts were already deleted from the directories.", 409)
        if row.account_ids:
            # Scoped: a single account's own "Enable" click asked for approval, so only that account comes back —
            # the others (if any are still disabled) stay exactly as they are.
            results = []
            for raw in row.account_ids:
                account = await session.get(IdentityAccount, UUID(str(raw)))
                provider = await session.get(IdentityProvider, account.provider_id) if account else None
                name = provider.name if provider else "Unknown"
                if account is None:
                    results.append(f"{name}: FAILED - account no longer exists")
                    continue
                if account.status != "DISABLED":
                    results.append(f"{name}: already enabled")
                    continue
                try:
                    await set_account_enabled(session, account.id, True, actor_subject, request_id)
                    results.append(f"{name}: enabled")
                except AccessPilotError as exc:
                    results.append(f"{name}: FAILED - {exc.message}")
            if results and all("FAILED" in r for r in results):
                raise AccessPilotError("PROVIDER_UNAVAILABLE", "No directory could enable the account: " + "; ".join(results), 502)
            accounts_note = "; ".join(results) or "no directory accounts"
        else:
            outcome = await set_person_enabled(session, person_id, True, actor_subject, request_id, force=True)
            if outcome.results and not any(r.ok for r in outcome.results):
                raise AccessPilotError("PROVIDER_UNAVAILABLE", "No directory could enable the account: " + "; ".join(f"{r.provider_name}: {r.error}" for r in outcome.results), 502)
            accounts_note = "; ".join(f"{r.provider_name}: {'enabled' if r.ok else 'FAILED - ' + str(r.error)}" for r in outcome.results) or "no directory accounts"
        user = await session.get(User, person_id)
        # Only reset the leaver cycle once EVERY account is back — a scoped (single-account) approval that leaves
        # another account still disabled means this person has not really "returned" yet.
        still_disabled = (await session.scalars(select(IdentityAccount.id).where(IdentityAccount.user_id == person_id, IdentityAccount.status == "DISABLED"))).first()
        if still_disabled is None:
            user.leaver_processed_at, user.leaver_date, user.leaver_reminders_sent, user.accounts_delete_at = None, None, [], None
    row = await session.get(ReenableRequest, request_row_id)
    row.status, row.decided_by, row.decision_note, row.decided_at = ("APPROVED" if approve else "REJECTED"), decider_id, (note or "").strip() or None, datetime.now(timezone.utc)
    await record_audit(session, action="LEAVER_REENABLE_APPROVED" if approve else "LEAVER_REENABLE_REJECTED", target_type="USER", target_id=person_id, actor_user_id=decider_id, request_id=request_id, metadata={"note": row.decision_note, "accounts": accounts_note})
    if requester_id is not None:
        verdict = "approved. The account is enabled again (access is not restored; it comes back through birthright policies or new requests)" if approve else "rejected"
        await create_notification(session, requester_id, "REENABLE_DECIDED", f"Your request to enable {person_name}'s account was {verdict}." + (f" Note: {row.decision_note}" if row.decision_note else ""), "/admin/movers")
    await session.commit()
    await session.refresh(row)
    return await _response(session, row, decider_id, viewer_is_admin, accounts_note)


# ---------------------------------------------------------------- automatic account deletion

async def sweep_account_deletions(session: AsyncSession) -> int:
    """Worker entry point: deletes the accounts of every leaver whose policy's delete_after_days has elapsed, in every
    IdP (each attempted independently; a failed one is retried on the next tick). The person's row is kept, labelled
    DELETED, and an audit entry records who/what was deleted. Skipped while a re-enable request is pending."""
    from app.services.lifecycle import _notify_lifecycle, get_lifecycle_settings

    now = datetime.now(timezone.utc)
    due_ids = list((await session.scalars(select(User.id).where(User.accounts_delete_at.is_not(None), User.accounts_delete_at <= now, User.accounts_deleted_at.is_(None), User.leaver_processed_at.is_not(None)))).all())
    completed = 0
    for user_id in due_ids:
        if (await session.scalars(select(ReenableRequest.id).where(ReenableRequest.user_id == user_id, ReenableRequest.status == "PENDING"))).first() is not None:
            continue
        user = await session.get(User, user_id)
        if user.status != "DISABLED":
            # Reactivated since the leaver process ran (approved re-enable, or a direct change in the directory) --
            # never delete a currently-active person's accounts. Cancel the scheduled deletion.
            user.accounts_delete_at = None
            await session.commit()
            continue
        await ensure_primary_account(session, user)
        await session.commit()
        snapshot = [(a.id, a.provider_id, a.external_id, a.status) for a in (await session.scalars(select(IdentityAccount).where(IdentityAccount.user_id == user_id))).all()]
        results: list[dict] = []
        for account_id, provider_id, external_id, status in snapshot:
            provider = await session.get(IdentityProvider, provider_id)
            name = provider.name if provider else "Unknown"
            if status == "DELETED":
                results.append({"provider": name, "ok": True, "already": True})
                continue
            try:
                await _connector(provider).delete_user(external_id)
            except (NotImplementedError, GraphError) as exc:
                results.append({"provider": name, "ok": False, "error": getattr(exc, "message", str(exc))})
                continue
            account = await session.get(IdentityAccount, account_id)
            account.status = "DELETED"
            results.append({"provider": name, "ok": True})
        await session.commit()
        if any(not r["ok"] for r in results):
            logger.warning("Account deletion incomplete for %s: %s", user_id, results)
            continue
        user = await session.get(User, user_id)
        record = {"display_name": user.display_name, "email": user.email, "employee_id": user.employee_id, "department": user.department, "job_title": user.job_title, "leaver_processed_at": user.leaver_processed_at.isoformat() if user.leaver_processed_at else None}
        user.accounts_deleted_at, user.status = now, "DELETED"
        await record_audit(session, action="LEAVER_ACCOUNTS_DELETED", target_type="USER", target_id=user_id, request_id=f"account-deletion-{user_id}", metadata={"person": record, "accounts": results})
        settings = await get_lifecycle_settings(session)
        await _notify_lifecycle(session, user, settings, "LIFECYCLE_ACCOUNTS_DELETED", f"{record['display_name']}'s accounts were deleted from {', '.join(r['provider'] for r in results) or 'no directory'} (leaver policy retention ended). The record is kept as Deleted for audit.")
        await session.commit()
        completed += 1
    return completed


# ---------------------------------------------------------------- manual leaver: justification -> disable -> approval

async def _leaver_response(session: AsyncSession, row, viewer_id: Optional[UUID] = None, viewer_is_admin: bool = False, accounts_note: Optional[str] = None):
    from app.schemas.lifecycle import LeaverRequestResponse

    person = await session.get(User, row.user_id)
    requester = await session.get(User, row.requested_by) if row.requested_by else None
    decider = await session.get(User, row.decided_by) if row.decided_by else None
    approvers = [a.display_name for a in [await session.get(User, UUID(str(x))) for x in (row.approver_ids or [])] if a is not None]
    approver_ids = {str(a) for a in (row.approver_ids or [])}
    # The requester is blocked from deciding their own request only when a real, named approver exists (a
    # manager or a lifecycle owner) — see the matching comment in decide()/decide_leaver_request().
    is_requester_blocked = viewer_id is not None and viewer_id == row.requested_by and bool(row.approver_ids)
    can_decide = row.status == "PENDING" and not is_requester_blocked and ((viewer_id is not None and str(viewer_id) in approver_ids) or viewer_is_admin)
    return LeaverRequestResponse(
        id=row.id, user_id=row.user_id, user_display_name=person.display_name if person else None, user_email=person.email if person else None,
        requested_by=row.requested_by, requested_by_name=requester.display_name if requester else None, justification=row.justification, status=row.status,
        approvers=approvers, decided_by_name=decider.display_name if decider else None, decision_note=row.decision_note, decided_at=row.decided_at,
        created_at=row.created_at, can_decide=can_decide, outcome=row.outcome, accounts_note=accounts_note,
    )


async def start_leaver_request(session: AsyncSession, user_id: UUID, justification: str, actor_subject: str, request_id: str):
    """The manual Start leaver process now: justification -> the person's accounts are disabled straight away ->
    their manager (lifecycle owners when none) approves or denies. Nothing is revoked until it is approved."""
    from app.models import LeaverRequest

    user = await session.get(User, user_id)
    if user is None:
        raise AccessPilotError("USER_NOT_FOUND", "The user was not found.", 404)
    if user.account_type != "NORMAL":
        raise AccessPilotError("VALIDATION_ERROR", "Only regular accounts go through the leaver process; PU/TU accounts follow their owner.", 422)
    requester_id = await _resolve_internal_user_id(session, actor_subject)
    if requester_id is not None and requester_id == user.id:
        raise AccessPilotError("VALIDATION_ERROR", "You cannot start the leaver process for yourself.", 400)
    if is_post_leaver(user):
        raise AccessPilotError("REQUEST_ALREADY_PROCESSED", "The leaver process has already run for this person and their account is still disabled.", 409)
    if (await session.scalars(select(LeaverRequest.id).where(LeaverRequest.user_id == user_id, LeaverRequest.status == "PENDING"))).first() is not None:
        raise AccessPilotError("REQUEST_ALREADY_EXISTS", "A leaver request for this person is already waiting for approval.", 409)
    person_name = user.display_name
    approvers = await _approver_ids(session, user, requester_id)
    # Step 1: disable the real accounts now, so the person cannot sign in while the manager decides.
    outcome = await set_person_enabled(session, user_id, False, actor_subject, request_id)
    changed = [r for r in outcome.results if r.ok and not r.already]
    if outcome.results and not any(r.ok for r in outcome.results):
        raise AccessPilotError("PROVIDER_UNAVAILABLE", "No directory could disable the account, so the request was not created: " + "; ".join(f"{r.provider_name}: {r.error}" for r in outcome.results), 502)
    accounts_note = "; ".join(f"{r.provider_name}: {'already disabled' if r.already else 'disabled' if r.ok else 'FAILED - ' + str(r.error)}" for r in outcome.results) or "no directory accounts"
    row = LeaverRequest(user_id=user_id, requested_by=requester_id, justification=justification.strip(), status="PENDING", approver_ids=[str(a) for a in approvers], disabled_accounts=[str(r.account_id) for r in changed])
    session.add(row)
    await session.flush()
    await record_audit(session, action="LEAVER_REQUESTED", target_type="USER", target_id=user_id, actor_user_id=requester_id, request_id=request_id, metadata={"justification": row.justification, "accounts": accounts_note, "approvers": [str(a) for a in approvers]})
    for approver_id in approvers:
        await create_notification(session, approver_id, "LEAVER_APPROVAL_REQUESTED", f"Approval needed: start the leaver process for {person_name}. Their accounts are already disabled. Justification: {row.justification}", "/access-reviews/mine")
    await session.commit()
    await session.refresh(row)
    return await _leaver_response(session, row, requester_id, False, accounts_note)


async def list_leaver_requests(session: AsyncSession, status: Optional[str], viewer_subject: str, viewer_is_admin: bool, *, only_mine: bool = False):
    from app.models import LeaverRequest

    viewer_id = await _resolve_internal_user_id(session, viewer_subject)
    stmt = select(LeaverRequest).order_by(LeaverRequest.created_at.desc())
    if status:
        stmt = stmt.where(LeaverRequest.status == status)
    responses = [await _leaver_response(session, row, viewer_id, viewer_is_admin) for row in (await session.scalars(stmt)).all()]
    return [r for r in responses if r.can_decide] if only_mine else responses


async def decide_leaver_request(session: AsyncSession, row_id: UUID, approve: bool, note: Optional[str], actor_subject: str, viewer_is_admin: bool, request_id: str):
    """Approve -> the leaver process runs (access revoked, the rest of the person's leaver policy). Deny -> the
    accounts disabled at the start are enabled again and the admin who initiated it is notified."""
    from app.models import LeaverRequest
    from app.services.accounts import set_account_enabled
    from app.services.lifecycle import run_leaver

    row = await session.get(LeaverRequest, row_id)
    if row is None:
        raise AccessPilotError("REQUEST_NOT_FOUND", "The request was not found.", 404)
    if row.status != "PENDING":
        raise AccessPilotError("REQUEST_ALREADY_PROCESSED", "This request was already decided.", 409)
    decider_id = await _resolve_internal_user_id(session, actor_subject)
    # Same rule as the re-enable request above: only block self-decision when a real, named approver exists.
    if decider_id is not None and decider_id == row.requested_by and row.approver_ids:
        raise AccessPilotError("ACCESS_DENIED", "You cannot decide your own request.", 403)
    if not viewer_is_admin and (decider_id is None or str(decider_id) not in {str(a) for a in (row.approver_ids or [])}):
        raise AccessPilotError("ACCESS_DENIED", "Only the person's manager (or a lifecycle owner) can decide this request.", 403)
    user = await session.get(User, row.user_id)
    person_id, person_name, requester_id = user.id, user.display_name, row.requested_by
    disabled_ids = [UUID(str(x)) for x in (row.disabled_accounts or [])]
    if approve:
        event = await run_leaver(session, person_id, "MANUAL", actor_subject, request_id)
        if event is None:
            raise AccessPilotError("VALIDATION_ERROR", "The leaver process could not be started for this person.", 422)
        outcome = f"Leaver process completed: {event.revoked_count} access removed."
    else:
        failures = []
        restored = 0
        for account_id in disabled_ids:
            try:
                await set_account_enabled(session, account_id, True, actor_subject, request_id)
                restored += 1
            except AccessPilotError as exc:
                failures.append(exc.message)
        outcome = f"Denied. {restored} account(s) enabled again." + (f" Could not re-enable: {'; '.join(failures)}" if failures else "")
    row = await session.get(LeaverRequest, row_id)
    row.status, row.decided_by, row.decision_note, row.decided_at, row.outcome = ("APPROVED" if approve else "DENIED"), decider_id, (note or "").strip() or None, datetime.now(timezone.utc), outcome[:500]
    await record_audit(session, action="LEAVER_REQUEST_APPROVED" if approve else "LEAVER_REQUEST_DENIED", target_type="USER", target_id=person_id, actor_user_id=decider_id, request_id=request_id, metadata={"note": row.decision_note, "outcome": outcome})
    if requester_id is not None:
        verdict = f"approved. {outcome}" if approve else f"denied. The account was enabled again. {outcome}"
        await create_notification(session, requester_id, "LEAVER_REQUEST_DECIDED", f"Your request to start the leaver process for {person_name} was {verdict}" + (f" Note: {row.decision_note}" if row.decision_note else ""), "/admin/movers")
    await session.commit()
    await session.refresh(row)
    return await _leaver_response(session, row, decider_id, viewer_is_admin)


# ---------------------------------------------------------------- one-stop status for the User Detail Leaver tab

async def get_leaver_overview(session: AsyncSession, user_id: UUID, viewer_subject: str, viewer_is_admin: bool):
    """Everything the Leaver tab needs in one call: any pending manual-start or re-enable request (with whether
    THIS viewer can decide it right here), and the last few leaver events — so a pending request or a past run is
    never invisible, and a second click never has to guess why nothing happened."""
    from app.models import LeaverRequest
    from app.schemas.lifecycle import LeaverOverviewResponse
    from app.services.lifecycle import list_events, resolve_leaver_policy

    user = await session.get(User, user_id)
    if user is None:
        raise AccessPilotError("USER_NOT_FOUND", "The user was not found.", 404)
    viewer_id = await _resolve_internal_user_id(session, viewer_subject)
    pending_leaver = (await session.scalars(select(LeaverRequest).where(LeaverRequest.user_id == user_id, LeaverRequest.status == "PENDING"))).first()
    pending_reenable = (await session.scalars(select(ReenableRequest).where(ReenableRequest.user_id == user_id, ReenableRequest.status == "PENDING"))).first()
    policy_name = None
    if user.leaver_date is not None and user.leaver_processed_at is None:
        policy_name = (await resolve_leaver_policy(session, user)).name
    events = [event for event in await list_events(session, "LEAVER", 200) if event.user_id == user_id][:5]
    return LeaverOverviewResponse(
        status=user.status, leaver_processed_at=user.leaver_processed_at, leaver_date=user.leaver_date, policy_name=policy_name,
        pending_leaver_request=await _leaver_response(session, pending_leaver, viewer_id, viewer_is_admin) if pending_leaver else None,
        pending_reenable_request=await _response(session, pending_reenable, viewer_id, viewer_is_admin) if pending_reenable else None,
        recent_events=events,
    )
