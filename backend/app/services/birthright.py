from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AccessPilotError
from app.models import AccessAssignment, BirthrightPolicy, User
from app.schemas.assignments import AssignmentCreate
from app.schemas.policies import BirthrightPolicyCreate, BirthrightPolicyUpdate
from app.services.assignments import _resolve_target, create_assignment
from app.services.audit import record_audit

NON_FINAL_ASSIGNMENT_STATUSES = ("REJECTED", "REVOKED", "EXPIRED")


async def list_birthright_policies(session: AsyncSession) -> list[BirthrightPolicy]:
    return list((await session.execute(select(BirthrightPolicy).order_by(BirthrightPolicy.created_at.desc()))).scalars().all())


async def create_birthright_policy(session: AsyncSession, data: BirthrightPolicyCreate, request_id: str) -> BirthrightPolicy:
    existing = (await session.execute(select(BirthrightPolicy).where(BirthrightPolicy.name == data.name))).scalar_one_or_none()
    if existing is not None:
        raise AccessPilotError("POLICY_NAME_TAKEN", "A birthright policy with this name already exists.", 409)
    await _resolve_target(session, data.resource_type, data.resource_id)  # 404s if the target doesn't exist
    row = BirthrightPolicy(name=data.name, match_field=data.match_field, match_value=data.match_value, resource_type=data.resource_type, resource_id=data.resource_id, app_role_external_id=data.app_role_external_id, assignment_type=data.assignment_type)
    session.add(row)
    await session.flush()
    await record_audit(session, action="BIRTHRIGHT_POLICY_CREATED", target_type="BIRTHRIGHT_POLICY", target_id=row.id, request_id=request_id, metadata={"name": row.name, "match_field": row.match_field, "match_value": row.match_value})
    await session.commit()
    await session.refresh(row)
    return row


async def _get_policy(session: AsyncSession, policy_id: UUID) -> BirthrightPolicy:
    row = await session.get(BirthrightPolicy, policy_id)
    if row is None:
        raise AccessPilotError("POLICY_NOT_FOUND", "The birthright policy was not found.", 404)
    return row


async def update_birthright_policy(session: AsyncSession, policy_id: UUID, data: BirthrightPolicyUpdate, request_id: str) -> BirthrightPolicy:
    row = await _get_policy(session, policy_id)
    if data.name is not None:
        row.name = data.name
    if data.match_value is not None:
        row.match_value = data.match_value
    if data.status is not None:
        row.status = data.status
    await record_audit(session, action="BIRTHRIGHT_POLICY_UPDATED", target_type="BIRTHRIGHT_POLICY", target_id=row.id, request_id=request_id, metadata=data.model_dump(exclude_none=True))
    await session.commit()
    await session.refresh(row)
    return row


async def delete_birthright_policy(session: AsyncSession, policy_id: UUID, request_id: str) -> None:
    row = await _get_policy(session, policy_id)
    await record_audit(session, action="BIRTHRIGHT_POLICY_DELETED", target_type="BIRTHRIGHT_POLICY", target_id=row.id, request_id=request_id, metadata={"name": row.name})
    await session.delete(row)
    await session.commit()


async def evaluate_birthright_policies(session: AsyncSession, user_id: UUID, actor_subject: str, request_id: str, *, bypass_activation: bool = False) -> list[UUID]:
    """Mover/joiner step of the CSV lifecycle: 'Identity -> Birthright Policy -> Role/Group determination'. Reuses
    create_assignment() UNMODIFIED. Idempotent: running this twice for the same identity never creates a
    duplicate — skips any policy whose exact target the identity already holds in a non-final state.

    `bypass_activation`: birthright access is conceptually 'day-one, automatic' access, distinct from JIT/PIM
    elevated access — when the caller has confirmed `user_id` is backed by a REAL provisioned account (see
    provisioning.py), passing True grants it for real immediately (reusing the existing bypass_activation
    mechanism built for Admin direct-assign) instead of landing merely ELIGIBLE. Defaults to False (ELIGIBLE-only)
    for the standalone evaluate endpoint, applied to already-synced identities where instant real access isn't
    necessarily intended."""
    user = await session.get(User, user_id)
    if user is None:
        raise AccessPilotError("USER_NOT_FOUND", "The user was not found.", 404)

    active_policies = (await session.execute(select(BirthrightPolicy).where(BirthrightPolicy.status == "ACTIVE"))).scalars().all()
    created_ids: list[UUID] = []
    for policy in active_policies:
        user_value = getattr(user, policy.match_field, None)
        if not user_value or user_value.strip().lower() != policy.match_value.strip().lower():
            continue
        already_held = (await session.execute(select(AccessAssignment.id).where(
            AccessAssignment.user_id == user.id,
            AccessAssignment.resource_type == policy.resource_type,
            AccessAssignment.resource_id == policy.resource_id,
            AccessAssignment.app_role_external_id == policy.app_role_external_id,
            AccessAssignment.status.notin_(NON_FINAL_ASSIGNMENT_STATUSES),
        ))).scalars().first()
        if already_held:
            continue
        data = AssignmentCreate(user_id=user.id, resource_type=policy.resource_type, resource_id=policy.resource_id, app_role_external_id=policy.app_role_external_id, assignment_type=policy.assignment_type, justification=f"Birthright policy: {policy.name}", bypass_activation=bypass_activation)
        try:
            assignment, _ = await create_assignment(session, data, actor_subject, request_id)
            created_ids.append(assignment.id)
        except AccessPilotError:
            continue  # e.g. the rule's target resource was deleted after the rule was created — don't block other rules
    return created_ids
