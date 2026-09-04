from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AccessPilotError
from app.models import AccessAssignment, AccessPackage, AccessPackageItem, Application, AuditLog, IdentityProvider, Role, SodException, SodExceptionRequest, SodNotification, SodNotificationSettings, SodPolicy, SodPolicyEntity, User, UserGroup
from app.providers.entra import EntraProvider
from app.providers.graph_client import GraphError
from app.schemas.assignments import AssignmentCreate
from app.schemas.sod import SodExceptionCreate, SodExceptionRequestCreate, SodExceptionRequestDeny, SodExceptionRequestGrant, SodExceptionRequestResponse, SodExceptionResponse, SodNotificationResponse, SodNotificationSettingsUpdateRequest, SodPolicyCreate, SodPolicyEntityResponse, SodPolicyResponse, SodPolicyUpdate, SodViolation, SodViolationHolding
from app.services.assignments import _app_role_name, _resolve_target, create_assignment, revoke_provider_access
from app.services.audit import record_audit
from app.services.notifications import create_notification
from app.services.provider_configuration import _connector

logger = logging.getLogger("accesspilot.sod")

ResourceTuple = tuple[str, UUID, Optional[str]]


def _dedupe_entities(entities: list) -> list:
    """Same lesson as _apply_package_eligibility's dedupe fix for access_package_eligibility: the DB unique
    constraint alone can't be trusted here since it has a nullable column (app_role_external_id), which Postgres
    treats as always-distinct — a duplicate GROUP/ROLE/PACKAGE entity on the same side would silently slip past
    it. Dedupe the submitted list before ever touching the database."""
    seen: set[tuple[str, str, str, Optional[str]]] = set()
    deduped = []
    for entity in entities:
        key = (entity.conflict_side, entity.entity_type, str(entity.entity_id), entity.app_role_external_id)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entity)
    return deduped


async def _expand_entity_to_resource_tuples(session: AsyncSession, entity: SodPolicyEntity) -> list[ResourceTuple]:
    """PACKAGE entities are resolved live against AccessPackageItem, never cached — editing a package's items (or
    deleting the package outright, in which case this returns []: "matches nothing", the safe default) is
    automatically reflected the next time a check runs, no migration/backfill needed."""
    if entity.entity_type == "PACKAGE":
        items = list((await session.scalars(select(AccessPackageItem).where(AccessPackageItem.package_id == entity.entity_id))).all())
        return [(item.resource_type, item.resource_id, item.app_role_external_id) for item in items]
    return [(entity.entity_type, entity.entity_id, entity.app_role_external_id)]


async def _resolve_entity_display_name(session: AsyncSession, entity: SodPolicyEntity) -> tuple[Optional[str], bool]:
    if entity.entity_type == "PACKAGE":
        package = await session.get(AccessPackage, entity.entity_id)
        return (package.name if package else None, package is not None)
    try:
        _, name, _ = await _resolve_target(session, entity.entity_type, entity.entity_id)
    except AccessPilotError:
        return None, False
    if entity.entity_type == "APPLICATION" and entity.app_role_external_id:
        application = await session.get(Application, entity.entity_id)
        role_name = _app_role_name(application, entity.app_role_external_id)
        if role_name:
            name = f"{name} — {role_name}"
    return name, True


def _tuple_condition(resource_type: str, resource_id: UUID, app_role_external_id: Optional[str]):
    condition = and_(AccessAssignment.resource_type == resource_type, AccessAssignment.resource_id == resource_id)
    if app_role_external_id is not None:
        return and_(condition, AccessAssignment.app_role_external_id == app_role_external_id)
    return and_(condition, AccessAssignment.app_role_external_id.is_(None))


async def _resolve_user_holdings(session: AsyncSession, user_id: UUID, tuples: set[ResourceTuple]) -> list[SodViolationHolding]:
    """The user's REAL holdings among these entitlements right now — checked two ways, so a real conflict is
    caught regardless of how either side was actually granted:
    1. AccessPilot-tracked: an ACTIVE **or ELIGIBLE** AccessAssignment row. ELIGIBLE counts deliberately, not just
       ACTIVE: an eligible-but-not-yet-activated grant is still a standing right the user (or an admin on their
       behalf) can turn real at any moment with no further review, so two conflicting items sitting ELIGIBLE at
       the same time is exactly the state this engine exists to prevent — waiting until one of them is actually
       activated would be too late to have ever stopped the assignment that created the conflict in the first
       place. PENDING_APPROVAL/SCHEDULED/REJECTED/REVOKED/EXPIRED never count — they're not yet, or no longer, a
       standing grant.
    2. Direct-in-Entra: real membership the AccessPilot assignment engine was never involved in granting — e.g.
       AccessPilot's own Admin/User app roles (always assigned directly via Entra's Enterprise Application
       blade, never through AccessPilot itself), or any group/role/app-role assigned outside AccessPilot.
       GROUP uses the already-synced, source-agnostic UserGroup table (no Graph call). ROLE and APPLICATION have
       no synced per-user table, so this does a live, on-demand Graph read — the exact same
       "isinstance(connector, EntraProvider), try/except GraphError, never blocks on failure" pattern already
       used for the User Detail page's Direct-in-Entra detection (see directory_read.py).
    Used both as a boolean check (_user_holds_any, the preventive gate — only runs at all when a SoD policy's
    opposite side actually contains a ROLE/APPLICATION entity, never on every assignment) and, for the detective
    scan, to get the actual holdings to display."""
    if not tuples:
        return []
    holdings: list[SodViolationHolding] = []
    seen: set[ResourceTuple] = set()

    conditions = [_tuple_condition(*t) for t in tuples]
    stmt = select(AccessAssignment).where(AccessAssignment.user_id == user_id, AccessAssignment.status.in_(("ACTIVE", "ELIGIBLE")), or_(*conditions))
    for assignment in (await session.scalars(stmt)).all():
        key = (assignment.resource_type, assignment.resource_id, assignment.app_role_external_id)
        if key in seen:
            continue
        seen.add(key)
        holdings.append(await _to_holding(session, assignment))

    group_ids = {resource_id for (resource_type, resource_id, _) in tuples if resource_type == "GROUP"}
    role_ids = {resource_id for (resource_type, resource_id, _) in tuples if resource_type == "ROLE"}
    app_pairs = {(resource_id, app_role_external_id) for (resource_type, resource_id, app_role_external_id) in tuples if resource_type == "APPLICATION"}

    if group_ids:
        stmt = select(UserGroup.group_id).where(UserGroup.user_id == user_id, UserGroup.group_id.in_(group_ids))
        for (group_id,) in (await session.execute(stmt)).all():
            key = ("GROUP", group_id, None)
            if key in seen:
                continue
            seen.add(key)
            _, resource_name, _ = await _resolve_target(session, "GROUP", group_id)
            holdings.append(SodViolationHolding(assignment_id=None, resource_type="GROUP", resource_id=group_id, resource_display_name=resource_name, app_role_external_id=None, source="DIRECT_IN_ENTRA"))

    if not role_ids and not app_pairs:
        return holdings

    user = await session.get(User, user_id)
    if user is None:
        return holdings
    provider = await session.get(IdentityProvider, user.provider_id)
    if provider is None:
        return holdings
    connector = _connector(provider)
    if not isinstance(connector, EntraProvider):
        return holdings

    if role_ids:
        try:
            live_role_external_ids = set(await connector.get_user_directory_role_ids(user.external_id))
        except GraphError as exc:
            # Fail-open (never blocks a grant on a Graph hiccup), but distinctly logged: this means the
            # direct-in-Entra ROLE check for this user did NOT actually run, not that it ran and found nothing —
            # the two look identical to the caller otherwise, so this is the only signal an admin has that SoD
            # enforcement was silently incomplete for this check.
            logger.warning("SoD direct-in-Entra ROLE check failed for user %s (treated as no match): %s", user_id, exc)
            live_role_external_ids = set()
        if live_role_external_ids:
            roles = (await session.execute(select(Role).where(Role.id.in_(role_ids), Role.external_id.in_(live_role_external_ids)))).scalars().all()
            for role in roles:
                key = ("ROLE", role.id, None)
                if key in seen:
                    continue
                seen.add(key)
                holdings.append(SodViolationHolding(assignment_id=None, resource_type="ROLE", resource_id=role.id, resource_display_name=role.name, app_role_external_id=None, source="DIRECT_IN_ENTRA"))

    if app_pairs:
        try:
            live_app_roles = await connector.get_user_app_role_assignments(user.external_id)
        except GraphError as exc:
            logger.warning("SoD direct-in-Entra APPLICATION check failed for user %s (treated as no match): %s", user_id, exc)
            live_app_roles = []
        for entry in live_app_roles:
            application = (await session.execute(select(Application).where(Application.external_id == entry["resource_id"]))).scalars().first()
            if not application:
                continue
            pair = (application.id, entry.get("app_role_id") or None)
            if pair not in app_pairs:
                continue
            key = ("APPLICATION", application.id, pair[1])
            if key in seen:
                continue
            seen.add(key)
            role_name = _app_role_name(application, pair[1])
            display_name = f"{application.name} — {role_name}" if role_name else application.name
            holdings.append(SodViolationHolding(assignment_id=None, resource_type="APPLICATION", resource_id=application.id, resource_display_name=display_name, app_role_external_id=pair[1], source="DIRECT_IN_ENTRA"))

    return holdings


async def _user_holds_any(session: AsyncSession, user_id: UUID, tuples: set[ResourceTuple]) -> bool:
    return bool(await _resolve_user_holdings(session, user_id, tuples))


async def _matching_active_assignments(session: AsyncSession, tuples: set[ResourceTuple]) -> list[AccessAssignment]:
    """Despite the name (kept for continuity with call sites), matches ACTIVE **or ELIGIBLE** rows — see
    _resolve_user_holdings's docstring for why ELIGIBLE counts as a real holding for SoD purposes."""
    if not tuples:
        return []
    conditions = [_tuple_condition(*t) for t in tuples]
    stmt = select(AccessAssignment).where(AccessAssignment.status.in_(("ACTIVE", "ELIGIBLE")), or_(*conditions))
    return list((await session.scalars(stmt)).all())


async def _to_holding(session: AsyncSession, assignment: AccessAssignment) -> SodViolationHolding:
    _, resource_name, _ = await _resolve_target(session, assignment.resource_type, assignment.resource_id)
    return SodViolationHolding(assignment_id=assignment.id, resource_type=assignment.resource_type, resource_id=assignment.resource_id, resource_display_name=resource_name, app_role_external_id=assignment.app_role_external_id, source="ACCESSPILOT")


async def _add_direct_group_holdings(session: AsyncSession, tuples: set[ResourceTuple], by_user: dict[UUID, list[SodViolationHolding]]) -> None:
    """Detective-scan counterpart of _user_holds_any's GROUP check — cheap (local UserGroup table, no Graph
    call), so unlike ROLE/APPLICATION this runs for every user, not just the preventive per-user check. Skips a
    (user, group) pair already represented by an AccessPilot-tracked holding, so a group AccessPilot itself
    granted (which also has a UserGroup row) is never listed twice."""
    group_ids = {resource_id for (resource_type, resource_id, _) in tuples if resource_type == "GROUP"}
    if not group_ids:
        return
    rows = (await session.execute(select(UserGroup.user_id, UserGroup.group_id).where(UserGroup.group_id.in_(group_ids)))).all()
    for user_id, group_id in rows:
        if any(h.resource_type == "GROUP" and h.resource_id == group_id for h in by_user.get(user_id, [])):
            continue
        _, resource_name, _ = await _resolve_target(session, "GROUP", group_id)
        by_user.setdefault(user_id, []).append(SodViolationHolding(assignment_id=None, resource_type="GROUP", resource_id=group_id, resource_display_name=resource_name, app_role_external_id=None, source="DIRECT_IN_ENTRA"))


async def get_active_sod_exception(session: AsyncSession, policy_id: UUID, user_id: UUID) -> Optional[SodException]:
    """A currently-active, time-boxed risk acceptance for this exact (policy, user) pair, or None. Scoped to the
    pair, not to the specific entitlements held at grant time — see SodException's own docstring for why."""
    now = datetime.now(timezone.utc)
    stmt = select(SodException).where(SodException.sod_policy_id == policy_id, SodException.user_id == user_id, SodException.revoked_at.is_(None), SodException.expires_at > now)
    return (await session.execute(stmt)).scalars().first()


async def get_sod_exception_covering_assignment(session: AsyncSession, assignment: AccessAssignment) -> Optional[SodException]:
    """The reverse of _find_exception_granted_assignment: given an ELIGIBLE/ACTIVE assignment, is there a
    currently-live SodException it depends on to stay allowed? Used purely for display (see
    hydrate_display_fields's sod_exception_expires_at) — the real gate is always the live check_sod_conflicts()
    call at activation time (assignments.py), never this. Returns None for a plain assignment never touched by
    the exception-request workflow, or one whose covering exception has since been revoked or expired (a stale
    ELIGIBLE row from an exception that's gone — see §9b of docs/19_SOD_ENGINE.md for why that can still exist
    momentarily even with the auto-revoke worker running).

    Same ambiguity risk as the reverse lookup, mirrored: the request must have existed at or before this
    assignment was created (a request can't grant an assignment that predates it), so ordering by created_at
    DESC and taking the most recent request at-or-before this assignment's own created_at deterministically finds
    the one that actually produced it, not an older, unrelated request for the same target."""
    if assignment.status not in ("ELIGIBLE", "ACTIVE"):
        return None
    conditions = [
        SodExceptionRequest.user_id == assignment.user_id,
        SodExceptionRequest.resource_type == assignment.resource_type,
        SodExceptionRequest.resource_id == assignment.resource_id,
        SodExceptionRequest.sod_exception_id.isnot(None),
        SodExceptionRequest.created_at <= assignment.created_at,
        SodExceptionRequest.app_role_external_id == assignment.app_role_external_id if assignment.app_role_external_id else SodExceptionRequest.app_role_external_id.is_(None),
    ]
    request = (await session.scalars(select(SodExceptionRequest).where(*conditions).order_by(SodExceptionRequest.created_at.desc()))).first()
    if request is None or request.sod_exception_id is None:
        return None
    # The expiry comparison is done in SQL, not Python, deliberately — SQLite (this test suite's DB) stores
    # datetimes naive, while the app always works in aware UTC; comparing an aware `now` against a naive column
    # value in Python raises TypeError, but the same comparison inside the WHERE clause is handled by the DB
    # driver without issue (the same reason get_active_sod_exception's near-identical check works this way).
    now = datetime.now(timezone.utc)
    return (await session.scalars(select(SodException).where(SodException.id == request.sod_exception_id, SodException.revoked_at.is_(None), SodException.expires_at > now))).first()


async def _recently_deactivated_or_revoked(session: AsyncSession, user_id: UUID, tuples: set[ResourceTuple], cooldown_hours: int) -> bool:
    """The cooldown anti-gaming check: was any of these entitlements ACTIVE for this user until recently
    (deactivated or revoked within the cooldown window)? Without this, a user can dodge every SoD check by
    deactivating side A and immediately activating side B — no single moment ever has both sides simultaneously
    ACTIVE, so the plain _user_holds_any check alone never catches it. Derived from the audit log rather than a
    dedicated timestamp column: deactivate_assignment()/revoke_assignment() already record
    ASSIGNMENT_DEACTIVATED/ASSIGNMENT_REVOKED with a real timestamp against the assignment's own id, and the
    assignment row's resource_type/resource_id/app_role_external_id never change after the fact — reusing that
    avoids a schema change just for this."""
    if not tuples:
        return False
    since = datetime.now(timezone.utc) - timedelta(hours=cooldown_hours)
    conditions = [_tuple_condition(*t) for t in tuples]
    stmt = (
        select(AuditLog.id)
        .join(AccessAssignment, AccessAssignment.id == AuditLog.target_id)
        .where(
            AuditLog.action.in_(("ASSIGNMENT_DEACTIVATED", "ASSIGNMENT_REVOKED")),
            AuditLog.target_type == "ASSIGNMENT",
            AuditLog.timestamp >= since,
            AccessAssignment.user_id == user_id,
            or_(*conditions),
        )
    )
    return (await session.execute(stmt)).first() is not None


async def check_sod_conflicts(session: AsyncSession, user_id: UUID, resource_type: str, resource_id: UUID, app_role_external_id: Optional[str] = None) -> list[SodPolicy]:
    """For every ACTIVE SoD policy, does the given entitlement land on side A or B? If so, does this user already
    hold (an ACTIVE assignment matching) anything on the OPPOSITE side — or, if the cooldown setting is enabled,
    did they hold it until recently (see _recently_deactivated_or_revoked)? Runs inside the caller's live
    transaction so it correctly sees rows already committed earlier in the same request (needed for the
    intra-package case, where item #2 of the same package must see item #1's just-committed assignment). A
    policy with an active, unexpired SodException for this exact (policy, user) pair is deliberately excluded
    from the returned conflicts — the risk has already been formally reviewed and accepted, so it must not keep
    blocking every subsequent grant attempt until the acceptance itself expires or is revoked."""
    settings = await get_sod_notification_settings(session)
    policies = list((await session.scalars(select(SodPolicy).where(SodPolicy.status == "ACTIVE"))).all())
    conflicts: list[SodPolicy] = []
    target = (resource_type, resource_id, app_role_external_id)
    for policy in policies:
        entities = list((await session.scalars(select(SodPolicyEntity).where(SodPolicyEntity.sod_policy_id == policy.id))).all())
        side_of_new: Optional[str] = None
        for entity in entities:
            tuples = await _expand_entity_to_resource_tuples(session, entity)
            if target in tuples:
                side_of_new = entity.conflict_side
                break
        if side_of_new is None:
            continue
        opposite_side = "B" if side_of_new == "A" else "A"
        opposite_tuples: set[ResourceTuple] = set()
        for entity in entities:
            if entity.conflict_side == opposite_side:
                opposite_tuples.update(await _expand_entity_to_resource_tuples(session, entity))
        if not opposite_tuples:
            continue
        holds_now = await _user_holds_any(session, user_id, opposite_tuples)
        held_recently = settings.cooldown_enabled and not holds_now and await _recently_deactivated_or_revoked(session, user_id, opposite_tuples, settings.cooldown_hours)
        if holds_now or held_recently:
            if await get_active_sod_exception(session, policy.id, user_id) is not None:
                continue
            conflicts.append(policy)
    return conflicts


async def get_sod_violations(session: AsyncSession, policy_id: Optional[UUID] = None) -> list[SodViolation]:
    """Live detective scan — never stored/materialized, matching this app's own get_user_access_segments()
    precedent. Batches each side into one OR-chain query rather than one query per entity row."""
    query = select(SodPolicy).where(SodPolicy.status == "ACTIVE")
    if policy_id is not None:
        query = query.where(SodPolicy.id == policy_id)
    policies = list((await session.scalars(query)).all())
    violations: list[SodViolation] = []
    for policy in policies:
        entities = list((await session.scalars(select(SodPolicyEntity).where(SodPolicyEntity.sod_policy_id == policy.id))).all())
        side_a_tuples: set[ResourceTuple] = set()
        side_b_tuples: set[ResourceTuple] = set()
        for entity in entities:
            tuples = await _expand_entity_to_resource_tuples(session, entity)
            (side_a_tuples if entity.conflict_side == "A" else side_b_tuples).update(tuples)
        if not side_a_tuples or not side_b_tuples:
            continue

        if any(rt in ("ROLE", "APPLICATION") for rt, _, _ in side_a_tuples | side_b_tuples):
            # No cheap synced-per-user source exists for ROLE/APPLICATION direct-in-Entra holdings (unlike
            # GROUP's UserGroup table) — finding them requires a live Graph read per candidate user, so this
            # scans every directory user rather than only ones already visible via local data (that's the whole
            # point: e.g. AccessPilot's own Admin/User app roles are NEVER tracked as an AccessAssignment, so a
            # user holding both would otherwise never surface here at all). Cost scales with directory size —
            # acceptable for realistic tenant sizes; a future optimization would batch/cache Graph reads instead
            # of one call per user per policy.
            all_users = list((await session.scalars(select(User))).all())
            for user in all_users:
                side_a_holdings = await _resolve_user_holdings(session, user.id, side_a_tuples)
                if not side_a_holdings:
                    continue
                side_b_holdings = await _resolve_user_holdings(session, user.id, side_b_tuples)
                if not side_b_holdings:
                    continue
                exception = await get_active_sod_exception(session, policy.id, user.id)
                violations.append(SodViolation(policy_id=policy.id, policy_name=policy.name, severity=policy.severity, user_id=user.id, user_display_name=user.display_name, side_a_holdings=side_a_holdings, side_b_holdings=side_b_holdings, exception_active=exception is not None, exception_expires_at=exception.expires_at if exception else None))
            continue

        # Cheap path for GROUP-only policies: pure DB, scales to any tenant size.
        a_by_user: dict[UUID, list[SodViolationHolding]] = {}
        for assignment in await _matching_active_assignments(session, side_a_tuples):
            a_by_user.setdefault(assignment.user_id, []).append(await _to_holding(session, assignment))
        await _add_direct_group_holdings(session, side_a_tuples, a_by_user)
        b_by_user: dict[UUID, list[SodViolationHolding]] = {}
        for assignment in await _matching_active_assignments(session, side_b_tuples):
            b_by_user.setdefault(assignment.user_id, []).append(await _to_holding(session, assignment))
        await _add_direct_group_holdings(session, side_b_tuples, b_by_user)
        for user_id in set(a_by_user) & set(b_by_user):
            user = await session.get(User, user_id)
            exception = await get_active_sod_exception(session, policy.id, user_id)
            violations.append(SodViolation(
                policy_id=policy.id, policy_name=policy.name, severity=policy.severity,
                user_id=user_id, user_display_name=user.display_name if user else None,
                side_a_holdings=a_by_user[user_id],
                side_b_holdings=b_by_user[user_id],
                exception_active=exception is not None, exception_expires_at=exception.expires_at if exception else None,
            ))
    return violations


async def list_sod_policies(session: AsyncSession) -> list[SodPolicy]:
    return list((await session.scalars(select(SodPolicy).order_by(SodPolicy.created_at.desc()))).all())


async def get_sod_policy(session: AsyncSession, policy_id: UUID) -> SodPolicy:
    policy = await session.get(SodPolicy, policy_id)
    if not policy:
        raise AccessPilotError("SOD_POLICY_NOT_FOUND", "The SoD policy was not found.", 404)
    return policy


async def to_policy_response(session: AsyncSession, policy: SodPolicy) -> SodPolicyResponse:
    entities = list((await session.scalars(select(SodPolicyEntity).where(SodPolicyEntity.sod_policy_id == policy.id))).all())
    entity_responses = []
    for entity in entities:
        name, resolved = await _resolve_entity_display_name(session, entity)
        entity_responses.append(SodPolicyEntityResponse(id=entity.id, conflict_side=entity.conflict_side, entity_type=entity.entity_type, entity_id=entity.entity_id, entity_display_name=name, app_role_external_id=entity.app_role_external_id, entity_resolved=resolved))
    return SodPolicyResponse(id=policy.id, name=policy.name, description=policy.description, severity=policy.severity, status=policy.status, entities=entity_responses, created_at=policy.created_at, updated_at=policy.updated_at)


async def create_sod_policy(session: AsyncSession, data: SodPolicyCreate, actor_id: Optional[UUID], request_id: str) -> SodPolicy:
    existing = (await session.execute(select(SodPolicy).where(SodPolicy.name == data.name))).scalars().first()
    if existing is not None:
        raise AccessPilotError("SOD_POLICY_NAME_TAKEN", "A SoD policy with this name already exists.", 409)
    policy = SodPolicy(name=data.name, description=data.description, severity=data.severity, status="ACTIVE")
    session.add(policy)
    await session.flush()
    for entity in _dedupe_entities(data.entities):
        session.add(SodPolicyEntity(sod_policy_id=policy.id, conflict_side=entity.conflict_side, entity_type=entity.entity_type, entity_id=entity.entity_id, app_role_external_id=entity.app_role_external_id))
    await record_audit(session, action="SOD_POLICY_CREATED", target_type="SOD_POLICY", target_id=policy.id, actor_user_id=actor_id, request_id=request_id, metadata={"name": data.name, "severity": data.severity})
    await session.commit()
    await session.refresh(policy)
    return policy


async def update_sod_policy(session: AsyncSession, policy_id: UUID, data: SodPolicyUpdate, actor_id: Optional[UUID], request_id: str) -> SodPolicy:
    policy = await get_sod_policy(session, policy_id)
    duplicate = (await session.execute(select(SodPolicy).where(SodPolicy.name == data.name, SodPolicy.id != policy_id))).scalars().first()
    if duplicate is not None:
        raise AccessPilotError("SOD_POLICY_NAME_TAKEN", "A SoD policy with this name already exists.", 409)
    policy.name = data.name
    policy.description = data.description
    policy.severity = data.severity
    policy.status = data.status
    for existing_entity in list((await session.scalars(select(SodPolicyEntity).where(SodPolicyEntity.sod_policy_id == policy_id))).all()):
        await session.delete(existing_entity)
    await session.flush()
    for entity in _dedupe_entities(data.entities):
        session.add(SodPolicyEntity(sod_policy_id=policy.id, conflict_side=entity.conflict_side, entity_type=entity.entity_type, entity_id=entity.entity_id, app_role_external_id=entity.app_role_external_id))
    await record_audit(session, action="SOD_POLICY_UPDATED", target_type="SOD_POLICY", target_id=policy.id, actor_user_id=actor_id, request_id=request_id, metadata={"status": data.status, "severity": data.severity})
    await session.commit()
    await session.refresh(policy)
    return policy


async def delete_sod_policy(session: AsyncSession, policy_id: UUID, actor_id: Optional[UUID], request_id: str) -> Optional[SodPolicy]:
    """Deletes the policy outright if it has no real history — otherwise disables it instead (kept for
    exception/notification audit history) and returns the disabled policy. A true delete returns None, mirroring
    delete_package()'s identical "delete if never used, else archive" shape.

    sod_exceptions, sod_notifications, and sod_exception_requests are all deliberately permanent, full-history
    tables (nothing in them is ever deleted elsewhere in this engine — see docs/19_SOD_ENGINE.md §9/§16) with a
    real FK to sod_policy_id and no ON DELETE CASCADE. A policy that has ever had an exception granted or a
    notification fired against it can therefore never be hard-deleted at all — confirmed live against Postgres
    (SQLite's test DB doesn't enforce the FK, so this never surfaced in the test suite). Disabling instead of
    erroring matches how this app already treats "can't truly delete, has real history" everywhere else."""
    policy = await get_sod_policy(session, policy_id)
    has_history = (
        (await session.execute(select(SodException.id).where(SodException.sod_policy_id == policy_id).limit(1))).scalar_one_or_none() is not None
        or (await session.execute(select(SodNotification.id).where(SodNotification.sod_policy_id == policy_id).limit(1))).scalar_one_or_none() is not None
        or (await session.execute(select(SodExceptionRequest.id).where(SodExceptionRequest.sod_policy_id == policy_id).limit(1))).scalar_one_or_none() is not None
    )
    if has_history:
        policy.status = "DISABLED"
        await record_audit(session, action="SOD_POLICY_DISABLED", target_type="SOD_POLICY", target_id=policy_id, actor_user_id=actor_id, request_id=request_id, metadata={"reason": "DELETE_REQUESTED_BUT_HAS_HISTORY"})
        await session.commit()
        await session.refresh(policy)
        return policy

    for entity in list((await session.scalars(select(SodPolicyEntity).where(SodPolicyEntity.sod_policy_id == policy_id))).all()):
        await session.delete(entity)
    # No ORM relationship() exists between SodPolicy and SodPolicyEntity (this codebase's convention — raw FK
    # columns, no cascade magic), so SQLAlchemy has no dependency information to order these two deletes
    # correctly on its own. An explicit flush forces the entity deletes to actually execute before the policy
    # delete is even issued — without it, Postgres's real FK constraint rejects the policy delete as "still
    # referenced" (the same class of bug as the history check above, just for pure-config child rows instead).
    await session.flush()
    await session.delete(policy)
    await record_audit(session, action="SOD_POLICY_DELETED", target_type="SOD_POLICY", target_id=policy_id, actor_user_id=actor_id, request_id=request_id)
    await session.commit()
    return None


async def hydrate_sod_exception(session: AsyncSession, exception: SodException) -> SodExceptionResponse:
    policy = await session.get(SodPolicy, exception.sod_policy_id)
    user = await session.get(User, exception.user_id)
    granter = await session.get(User, exception.granted_by) if exception.granted_by else None
    now = datetime.now(timezone.utc)
    expires = exception.expires_at if exception.expires_at.tzinfo else exception.expires_at.replace(tzinfo=timezone.utc)
    is_active = exception.revoked_at is None and expires > now
    return SodExceptionResponse(
        id=exception.id, sod_policy_id=exception.sod_policy_id, policy_name=policy.name if policy else None,
        user_id=exception.user_id, user_display_name=user.display_name if user else None, user_email=user.email if user else None,
        justification=exception.justification, granted_by=exception.granted_by, granted_by_display_name=granter.display_name if granter else None,
        expires_at=exception.expires_at, revoked_at=exception.revoked_at, is_active=is_active, created_at=exception.created_at,
    )


async def list_sod_exceptions(session: AsyncSession) -> list[SodExceptionResponse]:
    rows = list((await session.scalars(select(SodException).order_by(SodException.created_at.desc()))).all())
    return [await hydrate_sod_exception(session, row) for row in rows]


async def create_sod_exception(session: AsyncSession, data: SodExceptionCreate, actor_id: Optional[UUID], request_id: str) -> SodException:
    policy = await get_sod_policy(session, data.sod_policy_id)
    user = await session.get(User, data.user_id)
    if not user:
        raise AccessPilotError("USER_NOT_FOUND", "The user was not found.", 404)
    exception = SodException(sod_policy_id=data.sod_policy_id, user_id=data.user_id, justification=data.justification, granted_by=actor_id, expires_at=data.expires_at)
    session.add(exception)
    await session.flush()
    await record_audit(session, action="SOD_EXCEPTION_GRANTED", target_type="SOD_EXCEPTION", target_id=exception.id, actor_user_id=actor_id, request_id=request_id, metadata={"policy_name": policy.name, "user_id": str(data.user_id), "expires_at": data.expires_at.isoformat(), "justification": data.justification})
    await session.commit()
    await session.refresh(exception)
    return exception


async def _find_exception_granted_assignment(session: AsyncSession, exception: SodException) -> Optional[AccessAssignment]:
    """The one AccessAssignment (if any) this exception was granted specifically to unblock — found via the
    SodExceptionRequest it was granted from (SodExceptionRequest.sod_exception_id links back to it), not stored
    directly on SodException itself (which stays scoped to (policy, user) per its own docstring, not to a
    specific resource). Returns None for an exception granted through the older, untargeted
    POST /sod/exceptions path (no request behind it, nothing specific to automatically revoke).

    Real bug found via live testing and fixed here: matching purely on (user, resource_type, resource_id,
    app_role_external_id, status) with no ordering is ambiguous whenever an older, wholly unrelated ELIGIBLE/
    ACTIVE assignment for the exact same target already exists (e.g. a leftover row from an earlier, unrelated
    grant of the same group) — `.first()` on an unordered query can pick that stale row instead of the one this
    exception actually covers, silently revoking the wrong assignment. `create_assignment()` always creates the
    real assignment at essentially the same instant as the request that led to it, so filtering to
    `created_at >= request.created_at` and taking the EARLIEST such match deterministically picks the one this
    specific grant produced, never an older coincidental match."""
    request = (await session.execute(select(SodExceptionRequest).where(SodExceptionRequest.sod_exception_id == exception.id))).scalars().first()
    if request is None:
        return None
    conditions = [
        AccessAssignment.user_id == request.user_id,
        AccessAssignment.resource_type == request.resource_type,
        AccessAssignment.resource_id == request.resource_id,
        AccessAssignment.status.in_(("ELIGIBLE", "ACTIVE")),
        # A small grace window, not an exact >=, because the request and the assignment created_assignment()
        # produces from it are timestamped independently a moment apart — comparing them exactly risks a false
        # negative from ordinary clock/storage-precision jitter between the two writes.
        AccessAssignment.created_at >= request.created_at - timedelta(seconds=5),
        AccessAssignment.app_role_external_id == request.app_role_external_id if request.app_role_external_id else AccessAssignment.app_role_external_id.is_(None),
    ]
    return (await session.scalars(select(AccessAssignment).where(*conditions).order_by(AccessAssignment.created_at.asc()))).first()


async def _revoke_assignment_for_lapsed_exception(session: AsyncSession, assignment: AccessAssignment, policy_name: str, reason_phrase: str, actor_id: Optional[UUID], request_id: str) -> bool:
    """Shared by a manual exception revoke and the expiry-driven background worker (workers/sod_expiry.py): ends
    the specific ELIGIBLE-or-ACTIVE assignment an SoD exception was covering, now that the exception is gone —
    whichever of those two statuses it's currently in, per the explicit ask that it "doesn't matter". Mirrors
    revoke_assignment()'s ACTIVE-vs-not branching (assignments.py) but is its own function since this can run
    with no live human actor at all (the background worker) and needs SoD-specific wording instead of the
    generic Admin-revoke notification. Returns False (leaving the assignment untouched, to retry next poll) only
    if a real ACTIVE grant's removal genuinely fails."""
    if assignment.status == "ACTIVE":
        removed = await revoke_provider_access(session, assignment)
        if not removed:
            await record_audit(session, action="ASSIGNMENT_REVOKED", target_type="ASSIGNMENT", target_id=assignment.id, provider_id=assignment.provider_id, actor_user_id=actor_id, request_id=request_id, result="FAILURE", metadata={"reason": "SOD_EXCEPTION_LAPSED"})
            await session.commit()
            return False
    assignment.status = "REVOKED"
    assignment.revoked_at = datetime.now(timezone.utc)
    await record_audit(session, action="ASSIGNMENT_REVOKED", target_type="ASSIGNMENT", target_id=assignment.id, provider_id=assignment.provider_id, actor_user_id=actor_id, request_id=request_id, metadata={"reason": "SOD_EXCEPTION_LAPSED", "detail": reason_phrase})
    await create_notification(session, assignment.user_id, "SOD_EXCEPTION_LAPSED", f"Your access was automatically revoked because the SoD exception covering \"{policy_name}\" {reason_phrase}.", link="/my-access")
    await session.commit()
    return True


async def revoke_lapsed_sod_exceptions(session: AsyncSession) -> int:
    """Background-worker entry point (see workers/sod_expiry.py, polled every 60s): for every exception whose
    expires_at has already passed and that was never explicitly revoked (that path is handled synchronously by
    revoke_sod_exception itself, below), ends the specific ELIGIBLE/ACTIVE assignment it was granted to cover, if
    any. Naturally idempotent with no extra tracking column needed — once revoked, the assignment no longer
    matches ELIGIBLE/ACTIVE, so the next poll finds nothing left to do for that exception. An exception granted
    via the older, untargeted POST /sod/exceptions path (no linked request) has nothing specific to revoke and is
    silently skipped, same as it always was under the notify-only design."""
    now = datetime.now(timezone.utc)
    lapsed = list((await session.scalars(select(SodException).where(SodException.revoked_at.is_(None), SodException.expires_at <= now))).all())
    revoked_count = 0
    for exception in lapsed:
        assignment = await _find_exception_granted_assignment(session, exception)
        if assignment is None:
            continue
        policy = await session.get(SodPolicy, exception.sod_policy_id)
        request_id = f"sod-expiry-worker-{exception.id}"
        if await _revoke_assignment_for_lapsed_exception(session, assignment, policy.name if policy else "this policy", "expired", None, request_id):
            revoked_count += 1
    return revoked_count


async def revoke_sod_exception(session: AsyncSession, exception_id: UUID, actor_id: Optional[UUID], request_id: str) -> None:
    exception = await session.get(SodException, exception_id)
    if exception is None:
        raise AccessPilotError("SOD_EXCEPTION_NOT_FOUND", "The exception was not found.", 404)
    if exception.revoked_at is not None:
        raise AccessPilotError("REQUEST_ALREADY_PROCESSED", "This exception has already been revoked.", 409)
    exception.revoked_at = datetime.now(timezone.utc)
    await record_audit(session, action="SOD_EXCEPTION_REVOKED", target_type="SOD_EXCEPTION", target_id=exception.id, actor_user_id=actor_id, request_id=request_id)
    await session.commit()

    # Revoking the risk acceptance must also end the specific access it was covering — otherwise "Revoke" would
    # only block *future* grants while the one it already let through keeps running untouched indefinitely.
    assignment = await _find_exception_granted_assignment(session, exception)
    if assignment is not None:
        policy = await session.get(SodPolicy, exception.sod_policy_id)
        await _revoke_assignment_for_lapsed_exception(session, assignment, policy.name if policy else "this policy", "was revoked", actor_id, request_id)


async def hydrate_sod_exception_request(session: AsyncSession, exception_request: SodExceptionRequest) -> SodExceptionRequestResponse:
    policy = await session.get(SodPolicy, exception_request.sod_policy_id)
    user = await session.get(User, exception_request.user_id)
    requester = await session.get(User, exception_request.requested_by) if exception_request.requested_by else None
    decider = await session.get(User, exception_request.decided_by) if exception_request.decided_by else None
    approver = await session.get(User, exception_request.approver_id) if exception_request.approver_id else None
    try:
        _, resource_name, _ = await _resolve_target(session, exception_request.resource_type, exception_request.resource_id)
    except AccessPilotError:
        resource_name = None
    if exception_request.resource_type == "APPLICATION" and exception_request.app_role_external_id and resource_name:
        application = await session.get(Application, exception_request.resource_id)
        role_name = _app_role_name(application, exception_request.app_role_external_id)
        if role_name:
            resource_name = f"{resource_name} — {role_name}"
    return SodExceptionRequestResponse(
        id=exception_request.id, sod_policy_id=exception_request.sod_policy_id, policy_name=policy.name if policy else None,
        user_id=exception_request.user_id, user_display_name=user.display_name if user else None,
        requested_by=exception_request.requested_by, requested_by_display_name=requester.display_name if requester else None,
        justification=exception_request.justification, resource_type=exception_request.resource_type, resource_id=exception_request.resource_id,
        resource_display_name=resource_name, app_role_external_id=exception_request.app_role_external_id,
        approver_id=exception_request.approver_id, approver_display_name=approver.display_name if approver else None,
        assignment_type=exception_request.assignment_type, expiration_time=exception_request.expiration_time,
        status=exception_request.status, decided_by=exception_request.decided_by, decided_by_display_name=decider.display_name if decider else None,
        decided_at=exception_request.decided_at, denial_reason=exception_request.denial_reason, sod_exception_id=exception_request.sod_exception_id,
        created_at=exception_request.created_at,
    )


async def list_sod_exception_requests(session: AsyncSession) -> list[SodExceptionRequestResponse]:
    rows = list((await session.scalars(select(SodExceptionRequest).order_by(SodExceptionRequest.created_at.desc()))).all())
    return [await hydrate_sod_exception_request(session, row) for row in rows]


async def create_sod_exception_request(session: AsyncSession, data: SodExceptionRequestCreate, actor_id: Optional[UUID], request_id: str) -> SodExceptionRequest:
    """The bridge between a blocked assignment attempt and the exception workflow — an Admin who hits a
    SOD_CONFLICT can ask the SoDAdmin to review it instead of being stuck, or unilaterally using override_sod.
    Fires an EXCEPTION_REQUESTED notification immediately (not via reconcile_sod_notifications, which is for
    diffing computed state against reality — a request is a discrete event, not a derived condition)."""
    policy = await get_sod_policy(session, data.sod_policy_id)
    user = await session.get(User, data.user_id)
    if not user:
        raise AccessPilotError("USER_NOT_FOUND", "The user was not found.", 404)
    exception_request = SodExceptionRequest(
        sod_policy_id=data.sod_policy_id, user_id=data.user_id, requested_by=actor_id, justification=data.justification,
        resource_type=data.resource_type, resource_id=data.resource_id, app_role_external_id=data.app_role_external_id,
        approver_id=data.approver_id, fallback_approver_id=data.fallback_approver_id, fallback_unlock_hours=data.fallback_unlock_hours,
        assignment_type=data.assignment_type, expiration_time=data.expiration_time,
    )
    session.add(exception_request)
    await session.flush()
    settings = await get_sod_notification_settings(session)
    if settings.notify_on_exception_requested:
        requester = await session.get(User, actor_id) if actor_id else None
        session.add(SodNotification(
            notification_type="EXCEPTION_REQUESTED", sod_policy_id=data.sod_policy_id, user_id=data.user_id, sod_exception_request_id=exception_request.id,
            message=f"{requester.display_name if requester else 'An admin'} requested an SoD exception for {user.display_name} on \"{policy.name}\".",
        ))
    await record_audit(session, action="SOD_EXCEPTION_REQUESTED", target_type="SOD_EXCEPTION_REQUEST", target_id=exception_request.id, actor_user_id=actor_id, request_id=request_id, metadata={"policy_name": policy.name, "user_id": str(data.user_id), "justification": data.justification})
    await session.commit()
    await session.refresh(exception_request)
    return exception_request


async def grant_sod_exception_request(session: AsyncSession, exception_request_id: UUID, data: SodExceptionRequestGrant, actor_id: Optional[UUID], request_id: str) -> SodExceptionRequest:
    exception_request = await session.get(SodExceptionRequest, exception_request_id)
    if exception_request is None:
        raise AccessPilotError("SOD_EXCEPTION_REQUEST_NOT_FOUND", "The exception request was not found.", 404)
    if exception_request.status != "PENDING":
        raise AccessPilotError("REQUEST_ALREADY_PROCESSED", "This request has already been decided.", 409)
    exception = SodException(sod_policy_id=exception_request.sod_policy_id, user_id=exception_request.user_id, justification=exception_request.justification, granted_by=actor_id, expires_at=data.expires_at)
    session.add(exception)
    await session.flush()
    exception_request.status = "GRANTED"
    exception_request.decided_by = actor_id
    exception_request.decided_at = datetime.now(timezone.utc)
    exception_request.sod_exception_id = exception.id
    open_notification = (await session.execute(select(SodNotification).where(SodNotification.sod_exception_request_id == exception_request.id, SodNotification.resolved_at.is_(None)))).scalars().first()
    if open_notification is not None:
        open_notification.resolved_at = datetime.now(timezone.utc)
    policy = await session.get(SodPolicy, exception_request.sod_policy_id)
    target_user = await session.get(User, exception_request.user_id)
    requester = await session.get(User, exception_request.requested_by) if exception_request.requested_by else None
    target_label = target_user.display_name if target_user else "the user"
    try:
        _, resource_name, _ = await _resolve_target(session, exception_request.resource_type, exception_request.resource_id)
    except AccessPilotError:
        resource_name = exception_request.resource_type.title()
    if exception_request.resource_type == "APPLICATION" and exception_request.app_role_external_id:
        application = await session.get(Application, exception_request.resource_id)
        role_name = _app_role_name(application, exception_request.app_role_external_id)
        if role_name:
            resource_name = f"{resource_name} — {role_name}"

    # Granting recreates the ORIGINAL blocked attempt exactly as it would have gone through — including routing
    # through the same approver, if one was configured — by calling create_assignment() itself, rather than a
    # bespoke insert. That gives this path the same approval-required/no-approver branching, notifications, and
    # audit trail as any other admin-initiated assignment, for free. check_sod_at_creation=True so it still runs
    # its own SoD check (finding the exception just added above, visible within this same transaction) — needed
    # because the original attempt could have been blocked by MULTIPLE conflicting policies at once (the
    # frontend files one exception request per conflicting policy); if another, still-ungranted one still
    # applies, create_assignment() itself raises SOD_CONFLICT and nothing is created.
    outcome = "FAILED"
    failure_detail: Optional[str] = None
    if requester is not None:
        payload = AssignmentCreate(
            user_id=exception_request.user_id, resource_type=exception_request.resource_type, resource_id=exception_request.resource_id,
            app_role_external_id=exception_request.app_role_external_id, assignment_type=exception_request.assignment_type,
            expiration_time=exception_request.expiration_time, approver_id=exception_request.approver_id,
            fallback_approver_id=exception_request.fallback_approver_id, fallback_unlock_hours=exception_request.fallback_unlock_hours,
            justification=exception_request.justification,
        )
        try:
            await create_assignment(session, payload, requester.external_id, request_id, check_sod_at_creation=True)
            outcome = "CREATED"
        except AccessPilotError as exc:
            outcome = "BLOCKED" if exc.code == "SOD_CONFLICT" else "FAILED"
            failure_detail = exc.message
    else:
        failure_detail = "the original requester could not be identified"

    if outcome == "CREATED":
        status_label = f"eligible access to {resource_name}" if exception_request.approver_id is None else f"a pending-approval request for {resource_name} routed to the configured approver"
        requester_message = f"Your SoD exception request on \"{policy.name if policy else 'this policy'}\" was granted — {target_label} now has {status_label}."
    elif outcome == "BLOCKED":
        requester_message = f"Your SoD exception request on \"{policy.name if policy else 'this policy'}\" was granted, but {target_label} is still blocked by another Separation-of-Duties policy — request an exception for that one too, or retry once it's resolved."
    else:
        requester_message = f"Your SoD exception request on \"{policy.name if policy else 'this policy'}\" was granted, but the assignment could not be created automatically ({failure_detail}) — please create it manually."

    await record_audit(session, action="SOD_EXCEPTION_GRANTED", target_type="SOD_EXCEPTION", target_id=exception.id, actor_user_id=actor_id, request_id=request_id, metadata={"policy_name": policy.name if policy else None, "user_id": str(exception_request.user_id), "expires_at": data.expires_at.isoformat(), "justification": exception_request.justification, "from_request": True, "assignment_outcome": outcome})
    # Closes the loop back to whoever asked — general per-user notification (see services/notifications.py),
    # not the SoD-only log, since this is addressed to one specific admin, not "anyone with SOD_READ."
    if exception_request.requested_by is not None:
        await create_notification(session, exception_request.requested_by, "EXCEPTION_REQUEST_GRANTED", requester_message, link="/admin/assignments")
    await session.commit()
    await session.refresh(exception_request)
    return exception_request


async def deny_sod_exception_request(session: AsyncSession, exception_request_id: UUID, data: SodExceptionRequestDeny, actor_id: Optional[UUID], request_id: str) -> SodExceptionRequest:
    exception_request = await session.get(SodExceptionRequest, exception_request_id)
    if exception_request is None:
        raise AccessPilotError("SOD_EXCEPTION_REQUEST_NOT_FOUND", "The exception request was not found.", 404)
    if exception_request.status != "PENDING":
        raise AccessPilotError("REQUEST_ALREADY_PROCESSED", "This request has already been decided.", 409)
    exception_request.status = "DENIED"
    exception_request.decided_by = actor_id
    exception_request.decided_at = datetime.now(timezone.utc)
    exception_request.denial_reason = data.reason
    open_notification = (await session.execute(select(SodNotification).where(SodNotification.sod_exception_request_id == exception_request.id, SodNotification.resolved_at.is_(None)))).scalars().first()
    if open_notification is not None:
        open_notification.resolved_at = datetime.now(timezone.utc)
    policy = await session.get(SodPolicy, exception_request.sod_policy_id)
    await record_audit(session, action="SOD_EXCEPTION_REQUEST_DENIED", target_type="SOD_EXCEPTION_REQUEST", target_id=exception_request.id, actor_user_id=actor_id, request_id=request_id, metadata={"reason": data.reason})
    if exception_request.requested_by is not None:
        reason_suffix = f" Reason: {data.reason}" if data.reason else ""
        await create_notification(session, exception_request.requested_by, "EXCEPTION_REQUEST_DENIED", f"Your SoD exception request on \"{policy.name if policy else 'this policy'}\" was denied.{reason_suffix}", link="/admin/assignments")
    await session.commit()
    await session.refresh(exception_request)
    return exception_request


_SOD_DIRECT_ACTIONS = ("SOD_POLICY_CREATED", "SOD_POLICY_UPDATED", "SOD_POLICY_DELETED", "SOD_ADMIN_GRANTED", "SOD_ADMIN_REVOKED", "SOD_EXCEPTION_GRANTED", "SOD_EXCEPTION_REVOKED", "SOD_EXCEPTION_REQUESTED", "SOD_EXCEPTION_REQUEST_DENIED")
_SOD_CANDIDATE_ACTIONS = ("ASSIGNMENT_CREATE_BLOCKED", "ASSIGNMENT_ACTIVATED")


def _is_sod_relevant(entry: AuditLog) -> bool:
    if entry.action in _SOD_DIRECT_ACTIONS:
        return True
    if entry.action in _SOD_CANDIDATE_ACTIONS and entry.metadata_json:
        return entry.metadata_json.get("reason") == "SOD_CONFLICT" or bool(entry.metadata_json.get("sod_override"))
    return False


async def get_sod_activity(session: AsyncSession, limit: int = 100) -> list[tuple[AuditLog, dict]]:
    """Every SoD-relevant audit entry, newest first — rule create/edit/delete, SoDAdmin roster grant/revoke, and
    the subset of assignment activity that's actually about SoD (a blocked grant, or an activation that carried
    an override) out of the much larger pool of ordinary, unrelated assignment activity. SQL-filters by action
    name first (portable across SQLite/Postgres, no JSON operators needed); the metadata-based ASSIGNMENT_* check
    happens in Python, since only some entries with those action names are SoD-relevant."""
    from app.services.audit_read import _resolve_target_user
    all_actions = _SOD_DIRECT_ACTIONS + _SOD_CANDIDATE_ACTIONS
    candidates = list((await session.scalars(select(AuditLog).where(AuditLog.action.in_(all_actions)).order_by(AuditLog.timestamp.desc()).limit(limit * 5))).all())
    entries = [entry for entry in candidates if _is_sod_relevant(entry)][:limit]
    hydrated: list[tuple[AuditLog, dict]] = []
    for entry in entries:
        actor_name = None
        if entry.actor_user_id:
            actor = await session.get(User, entry.actor_user_id)
            actor_name = actor.display_name if actor else None
        target_user = await _resolve_target_user(session, entry)
        hydrated.append((entry, {"actor_display_name": actor_name, "target_user_display_name": target_user.display_name if target_user else None, "target_user_email": target_user.email if target_user else None}))
    return hydrated


async def get_sod_notification_settings(session: AsyncSession) -> SodNotificationSettings:
    """Singleton — creates the one row with its defaults (both notification types ON, 7-day exception-expiry
    warning) on first ever read, matching the SecuritySettings/BrandingSettings get-or-create convention."""
    settings = (await session.execute(select(SodNotificationSettings))).scalars().first()
    if settings is None:
        settings = SodNotificationSettings()
        session.add(settings)
        await session.commit()
        await session.refresh(settings)
    return settings


async def update_sod_notification_settings(session: AsyncSession, data: SodNotificationSettingsUpdateRequest) -> SodNotificationSettings:
    settings = await get_sod_notification_settings(session)
    settings.notify_on_new_violation = data.notify_on_new_violation
    settings.notify_on_exception_expiring = data.notify_on_exception_expiring
    settings.exception_expiring_warning_days = data.exception_expiring_warning_days
    settings.notify_on_exception_requested = data.notify_on_exception_requested
    settings.cooldown_enabled = data.cooldown_enabled
    settings.cooldown_hours = data.cooldown_hours
    await session.commit()
    await session.refresh(settings)
    return settings


async def reconcile_sod_notifications(session: AsyncSession) -> None:
    """Creates/resolves SodNotification rows to match current reality. Deliberately NOT wired into
    get_sod_violations() itself (which the Dashboard widget and the SoD page both call) — that would double the
    cost of the already-expensive per-user Graph scan for ROLE/APPLICATION rules (§11 of the SoD doc) on every
    view of either page. Instead this runs once, opportunistically, whenever GET /sod/notifications is called —
    no separate scheduler/background worker needed, consistent with the rest of this engine's "live, on-read
    compute" philosophy; it just means notifications are only as fresh as the last time someone (or the Bell
    icon's unread-count fetch) actually checked."""
    settings = await get_sod_notification_settings(session)
    now = datetime.now(timezone.utc)
    # Computed at most once, reused by both the NEW_VIOLATION block and the new EXCEPTION_EXPIRED block below —
    # calling get_sod_violations() twice in the same pass would double the expensive per-user Graph scan cost
    # (§11 of the SoD doc) for ROLE/APPLICATION rules whenever both settings happen to be enabled together.
    violations: Optional[list[SodViolation]] = None

    if settings.notify_on_new_violation:
        violations = await get_sod_violations(session)
        open_pairs = {(v.policy_id, v.user_id) for v in violations}
        open_notifications = list((await session.scalars(select(SodNotification).where(SodNotification.notification_type == "NEW_VIOLATION", SodNotification.resolved_at.is_(None)))).all())
        already_notified_pairs: set[tuple[Optional[UUID], Optional[UUID]]] = set()
        for note in open_notifications:
            if (note.sod_policy_id, note.user_id) not in open_pairs:
                note.resolved_at = now
            else:
                already_notified_pairs.add((note.sod_policy_id, note.user_id))
        for violation in violations:
            if (violation.policy_id, violation.user_id) in already_notified_pairs:
                continue
            session.add(SodNotification(
                notification_type="NEW_VIOLATION", sod_policy_id=violation.policy_id, user_id=violation.user_id,
                message=f"{violation.user_display_name or violation.user_id} now holds both sides of \"{violation.policy_name}\".",
            ))
        await session.commit()

    if settings.notify_on_exception_expiring:
        threshold = now + timedelta(days=settings.exception_expiring_warning_days)
        expiring = list((await session.scalars(select(SodException).where(SodException.revoked_at.is_(None), SodException.expires_at > now, SodException.expires_at <= threshold))).all())
        expiring_ids = {exception.id for exception in expiring}
        open_notifications = list((await session.scalars(select(SodNotification).where(SodNotification.notification_type == "EXCEPTION_EXPIRING", SodNotification.resolved_at.is_(None)))).all())
        already_notified_ids = set()
        for note in open_notifications:
            if note.sod_exception_id not in expiring_ids:
                note.resolved_at = now
            else:
                already_notified_ids.add(note.sod_exception_id)
        for exception in expiring:
            if exception.id in already_notified_ids:
                continue
            policy = await session.get(SodPolicy, exception.sod_policy_id)
            user = await session.get(User, exception.user_id)
            session.add(SodNotification(
                notification_type="EXCEPTION_EXPIRING", sod_policy_id=exception.sod_policy_id, user_id=exception.user_id, sod_exception_id=exception.id,
                message=f"The exception for {user.display_name if user else exception.user_id} on \"{policy.name if policy else 'a deleted policy'}\" expires {exception.expires_at.strftime('%Y-%m-%d %H:%M UTC')}.",
            ))
        await session.commit()

        # revoke_lapsed_sod_exceptions() (workers/sod_expiry.py, polled every 60s) already auto-revokes the
        # specific ELIGIBLE/ACTIVE assignment a request-based exception was granted to cover, the moment it
        # expires — so by the time this reconciliation pass runs, that case has normally already resolved itself
        # and won't show up in get_sod_violations() at all. This block only ever fires for the rarer case where
        # the conflict is STILL real despite the expiry: an exception granted through the older, untargeted
        # POST /sod/exceptions path (nothing specific to auto-revoke), or the narrow race window before the next
        # worker tick. Either way, someone must be told to look and decide by hand.
        if violations is None:
            violations = await get_sod_violations(session)
        open_pairs = {(v.policy_id, v.user_id) for v in violations}
        already_expired = list((await session.scalars(select(SodException).where(SodException.revoked_at.is_(None), SodException.expires_at <= now))).all())
        still_open_expired_ids: set[UUID] = set()
        for exception in already_expired:
            if (exception.sod_policy_id, exception.user_id) not in open_pairs:
                continue
            if await get_active_sod_exception(session, exception.sod_policy_id, exception.user_id) is not None:
                continue  # a fresh exception already covers this policy/user pair — nothing to flag
            still_open_expired_ids.add(exception.id)
        open_expired_notifications = list((await session.scalars(select(SodNotification).where(SodNotification.notification_type == "EXCEPTION_EXPIRED", SodNotification.resolved_at.is_(None)))).all())
        already_notified_expired_ids = set()
        for note in open_expired_notifications:
            if note.sod_exception_id not in still_open_expired_ids:
                note.resolved_at = now
            else:
                already_notified_expired_ids.add(note.sod_exception_id)
        for exception in already_expired:
            if exception.id not in still_open_expired_ids or exception.id in already_notified_expired_ids:
                continue
            policy = await session.get(SodPolicy, exception.sod_policy_id)
            user = await session.get(User, exception.user_id)
            session.add(SodNotification(
                notification_type="EXCEPTION_EXPIRED", sod_policy_id=exception.sod_policy_id, user_id=exception.user_id, sod_exception_id=exception.id,
                message=f"The exception for {user.display_name if user else exception.user_id} on \"{policy.name if policy else 'a deleted policy'}\" expired {exception.expires_at.strftime('%Y-%m-%d %H:%M UTC')} and the conflict is still active — this one wasn't automatically revoked. Review it and either revoke one side or grant a new exception.",
            ))
        await session.commit()


async def hydrate_sod_notification(session: AsyncSession, notification: SodNotification) -> SodNotificationResponse:
    policy = await session.get(SodPolicy, notification.sod_policy_id) if notification.sod_policy_id else None
    user = await session.get(User, notification.user_id) if notification.user_id else None
    return SodNotificationResponse(
        id=notification.id, notification_type=notification.notification_type, sod_policy_id=notification.sod_policy_id,
        policy_name=policy.name if policy else None, user_id=notification.user_id, user_display_name=user.display_name if user else None,
        message=notification.message, read_at=notification.read_at, resolved_at=notification.resolved_at, created_at=notification.created_at,
    )


async def list_sod_notifications(session: AsyncSession, reconcile: bool = True) -> list[SodNotificationResponse]:
    if reconcile:
        await reconcile_sod_notifications(session)
    rows = list((await session.scalars(select(SodNotification).order_by(SodNotification.created_at.desc()))).all())
    return [await hydrate_sod_notification(session, row) for row in rows]


async def mark_sod_notification_read(session: AsyncSession, notification_id: UUID) -> None:
    notification = await session.get(SodNotification, notification_id)
    if notification is None:
        raise AccessPilotError("SOD_NOTIFICATION_NOT_FOUND", "The notification was not found.", 404)
    if notification.read_at is None:
        notification.read_at = datetime.now(timezone.utc)
        await session.commit()


async def mark_all_sod_notifications_read(session: AsyncSession) -> None:
    unread = list((await session.scalars(select(SodNotification).where(SodNotification.read_at.is_(None)))).all())
    now = datetime.now(timezone.utc)
    for notification in unread:
        notification.read_at = now
    if unread:
        await session.commit()
