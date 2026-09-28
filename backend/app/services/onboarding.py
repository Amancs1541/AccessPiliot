from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AccessPilotError
from app.models import AccessAssignment, IdentityProvider, OnboardingImport, OnboardingImportRecord, User
from app.providers.base import NormalizedUser
from app.services.assignments import _resolve_internal_user_id, revoke_assignment
from app.services.birthright import evaluate_birthright_policies, reconcile_birthright_policies_for_user
from app.services.provisioning import provision_real_account
from app.services.audit import record_audit
from app.services.directory_sync import upsert_user

NON_FINAL_ASSIGNMENT_STATUSES = ("ELIGIBLE", "PENDING_APPROVAL", "SCHEDULED", "ACTIVE")

REQUIRED_COLUMNS = ("employeeId", "firstName", "lastName", "email", "department", "status")
VALID_STATUSES = {"ACTIVE", "TERMINATED"}

CSV_PROVIDER_NAME = "CSV / HR Onboarding"


async def _get_or_create_csv_provider(session: AsyncSession) -> IdentityProvider:
    """The CSV onboarding source is modeled as its own IdentityProvider row (type='CSV'), so CSV-sourced
    identities land in the existing `users` table — scoped by provider_id, exactly like Entra-sourced users —
    without ever colliding with a real Entra external_id. Reuses the same provider_id-scoping the whole schema
    already relies on; no new identity concept is introduced."""
    row = (await session.execute(select(IdentityProvider).where(IdentityProvider.type == "CSV"))).scalars().first()
    if row is not None:
        return row
    row = IdentityProvider(name=CSV_PROVIDER_NAME, type="CSV", status="CONNECTED", tenant_id="local-csv")
    session.add(row)
    await session.flush()
    return row


def _normalize_status(raw: str) -> str:
    return (raw or "").strip().upper()


def _parse_effective_date(value: Optional[str]):
    """Optional CSV effectiveDate (YYYY-MM-DD) -> a date, or None if blank/invalid."""
    text = (value or "").strip()
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _valid_email(value: str) -> bool:
    if "@" not in value:
        return False
    local, _, domain = value.rpartition("@")
    return bool(local) and "." in domain and not domain.startswith(".") and not domain.endswith(".")


def _display_name(first: str, last: str) -> str:
    return f"{first.strip()} {last.strip()}".strip()


async def parse_and_validate_csv(session: AsyncSession, filename: str, content: str, actor_subject: str, request_id: str) -> OnboardingImport:
    """Steps 1-3 of the CSV lifecycle: upload, validate, and stage a preview. Never writes to `users` — only the
    onboarding_import(_records) tables are touched here, exactly per the 'do not modify identities during
    validation' rule."""
    provider = await _get_or_create_csv_provider(session)
    uploaded_by = await _resolve_internal_user_id(session, actor_subject)

    try:
        reader = csv.DictReader(io.StringIO(content))
        fieldnames = reader.fieldnames or []
        rows = list(reader)
    except csv.Error as exc:
        return await _fail_import(session, provider.id, filename, uploaded_by, {"error": f"Could not parse CSV: {exc}"})

    missing_columns = [column for column in REQUIRED_COLUMNS if column not in fieldnames]
    if missing_columns:
        return await _fail_import(session, provider.id, filename, uploaded_by, {"error": "Missing required column(s).", "missingColumns": missing_columns})
    if not rows:
        return await _fail_import(session, provider.id, filename, uploaded_by, {"error": "CSV file has no data rows."})

    onboarding_import = OnboardingImport(provider_id=provider.id, filename=filename, status="VALIDATING", uploaded_by=uploaded_by, total_records=len(rows))
    session.add(onboarding_import)
    await session.flush()

    seen_employee_ids: set[str] = set()
    counts = {"CREATE": 0, "UPDATE": 0, "NO_CHANGE": 0, "DISABLE": 0, "ERROR": 0}

    for index, row in enumerate(rows, start=1):
        employee_id = (row.get("employeeId") or "").strip()
        action, error_message = await _plan_row(session, row, employee_id, seen_employee_ids)
        if employee_id:
            seen_employee_ids.add(employee_id)
        counts[action] += 1
        session.add(OnboardingImportRecord(import_id=onboarding_import.id, row_number=index, employee_id=employee_id, action=action, error_message=error_message, raw_data=row))

    onboarding_import.status = "VALIDATED"
    onboarding_import.created_count = counts["CREATE"]
    onboarding_import.updated_count = counts["UPDATE"]
    onboarding_import.no_change_count = counts["NO_CHANGE"]
    onboarding_import.disabled_count = counts["DISABLE"]
    onboarding_import.failed_count = counts["ERROR"]
    await record_audit(session, action="ONBOARDING_IMPORT_VALIDATED", target_type="ONBOARDING_IMPORT", target_id=onboarding_import.id, provider_id=provider.id, actor_user_id=uploaded_by, request_id=request_id, metadata={"filename": filename, **counts})
    await session.commit()
    await session.refresh(onboarding_import)
    return onboarding_import


async def _fail_import(session: AsyncSession, provider_id: UUID, filename: str, uploaded_by: Optional[UUID], error_summary: dict) -> OnboardingImport:
    onboarding_import = OnboardingImport(provider_id=provider_id, filename=filename, status="VALIDATION_FAILED", uploaded_by=uploaded_by, error_summary=error_summary, completed_at=datetime.now(timezone.utc))
    session.add(onboarding_import)
    await session.commit()
    await session.refresh(onboarding_import)
    return onboarding_import


async def _plan_row(session: AsyncSession, row: dict, employee_id: str, seen_employee_ids: set[str]) -> tuple[str, Optional[str]]:
    if not employee_id:
        return "ERROR", "Missing required field: employeeId"
    if employee_id in seen_employee_ids:
        return "ERROR", "Duplicate employeeId in file"
    for column in ("firstName", "lastName", "email", "department"):
        if not (row.get(column) or "").strip():
            return "ERROR", f"Missing required field: {column}"
    email = row["email"].strip()
    if not _valid_email(email):
        return "ERROR", f"Invalid email address: {email}"
    if not _parse_effective_date(row.get("effectiveDate")) and (row.get("effectiveDate") or "").strip():
        return "ERROR", f"Invalid effectiveDate '{row.get('effectiveDate')}' — use YYYY-MM-DD"
    if not _parse_effective_date(row.get("leaverDate")) and (row.get("leaverDate") or "").strip():
        return "ERROR", f"Invalid leaverDate '{row.get('leaverDate')}' — use YYYY-MM-DD"
    status = _normalize_status(row.get("status", ""))
    if status not in VALID_STATUSES:
        return "ERROR", f"Invalid status '{row.get('status')}' — must be ACTIVE or TERMINATED"

    existing = (await session.execute(select(User).where(User.employee_id == employee_id))).scalar_one_or_none()

    if status == "TERMINATED":
        if existing is None:
            return "ERROR", "Cannot terminate an employeeId that has no existing identity"
        return ("NO_CHANGE", None) if existing.status == "DISABLED" else ("DISABLE", None)

    if existing is None:
        return "CREATE", None

    job_title = (row.get("jobTitle") or "").strip() or None
    display_name = _display_name(row["firstName"], row["lastName"])
    changed = (
        existing.email != email
        or existing.display_name != display_name
        or existing.given_name != row["firstName"].strip()
        or existing.surname != row["lastName"].strip()
        or existing.department != row["department"].strip()
        or (existing.job_title or None) != job_title
        or existing.status != "ACTIVE"
        or (_parse_effective_date(row.get("leaverDate")) is not None and existing.leaver_date != _parse_effective_date(row.get("leaverDate")))
    )
    return ("UPDATE", None) if changed else ("NO_CHANGE", None)


async def get_import(session: AsyncSession, import_id: UUID) -> OnboardingImport:
    onboarding_import = await session.get(OnboardingImport, import_id)
    if onboarding_import is None:
        raise AccessPilotError("IMPORT_NOT_FOUND", "The onboarding import was not found.", 404)
    return onboarding_import


async def list_imports(session: AsyncSession) -> list[OnboardingImport]:
    return list((await session.execute(select(OnboardingImport).order_by(OnboardingImport.created_at.desc()))).scalars().all())


async def get_import_preview(session: AsyncSession, import_id: UUID) -> list[OnboardingImportRecord]:
    await get_import(session, import_id)
    return list((await session.execute(select(OnboardingImportRecord).where(OnboardingImportRecord.import_id == import_id).order_by(OnboardingImportRecord.row_number))).scalars().all())


async def _find_or_create_identity(session: AsyncSession, employee_id: str, normalized: NormalizedUser, request_id: str) -> tuple[User, bool, bool]:
    """The core Phase 10 fix: ONE identity row per person, never two. Keyed by `employee_id` (provider-independent
    — not `(provider_id, external_id)`) so the same person is found on every re-upload regardless of which
    connector they currently live under. Returns (row, is_real, newly_provisioned_this_call).

    - New joiner: tries real provisioning first; only falls back to the local CSV bookkeeping provider if no real
      connector is configured or Graph rejects it (e.g. unverified email domain) — never both.
    - Existing identity still on the CSV fallback: retries provisioning on every commit and, if it now succeeds,
      GRADUATES the row in place (same `users.id`, new `provider_id`/`external_id`) rather than creating a second
      row — every AccessAssignment already attached to this identity (e.g. an earlier ELIGIBLE birthright grant)
      stays attached and instantly becomes real-account-targetable. The freshly created duplicate `upsert_user`
      would otherwise have produced is merged away, unless it turns out to already have its own real assignment
      history (a separate pre-existing real account for this email) — then it's treated as the canonical identity
      instead, to avoid orphaning that history."""
    existing = (await session.execute(select(User).where(User.employee_id == employee_id))).scalar_one_or_none()

    if existing is not None:
        existing.email, existing.display_name = normalized.email, normalized.display_name
        existing.given_name, existing.surname = normalized.given_name, normalized.surname
        existing.department, existing.job_title = normalized.department, normalized.job_title
        existing.status = normalized.status
        existing.last_synced_at = datetime.now(timezone.utc)
        current_provider = await session.get(IdentityProvider, existing.provider_id)
        if current_provider is not None and current_provider.type != "CSV":
            await session.flush()
            return existing, True, False

        real_user = await provision_real_account(session, display_name=normalized.display_name, email=normalized.email, given_name=normalized.given_name, surname=normalized.surname, department=normalized.department, job_title=normalized.job_title, request_id=request_id)
        if real_user is None:
            await session.flush()
            return existing, False, False
        has_own_history = (await session.execute(select(AccessAssignment.id).where(AccessAssignment.user_id == real_user.id).limit(1))).scalar_one_or_none()
        if has_own_history:
            real_user.employee_id, real_user.source = employee_id, real_user.source or "CSV_ONBOARDING"
            await session.flush()
            return real_user, True, True
        # Order matters: delete the freshly-created duplicate FIRST and flush, THEN claim its
        # (provider_id, external_id) on `existing` — doing it in the other order risks a transient unique
        # constraint violation, since SQLAlchemy doesn't know these two statements' ordering is significant here
        # (it's a unique-constraint dependency, not a foreign-key one it tracks automatically).
        new_provider_id, new_external_id = real_user.provider_id, real_user.external_id
        await session.delete(real_user)
        await session.flush()
        existing.provider_id, existing.external_id = new_provider_id, new_external_id
        await session.flush()
        return existing, True, True

    real_user = await provision_real_account(session, display_name=normalized.display_name, email=normalized.email, given_name=normalized.given_name, surname=normalized.surname, department=normalized.department, job_title=normalized.job_title, request_id=request_id)
    if real_user is not None:
        real_user.employee_id, real_user.source = employee_id, real_user.source or "CSV_ONBOARDING"
        await session.flush()
        return real_user, True, True

    csv_provider = await _get_or_create_csv_provider(session)
    row, _ = await upsert_user(session, csv_provider.id, normalized)
    row.employee_id, row.source = employee_id, "CSV_ONBOARDING"
    await session.flush()
    return row, False, False


async def commit_import(session: AsyncSession, import_id: UUID, actor_subject: str, request_id: str) -> OnboardingImport:
    """Step 5 of the CSV lifecycle."""
    onboarding_import = await get_import(session, import_id)
    if onboarding_import.status != "VALIDATED":
        raise AccessPilotError("IMPORT_NOT_COMMITTABLE", "Only a validated import can be committed.", 409)

    actor_id = await _resolve_internal_user_id(session, actor_subject)
    records = await get_import_preview(session, import_id)
    access_revoked = access_revoke_failed = birthright_assignments_created = birthright_assignments_revoked = real_accounts_provisioned = moves_scheduled = 0
    for record in records:
        if record.action == "ERROR":
            continue
        row = record.raw_data or {}

        if record.action == "DISABLE":
            existing = (await session.execute(select(User).where(User.employee_id == record.employee_id))).scalar_one_or_none()
            if existing is None:
                continue
            existing.status = "DISABLED"
            existing.last_synced_at = datetime.now(timezone.utc)
            await session.flush()
            revoked, failed = await _revoke_all_access_for_leaver(session, existing.id, actor_subject, record.employee_id, onboarding_import, request_id)
            access_revoked += revoked
            access_revoke_failed += failed
            # The one leaver runner (app.services.lifecycle.run_leaver): per the person's leaver policy it disables
            # their linked PU/TU accounts and — new — their REAL account in every connected IdP (a CSV termination
            # used to flip only the local status). Access was already revoked just above.
            from app.services.lifecycle import run_leaver
            await run_leaver(session, existing.id, "CSV", actor_subject, request_id, skip_revoke=True, already_revoked=revoked)
            continue

        # CREATE / UPDATE / NO_CHANGE all resolve to the same one identity row, and are all worth re-evaluating —
        # NO_CHANGE in particular still retries provisioning if a prior attempt fell back to the CSV bookkeeping
        # provider (e.g. the domain wasn't verified yet at the time), self-healing on every re-upload.
        normalized = NormalizedUser(
            external_id=record.employee_id,
            email=(row.get("email") or "").strip(),
            display_name=_display_name(row.get("firstName", ""), row.get("lastName", "")),
            given_name=(row.get("firstName") or "").strip() or None,
            surname=(row.get("lastName") or "").strip() or None,
            department=(row.get("department") or "").strip() or None,
            job_title=(row.get("jobTitle") or "").strip() or None,
            status="ACTIVE",
        )
        # Captured BEFORE _find_or_create_identity overwrites the row: a person who already exists and whose
        # department/job title differs from this CSV row is a mover, and must go through the same reconcile a
        # directory sync or an in-app attribute edit would run (revoke what the old attributes granted, then grant
        # what the new ones qualify for) — not just the grant half.
        previous = (await session.execute(select(User).where(User.employee_id == record.employee_id))).scalar_one_or_none()
        previous_attributes = (previous.department, previous.job_title, previous.status, (previous.email or "").lower()) if previous is not None else None
        previous_values = {"department": previous.department, "job_title": previous.job_title} if previous is not None else None
        # Effective dating: an existing person's department/job-title change with a FUTURE effectiveDate is scheduled
        # instead of applied — nothing about them changes until that moment (see app.services.lifecycle).
        effective_date = _parse_effective_date(row.get("effectiveDate"))
        if effective_date is not None and previous is not None and (previous.department != normalized.department or previous.job_title != normalized.job_title):
            from zoneinfo import ZoneInfo
            from app.services.lifecycle import schedule_move
            from app.services.security_settings import get_security_settings
            tz = ZoneInfo((await get_security_settings(session)).timezone)
            effective_at = datetime(effective_date.year, effective_date.month, effective_date.day, tzinfo=tz).astimezone(timezone.utc)
            if effective_at > datetime.now(timezone.utc):
                await schedule_move(session, previous.id, normalized.department, normalized.job_title, effective_at, "CSV", actor_subject, request_id, import_id=onboarding_import.id)
                moves_scheduled += 1
                continue
        identity, _, newly_provisioned = await _find_or_create_identity(session, record.employee_id, normalized, request_id)
        csv_leaver_date = _parse_effective_date(row.get("leaverDate"))
        if csv_leaver_date is not None and identity.leaver_date != csv_leaver_date:
            identity.leaver_date, identity.leaver_processed_at, identity.leaver_reminders_sent = csv_leaver_date, None, []
            await session.flush()
        # Birthright grants always land ELIGIBLE, never bypassed straight to ACTIVE — even for a real, freshly
        # provisioned account. This matches the rest of AccessPilot's custom PIM model consistently: birthright
        # decides WHAT a joiner is entitled to, but the person (or an Admin on their behalf) still has to
        # self-activate it via My Access, exactly like every other eligible grant in the app.
        if previous_attributes is not None and previous_attributes != (identity.department, identity.job_title, identity.status, (identity.email or "").lower()):
            outcome = await reconcile_birthright_policies_for_user(session, identity.id, actor_subject, request_id)
            birthright_assignments_created += len(outcome["granted"])
            birthright_assignments_revoked += len(outcome["revoked"])
            from app.services.lifecycle import build_changes, record_mover
            await record_mover(session, identity.id, build_changes(previous_values, {"department": identity.department, "job_title": identity.job_title}), "CSV", outcome, request_id)
        else:
            joiner_grants = await evaluate_birthright_policies(session, identity.id, actor_subject, request_id)
            birthright_assignments_created += len(joiner_grants)
            if previous is None:
                from app.services.lifecycle import record_joiner
                await record_joiner(session, identity.id, "CSV", {"granted": joiner_grants}, request_id)
        if newly_provisioned:
            real_accounts_provisioned += 1

    onboarding_import.status = "COMMITTED"
    onboarding_import.completed_at = datetime.now(timezone.utc)
    onboarding_import.access_revoked_count = access_revoked
    onboarding_import.access_revoke_failed_count = access_revoke_failed
    onboarding_import.real_accounts_provisioned_count = real_accounts_provisioned
    onboarding_import.birthright_assignments_created_count = birthright_assignments_created
    onboarding_import.birthright_assignments_revoked_count = birthright_assignments_revoked
    onboarding_import.moves_scheduled_count = moves_scheduled
    await record_audit(
        session, action="ONBOARDING_IMPORT_COMMITTED", target_type="ONBOARDING_IMPORT", target_id=onboarding_import.id,
        provider_id=onboarding_import.provider_id, actor_user_id=actor_id, request_id=request_id,
        metadata={"filename": onboarding_import.filename, "created": onboarding_import.created_count, "updated": onboarding_import.updated_count, "disabled": onboarding_import.disabled_count, "noChange": onboarding_import.no_change_count, "failed": onboarding_import.failed_count, "accessRevoked": access_revoked, "accessRevokeFailed": access_revoke_failed, "birthrightAssignmentsCreated": birthright_assignments_created, "realAccountsProvisioned": real_accounts_provisioned},
    )
    await session.commit()
    await session.refresh(onboarding_import)
    return onboarding_import


async def _revoke_all_access_for_leaver(session: AsyncSession, user_id: UUID, actor_subject: str, employee_id: str, onboarding_import: OnboardingImport, request_id: str) -> tuple[int, int]:
    """Leaver step of the CSV lifecycle: 'Termination detected -> Disable Identity -> Disable Account -> Revoke
    Access -> Record Audit Event'. Reuses `revoke_assignment()` UNMODIFIED (the same universal, any-status revoke
    Admin Revoke already uses) for every one of this identity's non-final assignments, so a terminated employee
    doesn't just look disabled — their real Entra/Graph access is actually removed too. One assignment's Graph
    failure doesn't block the others, mirroring how directory_sync already isolates per-item provider failures."""
    justification = f"Automated leaver revocation — employeeId {employee_id} marked TERMINATED via onboarding import '{onboarding_import.filename}' ({onboarding_import.id})"
    assignment_ids = (await session.execute(select(AccessAssignment.id).where(AccessAssignment.user_id == user_id, AccessAssignment.status.in_(NON_FINAL_ASSIGNMENT_STATUSES)))).scalars().all()
    revoked = failed = 0
    for assignment_id in assignment_ids:
        try:
            await revoke_assignment(session, assignment_id, actor_subject, justification, request_id)
            revoked += 1
        except AccessPilotError:
            failed += 1
    return revoked, failed
