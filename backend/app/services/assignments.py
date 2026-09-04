from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AccessPilotError
from app.models import AccessAssignment, AccessPackage, AccessPackageAssignment, Application, Group, IdentityProvider, Role, User, UserGroup
from app.providers.graph_client import GraphError
from app.schemas.assignments import AssignmentResponse
from app.services.audit import record_audit
from app.services.notifications import create_notification
from app.services.provider_configuration import _connector


def to_response(assignment: AccessAssignment, hydrated: dict) -> AssignmentResponse:
    return AssignmentResponse(
        id=assignment.id, user_id=assignment.user_id, user_display_name=hydrated.get("user_display_name"),
        resource_type=assignment.resource_type, resource_id=assignment.resource_id, resource_display_name=hydrated.get("resource_display_name"),
        app_role_external_id=assignment.app_role_external_id,
        assignment_type=assignment.assignment_type, status=assignment.status, start_time=assignment.start_time, expiration_time=assignment.expiration_time,
        justification=assignment.justification, requested_by=assignment.requested_by, approved_by=assignment.approved_by,
        fallback_approver_id=assignment.fallback_approver_id, fallback_unlock_at=assignment.fallback_unlock_at,
        bypass_activation=assignment.bypass_activation,
        activated_at=assignment.activated_at, revoked_at=assignment.revoked_at, created_at=assignment.created_at,
        package_name=hydrated.get("package_name"), sod_exception_expires_at=hydrated.get("sod_exception_expires_at"),
    )


def _app_role_name(application: Optional[Application], app_role_external_id: Optional[str]) -> Optional[str]:
    if not application or not application.app_roles or not app_role_external_id:
        return None
    return next((role.get("name") for role in application.app_roles if role.get("id") == app_role_external_id), None)


async def _resolve_target(session: AsyncSession, resource_type: str, resource_id: UUID) -> tuple[UUID, str, str]:
    """Returns (provider_id, display_name, external_id) for the target group/role/application, raising 404 if it doesn't exist."""
    if resource_type == "GROUP":
        group = await session.get(Group, resource_id)
        if not group:
            raise AccessPilotError("GROUP_NOT_FOUND", "The group was not found.", 404)
        return group.provider_id, group.name, group.external_id
    if resource_type == "APPLICATION":
        application = await session.get(Application, resource_id)
        if not application:
            raise AccessPilotError("APPLICATION_NOT_FOUND", "The application was not found.", 404)
        return application.provider_id, application.name, application.external_id
    role = await session.get(Role, resource_id)
    if not role:
        raise AccessPilotError("ROLE_NOT_FOUND", "The role was not found.", 404)
    return role.provider_id, role.name, role.external_id


async def _resolve_internal_user_id(session: AsyncSession, external_subject: str) -> Optional[UUID]:
    user = (await session.execute(select(User).where(User.external_id == external_subject))).scalars().first()
    return user.id if user else None


async def hydrate_display_fields(session: AsyncSession, assignment: AccessAssignment) -> dict:
    user = await session.get(User, assignment.user_id)
    _, resource_name, _ = await _resolve_target(session, assignment.resource_type, assignment.resource_id)
    if assignment.resource_type == "APPLICATION" and assignment.app_role_external_id:
        application = await session.get(Application, assignment.resource_id)
        role_name = _app_role_name(application, assignment.app_role_external_id)
        if role_name:
            resource_name = f"{resource_name} — {role_name}"
    package_name = (await session.execute(
        select(AccessPackage.name).join(AccessPackageAssignment, AccessPackageAssignment.package_id == AccessPackage.id).where(AccessPackageAssignment.assignment_id == assignment.id)
    )).scalar_one_or_none()
    # Local import to avoid a circular import at module level — sod.py itself imports from this module directly
    # (create_assignment, revoke_provider_access), so the reverse direction has to stay function-scoped.
    from app.services.sod import get_sod_exception_covering_assignment
    covering_exception = await get_sod_exception_covering_assignment(session, assignment)
    return {"user_display_name": user.display_name if user else None, "resource_display_name": resource_name, "package_name": package_name, "sod_exception_expires_at": covering_exception.expires_at if covering_exception else None}


async def _grant_provider_access(session: AsyncSession, provider_id: UUID, resource_type: str, target_external_id: str, user_external_id: str, app_role_external_id: Optional[str] = None) -> None:
    """Performs the real Entra/Graph mutation. Raises AccessPilotError if it fails — callers must not mark ACTIVE on failure."""
    provider = await session.get(IdentityProvider, provider_id)
    if not provider:
        raise AccessPilotError("PROVIDER_NOT_FOUND", "The identity provider for this assignment was not found.", 404)
    connector = _connector(provider)
    request = {"resource_type": resource_type, "target_external_id": target_external_id, "user_external_id": user_external_id}
    if app_role_external_id:
        request["app_role_external_id"] = app_role_external_id
    try:
        granted = await connector.activate_assignment(request)
    except (GraphError, NotImplementedError, ValueError) as exc:
        code = getattr(exc, "code", "PROVIDER_UNAVAILABLE")
        message = getattr(exc, "message", str(exc)) or "The provider operation failed."
        status_code = getattr(exc, "status_code", 502)
        raise AccessPilotError(code, message, status_code) from exc
    if not granted:
        raise AccessPilotError("PROVIDER_UNAVAILABLE", "The provider did not confirm the access grant.", 502)


async def _record_local_group_membership(session: AsyncSession, user_id: UUID, group_id: UUID) -> None:
    """Real Entra/Graph group grants only reached the local user_groups table via the next periodic directory
    sync — which can lag by a full sync interval, or never run at all if scheduling was never configured (a real
    gap already hit once this session). Anything checking user_groups for something AccessPilot itself JUST
    granted (e.g. group-based Access Package eligibility, checked in list_requestable_packages) would otherwise
    wrongly see no membership until that next sync — confirmed live: several ACTIVE group assignments, some days
    old, still had zero corresponding user_groups rows. Insert the row immediately on a successful real grant
    instead; the sync worker's own upsert-if-missing logic is unaffected by a row already being there."""
    existing = (await session.execute(select(UserGroup).where(UserGroup.user_id == user_id, UserGroup.group_id == group_id))).scalars().first()
    if existing is None:
        session.add(UserGroup(user_id=user_id, group_id=group_id, source="ASSIGNMENT"))


async def _remove_local_group_membership(session: AsyncSession, user_id: UUID, group_id: UUID) -> None:
    """Mirror of _record_local_group_membership for the revoke path — otherwise a revoked group assignment would
    leave the user still locally recorded as a member (and still group-eligible for packages) until the next sync
    reconciles it away."""
    existing = (await session.execute(select(UserGroup).where(UserGroup.user_id == user_id, UserGroup.group_id == group_id))).scalars().first()
    if existing is not None:
        await session.delete(existing)


async def revoke_provider_access(session: AsyncSession, assignment: AccessAssignment) -> bool:
    """Performs the real Entra/Graph removal for an assignment. Returns True on success, False on failure (caller decides retry policy)."""
    provider = await session.get(IdentityProvider, assignment.provider_id)
    if not provider:
        return False
    _, _, target_external_id = await _resolve_target(session, assignment.resource_type, assignment.resource_id)
    user = await session.get(User, assignment.user_id)
    if not user:
        return False
    connector = _connector(provider)
    request = {"resource_type": assignment.resource_type, "target_external_id": target_external_id, "user_external_id": user.external_id}
    if assignment.app_role_external_id:
        request["app_role_external_id"] = assignment.app_role_external_id
    try:
        revoked = bool(await connector.revoke_assignment(request))
    except (GraphError, NotImplementedError, ValueError):
        return False
    if revoked and assignment.resource_type == "GROUP":
        await _remove_local_group_membership(session, assignment.user_id, assignment.resource_id)
    return revoked


async def grant_provider_access_for_assignment(session: AsyncSession, assignment: AccessAssignment) -> bool:
    """Safe wrapper for background workers: grants real Entra/Graph access, returning True/False instead of raising."""
    _, _, target_external_id = await _resolve_target(session, assignment.resource_type, assignment.resource_id)
    user = await session.get(User, assignment.user_id)
    if not user:
        return False
    try:
        await _grant_provider_access(session, assignment.provider_id, assignment.resource_type, target_external_id, user.external_id, assignment.app_role_external_id)
    except AccessPilotError:
        return False
    if assignment.resource_type == "GROUP":
        await _record_local_group_membership(session, assignment.user_id, assignment.resource_id)
    return True


def _is_in_the_future(moment: Optional[datetime], now: datetime) -> bool:
    if moment is None:
        return False
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment > now


async def _supersede_existing_assignment(session: AsyncSession, *, user_id: UUID, resource_type: str, resource_id: UUID, app_role_external_id: Optional[str], provider_id: UUID, actor_id: Optional[UUID], request_id: str, exclude_id: Optional[UUID] = None) -> None:
    """If the user already has an active/scheduled assignment to this exact group/role/application+role, remove it
    first (real Entra removal if it was actually granted) and log the removal — a new assignment to the same target
    replaces it. For applications, a different role on the same app is treated as a distinct target, not a replace."""
    conditions = [
        AccessAssignment.user_id == user_id,
        AccessAssignment.resource_type == resource_type,
        AccessAssignment.resource_id == resource_id,
        AccessAssignment.status.in_(("ACTIVE", "SCHEDULED", "ELIGIBLE")),
    ]
    if exclude_id is not None:
        conditions.append(AccessAssignment.id != exclude_id)
    if resource_type == "APPLICATION":
        conditions.append(AccessAssignment.app_role_external_id == app_role_external_id)
    existing = (await session.execute(select(AccessAssignment).where(*conditions))).scalars().first()
    if existing is None:
        return
    if existing.status == "ACTIVE":
        removed = await revoke_provider_access(session, existing)
        if not removed:
            raise AccessPilotError("PROVIDER_UNAVAILABLE", "Could not remove the user's existing access before reassigning. Please try again.", 502)
    existing.status = "REVOKED"
    existing.revoked_at = datetime.now(timezone.utc)
    await record_audit(session, action="ASSIGNMENT_REVOKED", target_type="ASSIGNMENT", target_id=existing.id, provider_id=provider_id, actor_user_id=actor_id, request_id=request_id, metadata={"reason": "SUPERSEDED_BY_NEW_ASSIGNMENT"})
    await session.commit()


async def create_assignment(session: AsyncSession, data, actor_subject: str, request_id: str, check_sod_at_creation: bool = False) -> tuple[AccessAssignment, dict]:
    target_user = await session.get(User, data.user_id)
    if not target_user:
        raise AccessPilotError("USER_NOT_FOUND", "The user was not found.", 404)
    provider_id, resource_name, _ = await _resolve_target(session, data.resource_type, data.resource_id)

    if data.resource_type == "APPLICATION":
        application = await session.get(Application, data.resource_id)
        role_name = _app_role_name(application, data.app_role_external_id)
        if not role_name:
            raise AccessPilotError("APPLICATION_ROLE_NOT_FOUND", "The selected application role was not found.", 404)
        resource_name = f"{resource_name} — {role_name}"

    if data.approver_id is not None:
        approver = await session.get(User, data.approver_id)
        if not approver:
            raise AccessPilotError("USER_NOT_FOUND", "The selected approver was not found.", 404)

    if data.fallback_approver_id is not None:
        fallback_approver = await session.get(User, data.fallback_approver_id)
        if not fallback_approver:
            raise AccessPilotError("USER_NOT_FOUND", "The selected fallback approver was not found.", 404)

    requested_by = await _resolve_internal_user_id(session, actor_subject)
    now = datetime.now(timezone.utc)
    approval_required = data.approver_id is not None
    effective_start = data.start_time or now
    bypass_activation = bool(getattr(data, "bypass_activation", False))

    # Real access is never granted at creation time — only an approver approving it, or the end user later
    # self-activating it (see activate_assignment), ever grants real Entra/Graph access. This applies uniformly
    # regardless of assignment_type (Permanent vs Temporary) and regardless of who created it (Admin, self-service
    # package request, etc.) — Permanent means "eligible indefinitely", not "granted forever with no activation".
    # The one deliberate exception: bypass_activation (Admin-only, direct "Add assignment" form) — the schema
    # already rejects combining it with an approver, so it only ever competes with the no-approver ELIGIBLE branch.
    if bypass_activation:
        status = "SCHEDULED" if _is_in_the_future(effective_start, now) else "ACTIVE"
    else:
        status = "PENDING_APPROVAL" if approval_required else "ELIGIBLE"

    # SoD check happens here — before the AccessAssignment row is even constructed, so a blocked grant leaves
    # nothing half-inserted, exactly like every other pre-construction validation above (USER_NOT_FOUND etc.).
    # The bypass-straight-to-ACTIVE branch is ALWAYS checked (that's the moment real access becomes real). The
    # ordinary PENDING_APPROVAL/ELIGIBLE branches are additionally checked when check_sod_at_creation=True —
    # set only by admin-initiated callers (the direct "Add assignment" endpoint, admin package-assign), NOT
    # self-service package requests, which still defer to activate_assignment() as before. The idea: an admin
    # assigning something that's already known to conflict should find out immediately, not watch it sit
    # ELIGIBLE only to be blocked later when someone tries to activate it.
    sod_conflicts: list = []
    if status == "ACTIVE" or check_sod_at_creation:
        from app.services.sod import check_sod_conflicts
        sod_conflicts = await check_sod_conflicts(session, data.user_id, data.resource_type, data.resource_id, data.app_role_external_id)
        if sod_conflicts and not getattr(data, "override_sod", False):
            await record_audit(session, action="ASSIGNMENT_CREATE_BLOCKED", target_type="ASSIGNMENT", provider_id=provider_id, actor_user_id=requested_by, request_id=request_id, result="FAILURE", metadata={"reason": "SOD_CONFLICT", "resource_type": data.resource_type, "resource_id": str(data.resource_id), "user_id": str(data.user_id), "conflicting_policies": [p.name for p in sod_conflicts]})
            await session.commit()
            raise AccessPilotError("SOD_CONFLICT", f"This grant conflicts with Separation-of-Duties polic{'y' if len(sod_conflicts) == 1 else 'ies'}: {', '.join(p.name for p in sod_conflicts)}.", 409, details={"conflicts": [{"policy_id": str(p.id), "policy_name": p.name, "severity": p.severity} for p in sod_conflicts]})

    fallback_approver_id = data.fallback_approver_id if approval_required else None
    # If a wait period is configured, the fallback approver can only act once it elapses without the primary
    # approver having responded; with no wait period, either approver may act at any time (immediate fallback).
    fallback_unlock_at = now + timedelta(hours=data.fallback_unlock_hours) if fallback_approver_id and data.fallback_unlock_hours else None

    assignment = AccessAssignment(
        provider_id=provider_id,
        user_id=data.user_id,
        resource_type=data.resource_type,
        resource_id=data.resource_id,
        app_role_external_id=data.app_role_external_id,
        assignment_type=data.assignment_type,
        status=status,
        start_time=effective_start,
        expiration_time=data.expiration_time,
        justification=data.justification,
        requested_by=requested_by,
        approved_by=data.approver_id,
        fallback_approver_id=fallback_approver_id,
        fallback_unlock_at=fallback_unlock_at,
        bypass_activation=bypass_activation,
        activated_at=now if status == "ACTIVE" else None,
    )
    session.add(assignment)
    await session.flush()

    if status == "ACTIVE":
        # Bypassing straight to real access — this is the moment it becomes real, so supersede any existing
        # real/eligible access to the exact same target now, exactly like activate_assignment/approve_assignment do.
        await _supersede_existing_assignment(session, user_id=data.user_id, resource_type=data.resource_type, resource_id=data.resource_id, app_role_external_id=data.app_role_external_id, provider_id=provider_id, actor_id=requested_by, request_id=request_id, exclude_id=assignment.id)
        _, _, target_external_id = await _resolve_target(session, data.resource_type, data.resource_id)
        try:
            await _grant_provider_access(session, provider_id, data.resource_type, target_external_id, target_user.external_id, data.app_role_external_id)
        except AccessPilotError:
            await session.rollback()
            raise
        if data.resource_type == "GROUP":
            await _record_local_group_membership(session, data.user_id, data.resource_id)
        await record_audit(session, action="ASSIGNMENT_ACTIVATED", target_type="ASSIGNMENT", target_id=assignment.id, provider_id=provider_id, actor_user_id=requested_by, request_id=request_id, metadata={"decision": "ADMIN_BYPASS", "justification": data.justification, "sod_override": bool(sod_conflicts)})

    await record_audit(session, action="ASSIGNMENT_CREATED", target_type="ASSIGNMENT", target_id=assignment.id, provider_id=provider_id, actor_user_id=requested_by, request_id=request_id, metadata={"status": status, "resource_type": data.resource_type, "bypass_activation": bypass_activation, "sod_override": bool(sod_conflicts) if status != "ACTIVE" else False})
    # General notification (see services/notifications.py) — deliberately distinct from a self-service request,
    # which the requester already knows about since they just did it themselves.
    if approval_required:
        for approver_id in {data.approver_id, fallback_approver_id} - {None}:
            await create_notification(session, approver_id, "ASSIGNMENT_PENDING_APPROVAL", f"{target_user.display_name} requested {resource_name} — awaiting your approval.", link="/approvals")
    elif requested_by != data.user_id:
        verb = "now have active access to" if status == "ACTIVE" else "are now eligible to activate"
        await create_notification(session, data.user_id, "ASSIGNMENT_CREATED", f"You {verb} {resource_name}.", link="/my-access")
    await session.commit()
    await session.refresh(assignment)
    return assignment, {"user_display_name": target_user.display_name, "resource_display_name": resource_name}


async def get_assignment(session: AsyncSession, assignment_id: UUID) -> AccessAssignment:
    assignment = await session.get(AccessAssignment, assignment_id)
    if not assignment:
        raise AccessPilotError("ASSIGNMENT_NOT_FOUND", "The assignment was not found.", 404)
    return assignment


async def list_assignments(session: AsyncSession) -> list[tuple[AccessAssignment, dict]]:
    assignments = list((await session.scalars(select(AccessAssignment).order_by(AccessAssignment.created_at.desc()))).all())
    return [(assignment, await hydrate_display_fields(session, assignment)) for assignment in assignments]


async def list_my_approvals(session: AsyncSession, actor_subject: str) -> list[tuple[AccessAssignment, dict]]:
    """Assignments where the caller is the designated approver OR the configured fallback approver — either one
    is authorized to decide, regardless of AccessPilot role."""
    actor_id = await _resolve_internal_user_id(session, actor_subject)
    if actor_id is None:
        return []
    assignments = list((await session.scalars(select(AccessAssignment).where(or_(AccessAssignment.approved_by == actor_id, AccessAssignment.fallback_approver_id == actor_id)).order_by(AccessAssignment.created_at.desc()))).all())
    return [(assignment, await hydrate_display_fields(session, assignment)) for assignment in assignments]


async def list_my_assignments(session: AsyncSession, actor_subject: str) -> list[tuple[AccessAssignment, dict]]:
    """The caller's own assignments, any status — powers the end-user 'My Access' dashboard (eligible-to-activate
    and currently-active access alike)."""
    actor_id = await _resolve_internal_user_id(session, actor_subject)
    if actor_id is None:
        return []
    assignments = list((await session.scalars(select(AccessAssignment).where(AccessAssignment.user_id == actor_id).order_by(AccessAssignment.created_at.desc()))).all())
    return [(assignment, await hydrate_display_fields(session, assignment)) for assignment in assignments]


async def _authorize_decision(session: AsyncSession, assignment: AccessAssignment, actor_subject: str, actor_roles: tuple[str, ...]) -> Optional[UUID]:
    """The designated approver or an Admin may always act. The configured fallback approver may act too — either
    immediately (if no wait period is configured) or, once one is, only after fallback_unlock_at has passed
    without the primary approver having responded. Returns the actor's internal user id (or None)."""
    actor_id = await _resolve_internal_user_id(session, actor_subject)
    if "AccessPilot.Admin" in actor_roles:
        return actor_id
    if actor_id is not None and actor_id == assignment.approved_by:
        return actor_id
    if actor_id is not None and actor_id == assignment.fallback_approver_id:
        if assignment.fallback_unlock_at is not None:
            now = datetime.now(timezone.utc)
            unlock_at = assignment.fallback_unlock_at
            if unlock_at.tzinfo is None:
                unlock_at = unlock_at.replace(tzinfo=timezone.utc)
            if now < unlock_at:
                raise AccessPilotError("FALLBACK_NOT_YET_AVAILABLE", f"The fallback approver may only act after {unlock_at.isoformat()} if the primary approver has not responded.", 403)
        return actor_id
    raise AccessPilotError("ACCESS_DENIED", "Only the designated approver, the fallback approver (once eligible), or an administrator can act on this assignment.", 403)


async def _authorize_activation(session: AsyncSession, assignment: AccessAssignment, actor_subject: str, actor_roles: tuple[str, ...]) -> Optional[UUID]:
    """Only the assignment's own target user, or an Admin, may activate it. Returns the actor's internal user id (or None)."""
    actor_id = await _resolve_internal_user_id(session, actor_subject)
    if "AccessPilot.Admin" in actor_roles:
        return actor_id
    if actor_id is not None and actor_id == assignment.user_id:
        return actor_id
    raise AccessPilotError("ACCESS_DENIED", "Only this assignment's own user or an administrator can activate it.", 403)


async def activate_assignment(session: AsyncSession, assignment_id: UUID, actor_subject: str, actor_roles: tuple[str, ...], duration_hours: float, justification: str, request_id: str, override_sod: bool = False) -> tuple[AccessAssignment, dict]:
    """Self-service (or admin-on-behalf-of) activation of an ELIGIBLE assignment — the custom-PIM equivalent of
    Entra PIM's 'Activate'. Grants real Entra/Graph access for a duration chosen by the caller, capped at the
    provider's max_self_activation_hours. A justification is mandatory (enforced by the schema) and recorded on
    the ASSIGNMENT_ACTIVATED audit entry — why THIS activation happened, distinct from the assignment's own
    original creation-time justification."""
    assignment = await get_assignment(session, assignment_id)
    if assignment.status != "ELIGIBLE":
        raise AccessPilotError("REQUEST_ALREADY_PROCESSED", "Only eligible assignments can be activated.", 409)
    actor_id = await _authorize_activation(session, assignment, actor_subject, actor_roles)
    now = datetime.now(timezone.utc)

    if _is_in_the_future(assignment.start_time, now):
        raise AccessPilotError("NOT_YET_ELIGIBLE", "This assignment cannot be activated until its start time arrives.", 409)

    if assignment.expiration_time is not None:
        eligibility_deadline = assignment.expiration_time
        if eligibility_deadline.tzinfo is None:
            eligibility_deadline = eligibility_deadline.replace(tzinfo=timezone.utc)
        if eligibility_deadline <= now:
            raise AccessPilotError("ELIGIBILITY_EXPIRED", "The window to activate this assignment has passed.", 409)

    provider = await session.get(IdentityProvider, assignment.provider_id)
    max_hours = provider.max_self_activation_hours if provider else 8
    if duration_hours > max_hours:
        raise AccessPilotError("DURATION_EXCEEDS_MAXIMUM", f"The requested duration exceeds the maximum of {max_hours} hours.", 422)

    # SoD check — the other moment real access becomes real (alongside create_assignment's bypass branch above).
    # A plain end-user self-activating gets a hard block, no override; an Admin acting on someone else's behalf
    # may override (still requires the mandatory justification already collected above).
    from app.services.sod import check_sod_conflicts
    sod_conflicts = await check_sod_conflicts(session, assignment.user_id, assignment.resource_type, assignment.resource_id, assignment.app_role_external_id)
    if sod_conflicts and not (override_sod and "AccessPilot.Admin" in actor_roles):
        await record_audit(session, action="ASSIGNMENT_ACTIVATED", target_type="ASSIGNMENT", target_id=assignment.id, provider_id=assignment.provider_id, actor_user_id=actor_id, request_id=request_id, result="FAILURE", metadata={"reason": "SOD_CONFLICT", "conflicting_policies": [p.name for p in sod_conflicts], "justification": justification})
        await session.commit()
        raise AccessPilotError("SOD_CONFLICT", f"Activating this would conflict with Separation-of-Duties polic{'y' if len(sod_conflicts) == 1 else 'ies'}: {', '.join(p.name for p in sod_conflicts)}.", 409, details={"conflicts": [{"policy_id": str(p.id), "policy_name": p.name, "severity": p.severity} for p in sod_conflicts]})

    # Only now that access is actually about to become real do we remove any existing eligible/active access to
    # the exact same target — mirrors approve_assignment's identical reasoning for the approval path.
    await _supersede_existing_assignment(session, user_id=assignment.user_id, resource_type=assignment.resource_type, resource_id=assignment.resource_id, app_role_external_id=assignment.app_role_external_id, provider_id=assignment.provider_id, actor_id=actor_id, request_id=request_id, exclude_id=assignment.id)

    _, resource_name, target_external_id = await _resolve_target(session, assignment.resource_type, assignment.resource_id)
    target_user = await session.get(User, assignment.user_id)
    if not target_user:
        raise AccessPilotError("USER_NOT_FOUND", "The user was not found.", 404)

    try:
        await _grant_provider_access(session, assignment.provider_id, assignment.resource_type, target_external_id, target_user.external_id, assignment.app_role_external_id)
    except AccessPilotError as exc:
        await record_audit(session, action="ASSIGNMENT_ACTIVATED", target_type="ASSIGNMENT", target_id=assignment.id, provider_id=assignment.provider_id, actor_user_id=actor_id, request_id=request_id, result="FAILURE", metadata={"decision": "SELF_ACTIVATED", "error_code": exc.code, "justification": justification})
        await session.commit()
        raise
    if assignment.resource_type == "GROUP":
        await _record_local_group_membership(session, assignment.user_id, assignment.resource_id)

    assignment.status = "ACTIVE"
    assignment.activated_at = now
    assignment.expiration_time = now + timedelta(hours=duration_hours)
    await record_audit(session, action="ASSIGNMENT_ACTIVATED", target_type="ASSIGNMENT", target_id=assignment.id, provider_id=assignment.provider_id, actor_user_id=actor_id, request_id=request_id, metadata={"decision": "SELF_ACTIVATED", "duration_hours": duration_hours, "justification": justification, "sod_override": bool(sod_conflicts)})
    if actor_id != assignment.user_id:
        await create_notification(session, assignment.user_id, "ASSIGNMENT_ACTIVATED", f"An administrator activated your access to {resource_name} — active for {duration_hours} hour{'s' if duration_hours != 1 else ''}.", link="/my-access")
    await session.commit()
    await session.refresh(assignment)
    return assignment, await hydrate_display_fields(session, assignment)


async def deactivate_assignment(session: AsyncSession, assignment_id: UUID, actor_subject: str, actor_roles: tuple[str, ...], request_id: str) -> tuple[AccessAssignment, dict]:
    """Self-service (or admin-on-behalf-of) early deactivation of an ACTIVE assignment — the custom-PIM equivalent
    of Entra PIM's 'Deactivate'. Revokes the real Entra/Graph access before the natural expiration and returns the
    assignment to ELIGIBLE (indefinitely, no activation deadline) so it can be activated again later without a new
    request/approval — reactivating never needs re-approval, exactly like activating any other eligible row."""
    assignment = await get_assignment(session, assignment_id)
    if assignment.status != "ACTIVE":
        raise AccessPilotError("REQUEST_ALREADY_PROCESSED", "Only active assignments can be deactivated.", 409)
    actor_id = await _authorize_activation(session, assignment, actor_subject, actor_roles)
    if assignment.bypass_activation and "AccessPilot.Admin" not in actor_roles:
        raise AccessPilotError("ACCESS_DENIED", "This assignment was granted directly by an administrator and cannot be deactivated by the end user.", 403)

    removed = await revoke_provider_access(session, assignment)
    if not removed:
        await record_audit(session, action="ASSIGNMENT_DEACTIVATED", target_type="ASSIGNMENT", target_id=assignment.id, provider_id=assignment.provider_id, actor_user_id=actor_id, request_id=request_id, result="FAILURE")
        await session.commit()
        raise AccessPilotError("PROVIDER_UNAVAILABLE", "Could not remove the real access. Please try again.", 502)

    assignment.status = "ELIGIBLE"
    assignment.activated_at = None
    assignment.expiration_time = None
    assignment.bypass_activation = False
    await record_audit(session, action="ASSIGNMENT_DEACTIVATED", target_type="ASSIGNMENT", target_id=assignment.id, provider_id=assignment.provider_id, actor_user_id=actor_id, request_id=request_id, metadata={"decision": "SELF_DEACTIVATED"})
    if actor_id != assignment.user_id:
        _, resource_name, _ = await _resolve_target(session, assignment.resource_type, assignment.resource_id)
        await create_notification(session, assignment.user_id, "ASSIGNMENT_DEACTIVATED", f"An administrator deactivated your access to {resource_name}.", link="/my-access")
    await session.commit()
    await session.refresh(assignment)
    return assignment, await hydrate_display_fields(session, assignment)


async def revoke_assignment(session: AsyncSession, assignment_id: UUID, actor_subject: str, justification: str, request_id: str) -> tuple[AccessAssignment, dict]:
    """Admin-only universal override: forcibly revokes an assignment regardless of its current status (ELIGIBLE,
    PENDING_APPROVAL, SCHEDULED, or ACTIVE) — unlike deactivate_assignment (self-service, ACTIVE-only, returns the
    user to ELIGIBLE so they can reactivate later), this always lands on the terminal REVOKED status and removes
    the real Entra/Graph grant first if one was actually made. A justification is mandatory (enforced by the
    schema) and recorded on the audit entry."""
    assignment = await get_assignment(session, assignment_id)
    if assignment.status in ("REJECTED", "REVOKED", "EXPIRED"):
        raise AccessPilotError("REQUEST_ALREADY_PROCESSED", "This assignment has already reached a final state.", 409)
    actor_id = await _resolve_internal_user_id(session, actor_subject)
    previous_status = assignment.status

    if assignment.status == "ACTIVE":
        removed = await revoke_provider_access(session, assignment)
        if not removed:
            await record_audit(session, action="ASSIGNMENT_REVOKED", target_type="ASSIGNMENT", target_id=assignment.id, provider_id=assignment.provider_id, actor_user_id=actor_id, request_id=request_id, result="FAILURE", metadata={"justification": justification})
            await session.commit()
            raise AccessPilotError("PROVIDER_UNAVAILABLE", "Could not remove the real access. Please try again.", 502)

    assignment.status = "REVOKED"
    assignment.revoked_at = datetime.now(timezone.utc)
    await record_audit(session, action="ASSIGNMENT_REVOKED", target_type="ASSIGNMENT", target_id=assignment.id, provider_id=assignment.provider_id, actor_user_id=actor_id, request_id=request_id, metadata={"reason": "ADMIN_REVOKED", "previous_status": previous_status, "justification": justification})
    if actor_id != assignment.user_id:
        _, resource_name, _ = await _resolve_target(session, assignment.resource_type, assignment.resource_id)
        await create_notification(session, assignment.user_id, "ASSIGNMENT_REVOKED", f"Your access to {resource_name} was revoked by an administrator.", link="/my-access")
    await session.commit()
    await session.refresh(assignment)
    return assignment, await hydrate_display_fields(session, assignment)


async def approve_assignment(session: AsyncSession, assignment_id: UUID, actor_subject: str, actor_roles: tuple[str, ...], justification: str, request_id: str) -> tuple[AccessAssignment, dict]:
    """Approves a pending request — but under the custom-PIM eligible/activate model, approval only grants
    ELIGIBILITY, never real access directly. The target user (or an Admin on their behalf) still activates it from
    My Access afterward, exactly like any other eligible assignment, capped at the provider's
    max_self_activation_hours. This mirrors create_assignment()'s no-approver branch — the only difference is the
    starting state (PENDING_APPROVAL) and that approved_by is already known. Superseding any existing access to the
    exact same target is deferred to that later activation, not done here — same reasoning as everywhere else in
    this file: nothing about the user's real access should change until it's actually about to become real. A
    justification for the decision is mandatory (enforced by the schema) and recorded on the audit entry."""
    assignment = await get_assignment(session, assignment_id)
    if assignment.status != "PENDING_APPROVAL":
        raise AccessPilotError("REQUEST_ALREADY_PROCESSED", "Only assignments pending approval can be approved.", 409)
    actor_id = await _authorize_decision(session, assignment, actor_subject, actor_roles)

    assignment.status = "ELIGIBLE"
    assignment.approved_by = actor_id or assignment.approved_by
    await record_audit(session, action="ASSIGNMENT_APPROVED", target_type="ASSIGNMENT", target_id=assignment.id, provider_id=assignment.provider_id, actor_user_id=actor_id, request_id=request_id, metadata={"decision": "APPROVED", "justification": justification})
    if actor_id != assignment.user_id:
        _, resource_name, _ = await _resolve_target(session, assignment.resource_type, assignment.resource_id)
        await create_notification(session, assignment.user_id, "ASSIGNMENT_APPROVED", f"Your request for {resource_name} was approved — you can now activate it from My Access.", link="/my-access")
    await session.commit()
    await session.refresh(assignment)
    return assignment, await hydrate_display_fields(session, assignment)


async def reject_assignment(session: AsyncSession, assignment_id: UUID, actor_subject: str, actor_roles: tuple[str, ...], request_id: str) -> tuple[AccessAssignment, dict]:
    assignment = await get_assignment(session, assignment_id)
    if assignment.status != "PENDING_APPROVAL":
        raise AccessPilotError("REQUEST_ALREADY_PROCESSED", "Only assignments pending approval can be rejected.", 409)
    actor_id = await _authorize_decision(session, assignment, actor_subject, actor_roles)
    assignment.status = "REJECTED"
    assignment.approved_by = actor_id or assignment.approved_by
    await record_audit(session, action="ASSIGNMENT_REJECTED", target_type="ASSIGNMENT", target_id=assignment.id, provider_id=assignment.provider_id, actor_user_id=actor_id, request_id=request_id, metadata={"decision": "REJECTED"})
    if actor_id != assignment.user_id:
        _, resource_name, _ = await _resolve_target(session, assignment.resource_type, assignment.resource_id)
        await create_notification(session, assignment.user_id, "ASSIGNMENT_REJECTED", f"Your request for {resource_name} was rejected.", link="/my-requests")
    await session.commit()
    await session.refresh(assignment)
    return assignment, await hydrate_display_fields(session, assignment)
