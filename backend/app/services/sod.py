from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AccessPilotError
from app.models import AccessAssignment, AccessPackage, AccessPackageItem, Application, AuditLog, IdentityProvider, Role, SodAdmin, SodException, SodNotification, SodNotificationSettings, SodPolicy, SodPolicyEntity, User, UserGroup
from app.providers.entra import EntraProvider
from app.providers.graph_client import GraphError
from app.schemas.sod import SodAdminResponse, SodExceptionCreate, SodExceptionResponse, SodNotificationResponse, SodNotificationSettingsUpdateRequest, SodPolicyCreate, SodPolicyEntityResponse, SodPolicyResponse, SodPolicyUpdate, SodViolation, SodViolationHolding
from app.services.assignments import _app_role_name, _resolve_target
from app.services.audit import record_audit
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
    1. AccessPilot-tracked: an ACTIVE AccessAssignment row (as before).
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
    stmt = select(AccessAssignment).where(AccessAssignment.user_id == user_id, AccessAssignment.status == "ACTIVE", or_(*conditions))
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
    if not tuples:
        return []
    conditions = [_tuple_condition(*t) for t in tuples]
    stmt = select(AccessAssignment).where(AccessAssignment.status == "ACTIVE", or_(*conditions))
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


async def check_sod_conflicts(session: AsyncSession, user_id: UUID, resource_type: str, resource_id: UUID, app_role_external_id: Optional[str] = None) -> list[SodPolicy]:
    """For every ACTIVE SoD policy, does the given entitlement land on side A or B? If so, does this user already
    hold (an ACTIVE assignment matching) anything on the OPPOSITE side? Runs inside the caller's live transaction
    so it correctly sees rows already committed earlier in the same request (needed for the intra-package case,
    where item #2 of the same package must see item #1's just-committed assignment). A policy with an active,
    unexpired SodException for this exact (policy, user) pair is deliberately excluded from the returned
    conflicts — the risk has already been formally reviewed and accepted, so it must not keep blocking every
    subsequent grant attempt until the acceptance itself expires or is revoked."""
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
        if opposite_tuples and await _user_holds_any(session, user_id, opposite_tuples):
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


async def delete_sod_policy(session: AsyncSession, policy_id: UUID, actor_id: Optional[UUID], request_id: str) -> None:
    policy = await get_sod_policy(session, policy_id)
    for entity in list((await session.scalars(select(SodPolicyEntity).where(SodPolicyEntity.sod_policy_id == policy_id))).all()):
        await session.delete(entity)
    await session.delete(policy)
    await record_audit(session, action="SOD_POLICY_DELETED", target_type="SOD_POLICY", target_id=policy_id, actor_user_id=actor_id, request_id=request_id)
    await session.commit()


async def hydrate_sod_admin(session: AsyncSession, admin: SodAdmin) -> SodAdminResponse:
    user = await session.get(User, admin.user_id)
    granter = await session.get(User, admin.granted_by) if admin.granted_by else None
    return SodAdminResponse(id=admin.id, user_id=admin.user_id, user_display_name=user.display_name if user else None, user_email=user.email if user else None, granted_by=admin.granted_by, granted_by_display_name=granter.display_name if granter else None, created_at=admin.created_at)


async def list_sod_admins(session: AsyncSession) -> list[SodAdminResponse]:
    rows = list((await session.scalars(select(SodAdmin).order_by(SodAdmin.created_at.desc()))).all())
    return [await hydrate_sod_admin(session, row) for row in rows]


async def add_sod_admin(session: AsyncSession, user_id: UUID, granted_by: Optional[UUID], request_id: str) -> SodAdmin:
    user = await session.get(User, user_id)
    if not user:
        raise AccessPilotError("USER_NOT_FOUND", "The user was not found.", 404)
    existing = (await session.execute(select(SodAdmin).where(SodAdmin.user_id == user_id))).scalars().first()
    if existing is not None:
        raise AccessPilotError("ALREADY_SOD_ADMIN", "This user is already an SoD Admin.", 409)
    admin = SodAdmin(user_id=user_id, granted_by=granted_by)
    session.add(admin)
    await record_audit(session, action="SOD_ADMIN_GRANTED", target_type="USER", target_id=user_id, actor_user_id=granted_by, request_id=request_id)
    await session.commit()
    await session.refresh(admin)
    return admin


async def remove_sod_admin(session: AsyncSession, user_id: UUID, actor_id: Optional[UUID], request_id: str) -> None:
    admin = (await session.execute(select(SodAdmin).where(SodAdmin.user_id == user_id))).scalars().first()
    if admin is None:
        raise AccessPilotError("NOT_SOD_ADMIN", "This user is not an SoD Admin.", 404)
    await session.delete(admin)
    await record_audit(session, action="SOD_ADMIN_REVOKED", target_type="USER", target_id=user_id, actor_user_id=actor_id, request_id=request_id)
    await session.commit()


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


async def revoke_sod_exception(session: AsyncSession, exception_id: UUID, actor_id: Optional[UUID], request_id: str) -> None:
    exception = await session.get(SodException, exception_id)
    if exception is None:
        raise AccessPilotError("SOD_EXCEPTION_NOT_FOUND", "The exception was not found.", 404)
    if exception.revoked_at is not None:
        raise AccessPilotError("REQUEST_ALREADY_PROCESSED", "This exception has already been revoked.", 409)
    exception.revoked_at = datetime.now(timezone.utc)
    await record_audit(session, action="SOD_EXCEPTION_REVOKED", target_type="SOD_EXCEPTION", target_id=exception.id, actor_user_id=actor_id, request_id=request_id)
    await session.commit()


_SOD_DIRECT_ACTIONS = ("SOD_POLICY_CREATED", "SOD_POLICY_UPDATED", "SOD_POLICY_DELETED", "SOD_ADMIN_GRANTED", "SOD_ADMIN_REVOKED", "SOD_EXCEPTION_GRANTED", "SOD_EXCEPTION_REVOKED")
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


async def is_sod_admin(session: AsyncSession, directory_object_id: str) -> bool:
    """The request-time hook used by security/auth.py to fold AccessPilot.SoDAdmin into a caller's effective
    roles — a Break-Glass subject (e.g. "breakglass:<id>") never matches a real external_id, so this correctly
    returns False for those sessions with no special-casing needed."""
    stmt = select(SodAdmin.id).join(User, SodAdmin.user_id == User.id).where(User.external_id == directory_object_id)
    return (await session.execute(stmt)).first() is not None


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
