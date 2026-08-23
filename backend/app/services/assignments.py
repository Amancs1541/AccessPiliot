from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AccessPilotError
from app.models import AccessAssignment, Group, IdentityProvider, Role, User
from app.providers.graph_client import GraphError
from app.schemas.assignments import AssignmentResponse
from app.services.audit import record_audit
from app.services.provider_configuration import _connector


def to_response(assignment: AccessAssignment, hydrated: dict) -> AssignmentResponse:
    return AssignmentResponse(
        id=assignment.id, user_id=assignment.user_id, user_display_name=hydrated.get("user_display_name"),
        resource_type=assignment.resource_type, resource_id=assignment.resource_id, resource_display_name=hydrated.get("resource_display_name"),
        assignment_type=assignment.assignment_type, status=assignment.status, start_time=assignment.start_time, expiration_time=assignment.expiration_time,
        justification=assignment.justification, requested_by=assignment.requested_by, approved_by=assignment.approved_by,
        activated_at=assignment.activated_at, revoked_at=assignment.revoked_at, created_at=assignment.created_at,
    )


async def _resolve_target(session: AsyncSession, resource_type: str, resource_id: UUID) -> tuple[UUID, str, str]:
    """Returns (provider_id, display_name, external_id) for the target group/role, raising 404 if it doesn't exist."""
    if resource_type == "GROUP":
        group = await session.get(Group, resource_id)
        if not group:
            raise AccessPilotError("GROUP_NOT_FOUND", "The group was not found.", 404)
        return group.provider_id, group.name, group.external_id
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
    return {"user_display_name": user.display_name if user else None, "resource_display_name": resource_name}


async def _grant_provider_access(session: AsyncSession, provider_id: UUID, resource_type: str, target_external_id: str, user_external_id: str) -> None:
    """Performs the real Entra/Graph mutation. Raises AccessPilotError if it fails — callers must not mark ACTIVE on failure."""
    provider = await session.get(IdentityProvider, provider_id)
    if not provider:
        raise AccessPilotError("PROVIDER_NOT_FOUND", "The identity provider for this assignment was not found.", 404)
    connector = _connector(provider)
    try:
        granted = await connector.activate_assignment({"resource_type": resource_type, "target_external_id": target_external_id, "user_external_id": user_external_id})
    except (GraphError, NotImplementedError, ValueError) as exc:
        code = getattr(exc, "code", "PROVIDER_UNAVAILABLE")
        message = getattr(exc, "message", str(exc)) or "The provider operation failed."
        status_code = getattr(exc, "status_code", 502)
        raise AccessPilotError(code, message, status_code) from exc
    if not granted:
        raise AccessPilotError("PROVIDER_UNAVAILABLE", "The provider did not confirm the access grant.", 502)


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
    try:
        return bool(await connector.revoke_assignment({"resource_type": assignment.resource_type, "target_external_id": target_external_id, "user_external_id": user.external_id}))
    except (GraphError, NotImplementedError, ValueError):
        return False


async def grant_provider_access_for_assignment(session: AsyncSession, assignment: AccessAssignment) -> bool:
    """Safe wrapper for background workers: grants real Entra/Graph access, returning True/False instead of raising."""
    _, _, target_external_id = await _resolve_target(session, assignment.resource_type, assignment.resource_id)
    user = await session.get(User, assignment.user_id)
    if not user:
        return False
    try:
        await _grant_provider_access(session, assignment.provider_id, assignment.resource_type, target_external_id, user.external_id)
        return True
    except AccessPilotError:
        return False


def _is_in_the_future(moment: Optional[datetime], now: datetime) -> bool:
    if moment is None:
        return False
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment > now


async def _supersede_existing_assignment(session: AsyncSession, *, user_id: UUID, resource_type: str, resource_id: UUID, provider_id: UUID, actor_id: Optional[UUID], request_id: str) -> None:
    """If the user already has an active/scheduled assignment to this exact group/role, remove it first (real Entra
    removal if it was actually granted) and log the removal — a new assignment to the same target replaces it."""
    existing = (await session.execute(select(AccessAssignment).where(
        AccessAssignment.user_id == user_id,
        AccessAssignment.resource_type == resource_type,
        AccessAssignment.resource_id == resource_id,
        AccessAssignment.status.in_(("ACTIVE", "SCHEDULED")),
    ))).scalars().first()
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


async def create_assignment(session: AsyncSession, data, actor_subject: str, request_id: str) -> tuple[AccessAssignment, dict]:
    target_user = await session.get(User, data.user_id)
    if not target_user:
        raise AccessPilotError("USER_NOT_FOUND", "The user was not found.", 404)
    provider_id, resource_name, target_external_id = await _resolve_target(session, data.resource_type, data.resource_id)

    if data.approver_id is not None:
        approver = await session.get(User, data.approver_id)
        if not approver:
            raise AccessPilotError("USER_NOT_FOUND", "The selected approver was not found.", 404)

    requested_by = await _resolve_internal_user_id(session, actor_subject)
    await _supersede_existing_assignment(session, user_id=data.user_id, resource_type=data.resource_type, resource_id=data.resource_id, provider_id=provider_id, actor_id=requested_by, request_id=request_id)
    now = datetime.now(timezone.utc)
    approval_required = data.approver_id is not None
    effective_start = data.start_time or now
    starts_in_future = _is_in_the_future(effective_start, now)

    if approval_required:
        status = "PENDING_APPROVAL"
    elif starts_in_future:
        status = "SCHEDULED"
    else:
        status = "ACTIVE"

    if status == "ACTIVE":
        # Grant real Entra/Graph access before persisting ACTIVE state — never claim success the provider didn't confirm.
        try:
            await _grant_provider_access(session, provider_id, data.resource_type, target_external_id, target_user.external_id)
        except AccessPilotError as exc:
            await record_audit(session, action="ASSIGNMENT_CREATED", target_type="ASSIGNMENT", target_id=None, provider_id=provider_id, actor_user_id=requested_by, request_id=request_id, result="FAILURE", metadata={"error_code": exc.code, "resource_type": data.resource_type})
            await session.commit()
            raise

    assignment = AccessAssignment(
        provider_id=provider_id,
        user_id=data.user_id,
        resource_type=data.resource_type,
        resource_id=data.resource_id,
        assignment_type=data.assignment_type,
        status=status,
        start_time=effective_start,
        expiration_time=data.expiration_time,
        justification=data.justification,
        requested_by=requested_by,
        approved_by=data.approver_id,
        activated_at=now if status == "ACTIVE" else None,
    )
    session.add(assignment)
    await session.flush()
    await record_audit(session, action="ASSIGNMENT_CREATED", target_type="ASSIGNMENT", target_id=assignment.id, provider_id=provider_id, actor_user_id=requested_by, request_id=request_id, metadata={"status": status, "resource_type": data.resource_type})
    if status == "ACTIVE":
        await record_audit(session, action="ASSIGNMENT_ACTIVATED", target_type="ASSIGNMENT", target_id=assignment.id, provider_id=provider_id, actor_user_id=requested_by, request_id=request_id)
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
    """Assignments where the caller is the designated approver — regardless of AccessPilot role."""
    actor_id = await _resolve_internal_user_id(session, actor_subject)
    if actor_id is None:
        return []
    assignments = list((await session.scalars(select(AccessAssignment).where(AccessAssignment.approved_by == actor_id).order_by(AccessAssignment.created_at.desc()))).all())
    return [(assignment, await hydrate_display_fields(session, assignment)) for assignment in assignments]


async def _authorize_decision(session: AsyncSession, assignment: AccessAssignment, actor_subject: str, actor_roles: tuple[str, ...]) -> Optional[UUID]:
    """Only the designated approver, or an Admin, may approve/reject. Returns the actor's internal user id (or None)."""
    actor_id = await _resolve_internal_user_id(session, actor_subject)
    if "AccessPilot.Admin" in actor_roles:
        return actor_id
    if actor_id is not None and actor_id == assignment.approved_by:
        return actor_id
    raise AccessPilotError("ACCESS_DENIED", "Only the designated approver or an administrator can act on this assignment.", 403)


async def approve_assignment(session: AsyncSession, assignment_id: UUID, actor_subject: str, actor_roles: tuple[str, ...], request_id: str) -> tuple[AccessAssignment, dict]:
    assignment = await get_assignment(session, assignment_id)
    if assignment.status != "PENDING_APPROVAL":
        raise AccessPilotError("REQUEST_ALREADY_PROCESSED", "Only assignments pending approval can be approved.", 409)
    actor_id = await _authorize_decision(session, assignment, actor_subject, actor_roles)
    now = datetime.now(timezone.utc)

    if _is_in_the_future(assignment.start_time, now):
        # Approved, but its start time hasn't arrived yet — the activation worker grants real access when it does.
        assignment.status = "SCHEDULED"
        assignment.approved_by = actor_id or assignment.approved_by
        await record_audit(session, action="ASSIGNMENT_APPROVED", target_type="ASSIGNMENT", target_id=assignment.id, provider_id=assignment.provider_id, actor_user_id=actor_id, request_id=request_id, metadata={"decision": "APPROVED", "scheduled_start": assignment.start_time.isoformat() if assignment.start_time else None})
        await session.commit()
        await session.refresh(assignment)
        return assignment, await hydrate_display_fields(session, assignment)

    _, _, target_external_id = await _resolve_target(session, assignment.resource_type, assignment.resource_id)
    target_user = await session.get(User, assignment.user_id)
    if not target_user:
        raise AccessPilotError("USER_NOT_FOUND", "The user was not found.", 404)

    try:
        await _grant_provider_access(session, assignment.provider_id, assignment.resource_type, target_external_id, target_user.external_id)
    except AccessPilotError as exc:
        await record_audit(session, action="ASSIGNMENT_ACTIVATED", target_type="ASSIGNMENT", target_id=assignment.id, provider_id=assignment.provider_id, actor_user_id=actor_id, request_id=request_id, result="FAILURE", metadata={"decision": "APPROVED", "error_code": exc.code})
        await session.commit()
        raise

    assignment.status = "ACTIVE"
    assignment.activated_at = now
    assignment.approved_by = actor_id or assignment.approved_by
    await record_audit(session, action="ASSIGNMENT_ACTIVATED", target_type="ASSIGNMENT", target_id=assignment.id, provider_id=assignment.provider_id, actor_user_id=actor_id, request_id=request_id, metadata={"decision": "APPROVED"})
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
    await session.commit()
    await session.refresh(assignment)
    return assignment, await hydrate_display_fields(session, assignment)
