from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AccessPilotError
from app.models import AccessAssignment, Application, Group, GroupRoleMapping, User, UserGroup
from app.schemas.assignments import AssignmentCreate
from app.schemas.policies import GroupRoleMappingCreate, GroupRoleMappingUpdate
from app.services.assignments import _app_role_name, _resolve_target, create_assignment, revoke_assignment
from app.services.audit import record_audit
from app.services.birthright import NON_FINAL_ASSIGNMENT_STATUSES

# Deliberately not GROUP — associating a group with another group would be nested-group membership, a materially
# different (and more complex, cycle-prone) feature nobody asked for here. "APP ROLE" is APPLICATION with
# app_role_external_id set; "APP" alone is APPLICATION with it left None (the app's own default-access grant).
VALID_RESOURCE_TYPES = ("ROLE", "APPLICATION")


async def _get_mapping(session: AsyncSession, mapping_id: UUID) -> GroupRoleMapping:
    mapping = await session.get(GroupRoleMapping, mapping_id)
    if mapping is None:
        raise AccessPilotError("GROUP_ROLE_MAPPING_NOT_FOUND", "The group role mapping was not found.", 404)
    return mapping


async def _hydrate(session: AsyncSession, mapping: GroupRoleMapping) -> dict:
    group = await session.get(Group, mapping.source_group_id)
    _, resource_name, _ = await _resolve_target(session, mapping.resource_type, mapping.resource_id)
    if mapping.resource_type == "APPLICATION" and mapping.app_role_external_id:
        application = await session.get(Application, mapping.resource_id)
        role_name = _app_role_name(application, mapping.app_role_external_id)
        if role_name:
            resource_name = f"{resource_name} — {role_name}"
    return {
        "id": mapping.id, "source_group_id": mapping.source_group_id, "source_group_name": group.name if group else "Unknown group",
        "resource_type": mapping.resource_type, "resource_id": mapping.resource_id, "resource_display_name": resource_name,
        "app_role_external_id": mapping.app_role_external_id, "assignment_type": mapping.assignment_type, "status": mapping.status,
        "created_at": mapping.created_at, "updated_at": mapping.updated_at,
    }


async def list_group_role_mappings(session: AsyncSession, group_id: UUID | None = None) -> list[dict]:
    statement = select(GroupRoleMapping).order_by(GroupRoleMapping.created_at.desc())
    if group_id is not None:
        statement = statement.where(GroupRoleMapping.source_group_id == group_id)
    mappings = (await session.execute(statement)).scalars().all()
    return [await _hydrate(session, mapping) for mapping in mappings]


async def create_group_role_mapping(session: AsyncSession, data: GroupRoleMappingCreate, actor_subject: str, request_id: str) -> dict:
    group = await session.get(Group, data.source_group_id)
    if group is None:
        raise AccessPilotError("GROUP_NOT_FOUND", "The group was not found.", 404)
    await _resolve_target(session, data.resource_type, data.resource_id)  # 404s if the target doesn't exist
    mapping = GroupRoleMapping(source_group_id=data.source_group_id, resource_type=data.resource_type, resource_id=data.resource_id, app_role_external_id=data.app_role_external_id, assignment_type=data.assignment_type)
    session.add(mapping)
    await session.flush()
    await record_audit(session, action="GROUP_ROLE_MAPPING_CREATED", target_type="GROUP_ROLE_MAPPING", target_id=mapping.id, request_id=request_id, metadata={"source_group_id": str(data.source_group_id), "resource_type": data.resource_type, "resource_id": str(data.resource_id)})
    await session.commit()
    await session.refresh(mapping)
    # Instant enforcement (Layer A, same principle as birthright): don't wait for anyone's group membership to
    # change again — every CURRENT member of the source group is evaluated against this brand-new mapping right now.
    await reconcile_all_members_of_group(session, data.source_group_id, actor_subject, request_id)
    return await _hydrate(session, mapping)


async def update_group_role_mapping(session: AsyncSession, mapping_id: UUID, data: GroupRoleMappingUpdate, actor_subject: str, request_id: str) -> dict:
    mapping = await _get_mapping(session, mapping_id)
    if data.status is not None:
        mapping.status = data.status
    await record_audit(session, action="GROUP_ROLE_MAPPING_UPDATED", target_type="GROUP_ROLE_MAPPING", target_id=mapping.id, request_id=request_id, metadata=data.model_dump(exclude_none=True))
    await session.commit()
    await session.refresh(mapping)
    # A disabled mapping should stop granting/keeping access for its current members right away, not just the
    # next time one of them happens to change groups.
    await reconcile_all_members_of_group(session, mapping.source_group_id, actor_subject, request_id)
    return await _hydrate(session, mapping)


async def delete_group_role_mapping(session: AsyncSession, mapping_id: UUID, actor_subject: str, request_id: str) -> None:
    mapping = await _get_mapping(session, mapping_id)
    source_group_id = mapping.source_group_id
    await record_audit(session, action="GROUP_ROLE_MAPPING_DELETED", target_type="GROUP_ROLE_MAPPING", target_id=mapping.id, request_id=request_id, metadata={"source_group_id": str(source_group_id)})
    await session.delete(mapping)
    await session.commit()
    await reconcile_all_members_of_group(session, source_group_id, actor_subject, request_id)


async def evaluate_group_role_mappings_for_user(session: AsyncSession, user_id: UUID, actor_subject: str, request_id: str, *, bypass_activation: bool = False) -> list[UUID]:
    """Membership-triggered analog of evaluate_birthright_policies — same idempotent-by-construction shape,
    same ELIGIBLE-only default (a mover/joiner into a mapped group doesn't get instant real access any more than
    a birthright-matched attribute change does)."""
    user = await session.get(User, user_id)
    if user is None:
        raise AccessPilotError("USER_NOT_FOUND", "The user was not found.", 404)
    # Same exclusion as evaluate_birthright_policies — a PU/TU shadow account's access is always a deliberate,
    # individual Admin decision, never inherited from a group-membership rule meant for real hires.
    if user.account_type != "NORMAL":
        return []

    member_group_ids = set((await session.execute(select(UserGroup.group_id).where(UserGroup.user_id == user_id))).scalars().all())
    if not member_group_ids:
        return []

    active_mappings = (await session.execute(select(GroupRoleMapping).where(GroupRoleMapping.status == "ACTIVE", GroupRoleMapping.source_group_id.in_(member_group_ids)))).scalars().all()
    created_ids: list[UUID] = []
    for mapping in active_mappings:
        already_held = (await session.execute(select(AccessAssignment.id).where(
            AccessAssignment.user_id == user.id,
            AccessAssignment.resource_type == mapping.resource_type,
            AccessAssignment.resource_id == mapping.resource_id,
            AccessAssignment.app_role_external_id == mapping.app_role_external_id,
            AccessAssignment.status.notin_(NON_FINAL_ASSIGNMENT_STATUSES),
        ))).scalars().first()
        if already_held:
            continue
        group = await session.get(Group, mapping.source_group_id)
        data = AssignmentCreate(user_id=user.id, resource_type=mapping.resource_type, resource_id=mapping.resource_id, app_role_external_id=mapping.app_role_external_id, assignment_type=mapping.assignment_type, justification=f"Group mapping: {group.name if group else mapping.source_group_id}", bypass_activation=bypass_activation)
        try:
            assignment, _ = await create_assignment(session, data, actor_subject, request_id, group_role_mapping_id=mapping.id)
            created_ids.append(assignment.id)
        except AccessPilotError:
            continue  # e.g. the mapping's target resource was deleted after it was created — don't block the others
    return created_ids


async def reconcile_group_role_mappings_for_user(session: AsyncSession, user_id: UUID, actor_subject: str, request_id: str) -> dict:
    """Mover/leaver reconciliation for group-triggered mappings — call whenever a user's group membership has
    just changed (a directory sync noticing they joined/left a mapped group, or a mapping itself being disabled/
    deleted). Only ever revokes a live assignment tagged with group_role_mapping_id — a manual grant to the exact
    same resource is structurally invisible to this, the same safety property birthright reconciliation has."""
    user = await session.get(User, user_id)
    if user is None:
        raise AccessPilotError("USER_NOT_FOUND", "The user was not found.", 404)

    member_group_ids = set((await session.execute(select(UserGroup.group_id).where(UserGroup.user_id == user_id))).scalars().all())
    active_mappings = (await session.execute(select(GroupRoleMapping).where(GroupRoleMapping.status == "ACTIVE"))).scalars().all()
    matching_mapping_ids = {mapping.id for mapping in active_mappings if mapping.source_group_id in member_group_ids}

    live_mapping_assignments = (await session.execute(select(AccessAssignment).where(
        AccessAssignment.user_id == user_id,
        AccessAssignment.group_role_mapping_id.isnot(None),
        AccessAssignment.status.notin_(NON_FINAL_ASSIGNMENT_STATUSES),
    ))).scalars().all()

    revoked_ids: list[UUID] = []
    for assignment in live_mapping_assignments:
        if assignment.group_role_mapping_id in matching_mapping_ids:
            continue
        try:
            await revoke_assignment(session, assignment.id, actor_subject, "Group role mapping no longer applies — group membership changed.", request_id, reason="GROUP_ROLE_MAPPING_NO_LONGER_APPLIES")
            revoked_ids.append(assignment.id)
        except AccessPilotError:
            continue

    granted_ids = await evaluate_group_role_mappings_for_user(session, user_id, actor_subject, request_id)
    return {"revoked": revoked_ids, "granted": granted_ids}


async def reconcile_all_members_of_group(session: AsyncSession, group_id: UUID, actor_subject: str, request_id: str) -> None:
    """The 'instant enforcement on policy change' half of this feature — reconciles every CURRENT member of
    `group_id` right now, so creating/disabling/deleting a mapping takes effect immediately instead of waiting
    for each affected member's group membership to happen to change again."""
    member_ids = (await session.execute(select(UserGroup.user_id).where(UserGroup.group_id == group_id))).scalars().all()
    for member_id in member_ids:
        try:
            await reconcile_group_role_mappings_for_user(session, member_id, actor_subject, request_id)
        except AccessPilotError:
            continue


async def get_or_create_eligible_assignments_for_group(session: AsyncSession, user_id: UUID, group_id: UUID, actor_subject: str, request_id: str) -> list[AccessAssignment]:
    """Cascading-activation support (see assignments.activate_assignment): ensures every currently-ACTIVE
    GroupRoleMapping sourced from `group_id` has an ELIGIBLE assignment for this user — creating one on the spot
    if this is the first time the group's own eligibility ran ahead of the mapping's — then returns exactly
    those ELIGIBLE rows, ready to be activated in the same action as the group itself. Timing (mapping created
    before or after the user became group-eligible) never matters."""
    await evaluate_group_role_mappings_for_user(session, user_id, actor_subject, request_id)
    mapping_ids = (await session.execute(select(GroupRoleMapping.id).where(GroupRoleMapping.source_group_id == group_id, GroupRoleMapping.status == "ACTIVE"))).scalars().all()
    if not mapping_ids:
        return []
    return list((await session.execute(select(AccessAssignment).where(
        AccessAssignment.user_id == user_id, AccessAssignment.group_role_mapping_id.in_(mapping_ids), AccessAssignment.status == "ELIGIBLE",
    ))).scalars().all())


async def get_linked_assignments_for_group(session: AsyncSession, user_id: UUID, group_id: UUID, statuses: tuple[str, ...] | None = None) -> list[AccessAssignment]:
    """Cascading-deactivation/-revocation support: every assignment for this user that SOME GroupRoleMapping
    sourced from `group_id` actually granted (regardless of whether that mapping is still ACTIVE — a disabled
    mapping's past grant is still 'linked' for lifecycle purposes), optionally narrowed to specific statuses."""
    mapping_ids = (await session.execute(select(GroupRoleMapping.id).where(GroupRoleMapping.source_group_id == group_id))).scalars().all()
    if not mapping_ids:
        return []
    statement = select(AccessAssignment).where(AccessAssignment.user_id == user_id, AccessAssignment.group_role_mapping_id.in_(mapping_ids))
    if statuses:
        statement = statement.where(AccessAssignment.status.in_(statuses))
    return list((await session.execute(statement)).scalars().all())
