from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AccessPilotError
from app.models import AccessAssignment, DynamicGroup, DynamicGroupTarget, Group, IdentityProvider, User
from app.providers.base import NewGroupRequest, ProviderConflictError
from app.providers.graph_client import GraphError
from app.schemas.assignments import AssignmentCreate
from app.schemas.policies import BirthrightRuleJson
from app.services.assignments import _grant_provider_access, _record_local_group_membership, _remove_local_group_membership, _resolve_target, create_assignment, revoke_assignment, revoke_provider_access
from app.services.audit import record_audit
from app.services.birthright import _validate_rule, matches_conditions
from app.services.directory_sync import upsert_group
from app.services.provider_configuration import _connector

NON_FINAL_ASSIGNMENT_STATUSES = ("REJECTED", "REVOKED", "EXPIRED")
SYNC_MODES = ("OFF", "ELIGIBLE", "ACTIVE")


async def _get_dynamic_group(session: AsyncSession, dynamic_group_id: UUID) -> DynamicGroup:
    row = await session.get(DynamicGroup, dynamic_group_id)
    if row is None:
        raise AccessPilotError("DYNAMIC_GROUP_NOT_FOUND", "The dynamic group was not found.", 404)
    return row


async def _get_target(session: AsyncSession, dynamic_group_id: UUID, provider_id: UUID) -> DynamicGroupTarget:
    target = (await session.execute(select(DynamicGroupTarget).where(DynamicGroupTarget.dynamic_group_id == dynamic_group_id, DynamicGroupTarget.provider_id == provider_id))).scalars().first()
    if target is None:
        raise AccessPilotError("DYNAMIC_GROUP_TARGET_NOT_FOUND", "This provider is not a target of this dynamic group.", 404)
    return target


async def list_targets(session: AsyncSession, dynamic_group_id: UUID) -> list[DynamicGroupTarget]:
    return list((await session.scalars(select(DynamicGroupTarget).where(DynamicGroupTarget.dynamic_group_id == dynamic_group_id))).all())


async def list_dynamic_groups(session: AsyncSession) -> list[DynamicGroup]:
    return list((await session.scalars(select(DynamicGroup).order_by(DynamicGroup.created_at.desc()))).all())


async def get_dynamic_group(session: AsyncSession, dynamic_group_id: UUID) -> DynamicGroup:
    return await _get_dynamic_group(session, dynamic_group_id)


async def group_owner_map(session: AsyncSession) -> dict[UUID, DynamicGroup]:
    """Every real Group id -> the DynamicGroup that created it via sync, for the Groups catalog's 'managed by'
    badge — one batch lookup instead of an N+1 per-group fetch. Distinct from (and not to be confused with)
    birthright.dynamic_group_ids, last round's simpler 'a plain Birthright Policy happens to grant this group'
    lens — this one is specifically for groups a DynamicGroupTarget itself created."""
    targets = (await session.execute(select(DynamicGroupTarget).where(DynamicGroupTarget.group_id.isnot(None)))).scalars().all()
    if not targets:
        return {}
    groups_by_id = {g.id: g for g in (await session.execute(select(DynamicGroup).where(DynamicGroup.id.in_({t.dynamic_group_id for t in targets})))).scalars().all()}
    return {target.group_id: groups_by_id[target.dynamic_group_id] for target in targets if target.dynamic_group_id in groups_by_id}


def _conditions_from_rule(rule: BirthrightRuleJson) -> list[dict]:
    return [{"field": c.field, "operator": c.operator, "value": ([str(item).strip() for item in c.value] if isinstance(c.value, list) else c.value.strip())} for c in rule.conditions]


async def create_dynamic_group(session: AsyncSession, name: str, description: Optional[str], group_label: Optional[str], rule: BirthrightRuleJson, actor_id: Optional[UUID], request_id: str) -> DynamicGroup:
    _validate_rule(rule)
    existing = (await session.execute(select(DynamicGroup.id).where(DynamicGroup.name == name))).scalar_one_or_none()
    if existing is not None:
        raise AccessPilotError("DYNAMIC_GROUP_NAME_TAKEN", "A dynamic group with this name already exists.", 409)
    row = DynamicGroup(name=name, description=description, group_label=group_label, conditions_json=_conditions_from_rule(rule), conditions_operator=rule.operator, status="ACTIVE", created_by=actor_id)
    session.add(row)
    await session.flush()
    connected = (await session.execute(select(IdentityProvider).where(IdentityProvider.status == "CONNECTED"))).scalars().all()
    for provider in connected:
        session.add(DynamicGroupTarget(dynamic_group_id=row.id, provider_id=provider.id, sync_mode="OFF"))
    await record_audit(session, action="DYNAMIC_GROUP_CREATED", target_type="DYNAMIC_GROUP", target_id=row.id, request_id=request_id, actor_user_id=actor_id, metadata={"name": name, "targets": len(connected)})
    await session.commit()
    await session.refresh(row)
    return row


async def update_dynamic_group(session: AsyncSession, dynamic_group_id: UUID, *, name: Optional[str], description: Optional[str], group_label: Optional[str], rule: Optional[BirthrightRuleJson], status: Optional[str], actor_subject: str, actor_id: Optional[UUID], request_id: str) -> DynamicGroup:
    row = await _get_dynamic_group(session, dynamic_group_id)
    if name is not None and name != row.name:
        clash = (await session.execute(select(DynamicGroup.id).where(DynamicGroup.name == name, DynamicGroup.id != dynamic_group_id))).scalar_one_or_none()
        if clash is not None:
            raise AccessPilotError("DYNAMIC_GROUP_NAME_TAKEN", "A dynamic group with this name already exists.", 409)
        row.name = name
    if description is not None:
        row.description = description
    if group_label is not None:
        row.group_label = group_label
    if rule is not None:
        _validate_rule(rule)
        row.conditions_json = _conditions_from_rule(rule)
        row.conditions_operator = rule.operator
    if status is not None:
        row.status = status
    await record_audit(session, action="DYNAMIC_GROUP_UPDATED", target_type="DYNAMIC_GROUP", target_id=row.id, request_id=request_id, actor_user_id=actor_id, metadata={"name": row.name, "status": row.status})
    await session.commit()
    await session.refresh(row)
    # "Apply on save": a rule or status change is re-checked against every synced target immediately, same
    # precedent app.services.birthright.recheck_users_for_policy already set for its own JSON editor.
    for target in await list_targets(session, dynamic_group_id):
        if target.sync_mode != "OFF":
            await _reconcile_target(session, row, target, actor_subject, request_id)
    return row


async def delete_dynamic_group(session: AsyncSession, dynamic_group_id: UUID, actor_id: Optional[UUID], request_id: str) -> None:
    """Removes AccessPilot's own bookkeeping only — any real group(s)/membership already pushed to an IdP are
    left exactly as they are (same philosophy as turning a target off), never silently torn down. Every
    assignment this dynamic group ever created (active or long since revoked) is kept as a real historical
    record — only its dynamic_group_id provenance tag is cleared, the same real FK-violation fix
    birthright_policy_id's own delete path still needs (confirmed live: deleting a policy/group that ever made a
    grant, even one later revoked, otherwise fails outright with a foreign-key error)."""
    row = await _get_dynamic_group(session, dynamic_group_id)
    assignments = (await session.execute(select(AccessAssignment).where(AccessAssignment.dynamic_group_id == dynamic_group_id))).scalars().all()
    for assignment in assignments:
        assignment.dynamic_group_id = None
    await record_audit(session, action="DYNAMIC_GROUP_DELETED", target_type="DYNAMIC_GROUP", target_id=row.id, request_id=request_id, actor_user_id=actor_id, metadata={"name": row.name, "assignments_unlinked": len(assignments)})
    await session.delete(row)
    await session.commit()


async def _ensure_real_group(session: AsyncSession, dynamic_group: DynamicGroup, target: DynamicGroupTarget, actor_subject: str, request_id: str) -> Group:
    if target.group_id is not None:
        group = await session.get(Group, target.group_id)
        if group is not None:
            return group
    provider = await session.get(IdentityProvider, target.provider_id)
    if provider is None:
        raise AccessPilotError("PROVIDER_NOT_FOUND", "This target's provider was not found.", 404)
    connector = _connector(provider)
    try:
        normalized = await connector.create_group(NewGroupRequest(display_name=dynamic_group.name, description=dynamic_group.description, mail_nickname=None))
    except ProviderConflictError as exc:
        raise AccessPilotError("DYNAMIC_GROUP_NAME_CONFLICT", f"A group named {dynamic_group.name} already exists in {provider.name} ({exc}).", 409) from exc
    except GraphError as exc:
        raise AccessPilotError(exc.code, exc.message, exc.status_code) from exc
    group = await upsert_group(session, provider.id, normalized)
    group.group_label = dynamic_group.group_label
    target.group_id = group.id
    await session.flush()
    await record_audit(session, action="DYNAMIC_GROUP_REAL_GROUP_CREATED", target_type="GROUP", target_id=group.id, provider_id=provider.id, request_id=request_id, metadata={"dynamic_group_id": str(dynamic_group.id), "dynamic_group_name": dynamic_group.name})
    return group


async def _promote_to_active(session: AsyncSession, assignment: AccessAssignment, actor_subject: str, request_id: str) -> None:
    """System-level promotion (a target's mode moved to ACTIVE) — unlike activate_assignment, this is not
    self-service: no duration/expiration window, no per-actor authorization check, since the actor here is an
    admin changing a dynamic group's global sync mode, not activating their own personal access."""
    target_user = await session.get(User, assignment.user_id)
    _, _, target_external_id = await _resolve_target(session, assignment.resource_type, assignment.resource_id)
    await _grant_provider_access(session, assignment.provider_id, assignment.resource_type, target_external_id, target_user, assignment.app_role_external_id, actor_subject=actor_subject, request_id=request_id)
    await _record_local_group_membership(session, assignment.user_id, assignment.resource_id)
    assignment.status = "ACTIVE"
    assignment.activated_at = datetime.now(timezone.utc)
    await record_audit(session, action="DYNAMIC_GROUP_ASSIGNMENT_PROMOTED", target_type="ASSIGNMENT", target_id=assignment.id, provider_id=assignment.provider_id, request_id=request_id, metadata={"dynamic_group_id": str(assignment.dynamic_group_id)})
    await session.commit()


async def _demote_to_eligible(session: AsyncSession, assignment: AccessAssignment, actor_subject: str, request_id: str) -> None:
    """The symmetric downgrade — removes real membership but keeps the eligibility record (never REVOKED)."""
    await revoke_provider_access(session, assignment)
    assignment.status = "ELIGIBLE"
    assignment.activated_at = None
    await record_audit(session, action="DYNAMIC_GROUP_ASSIGNMENT_DEMOTED", target_type="ASSIGNMENT", target_id=assignment.id, provider_id=assignment.provider_id, request_id=request_id, metadata={"dynamic_group_id": str(assignment.dynamic_group_id)})
    await session.commit()


async def _reconcile_target(session: AsyncSession, dynamic_group: DynamicGroup, target: DynamicGroupTarget, actor_subject: str, request_id: str) -> dict:
    """Brings this ONE target's real membership in line with dynamic_group's rule + target.sync_mode, right now.
    One user's failure never blocks the rest, same discipline reconcile_birthright_policies_for_user already
    uses. A no-op (returns zeroed counts) when sync_mode is OFF — existing membership is left untouched."""
    counts = {"granted": 0, "revoked": 0, "promoted": 0, "demoted": 0}
    if target.sync_mode == "OFF":
        return counts
    group = await _ensure_real_group(session, dynamic_group, target, actor_subject, request_id)

    users = list((await session.execute(select(User).where(User.account_type == "NORMAL"))).scalars().all())
    live = (await session.execute(select(AccessAssignment).where(
        AccessAssignment.dynamic_group_id == dynamic_group.id,
        AccessAssignment.resource_id == group.id,
        AccessAssignment.status.notin_(NON_FINAL_ASSIGNMENT_STATUSES),
    ))).scalars().all()
    held_by_user = {a.user_id: a for a in live}

    for user in users:
        matches = dynamic_group.status == "ACTIVE" and matches_conditions(user, dynamic_group.conditions_json or [], dynamic_group.conditions_operator)
        held = held_by_user.get(user.id)
        if matches and held is None:
            data = AssignmentCreate(user_id=user.id, resource_type="GROUP", resource_id=group.id, assignment_type="PERMANENT", justification=f"Dynamic group: {dynamic_group.name}", bypass_activation=(target.sync_mode == "ACTIVE"))
            try:
                await create_assignment(session, data, actor_subject, request_id, dynamic_group_id=dynamic_group.id)
                counts["granted"] += 1
            except AccessPilotError:
                continue
        elif matches and held is not None:
            try:
                if target.sync_mode == "ACTIVE" and held.status == "ELIGIBLE":
                    await _promote_to_active(session, held, actor_subject, request_id)
                    counts["promoted"] += 1
                elif target.sync_mode == "ELIGIBLE" and held.status == "ACTIVE":
                    await _demote_to_eligible(session, held, actor_subject, request_id)
                    counts["demoted"] += 1
            except AccessPilotError:
                continue
        elif not matches and held is not None:
            try:
                await revoke_assignment(session, held.id, actor_subject, "Dynamic group rule no longer matches this person.", request_id, reason="DYNAMIC_GROUP_NO_LONGER_MATCHES")
                counts["revoked"] += 1
            except AccessPilotError:
                continue
    return counts


async def set_target_sync_mode(session: AsyncSession, dynamic_group_id: UUID, provider_id: UUID, mode: str, actor_subject: str, request_id: str) -> DynamicGroupTarget:
    if mode not in SYNC_MODES:
        raise AccessPilotError("VALIDATION_ERROR", f"Unsupported sync mode '{mode}'.", 400)
    dynamic_group = await _get_dynamic_group(session, dynamic_group_id)
    target = await _get_target(session, dynamic_group_id, provider_id)
    previous_mode = target.sync_mode
    target.sync_mode = mode
    await session.flush()
    if mode != "OFF":
        await _reconcile_target(session, dynamic_group, target, actor_subject, request_id)
    await record_audit(session, action="DYNAMIC_GROUP_TARGET_SYNC_CHANGED", target_type="DYNAMIC_GROUP", target_id=dynamic_group_id, provider_id=provider_id, request_id=request_id, metadata={"previous_mode": previous_mode, "new_mode": mode})
    await session.commit()
    await session.refresh(target)
    return target


async def evaluate_dynamic_groups_for_user(session: AsyncSession, user_id: UUID, actor_subject: str, request_id: str) -> dict:
    """The per-user re-evaluation step — called alongside reconcile_birthright_policies_for_user at every real
    attribute-change trigger point (joiner, CSV import, an admin's PATCH, a real directory sync, mover/leaver,
    a workflow's USER_ATTRIBUTES completion). Mirrors that function's own diff-based shape, scoped to
    DynamicGroup + its non-OFF targets instead of BirthrightPolicy actions."""
    user = await session.get(User, user_id)
    if user is None or user.account_type != "NORMAL":
        return {"granted": 0, "revoked": 0, "promoted": 0, "demoted": 0}

    totals = {"granted": 0, "revoked": 0, "promoted": 0, "demoted": 0}
    groups = (await session.execute(select(DynamicGroup).where(DynamicGroup.status == "ACTIVE"))).scalars().all()
    for dynamic_group in groups:
        matches = matches_conditions(user, dynamic_group.conditions_json or [], dynamic_group.conditions_operator)
        for target in await list_targets(session, dynamic_group.id):
            if target.sync_mode == "OFF":
                continue
            held = None
            if target.group_id is not None:
                held = (await session.execute(select(AccessAssignment).where(
                    AccessAssignment.dynamic_group_id == dynamic_group.id,
                    AccessAssignment.resource_id == target.group_id,
                    AccessAssignment.user_id == user_id,
                    AccessAssignment.status.notin_(NON_FINAL_ASSIGNMENT_STATUSES),
                ))).scalars().first()
            if matches and held is None:
                group = await _ensure_real_group(session, dynamic_group, target, actor_subject, request_id)
                data = AssignmentCreate(user_id=user_id, resource_type="GROUP", resource_id=group.id, assignment_type="PERMANENT", justification=f"Dynamic group: {dynamic_group.name}", bypass_activation=(target.sync_mode == "ACTIVE"))
                try:
                    await create_assignment(session, data, actor_subject, request_id, dynamic_group_id=dynamic_group.id)
                    totals["granted"] += 1
                except AccessPilotError:
                    continue
            elif matches and held is not None:
                try:
                    if target.sync_mode == "ACTIVE" and held.status == "ELIGIBLE":
                        await _promote_to_active(session, held, actor_subject, request_id)
                        totals["promoted"] += 1
                    elif target.sync_mode == "ELIGIBLE" and held.status == "ACTIVE":
                        await _demote_to_eligible(session, held, actor_subject, request_id)
                        totals["demoted"] += 1
                except AccessPilotError:
                    continue
            elif not matches and held is not None:
                try:
                    await revoke_assignment(session, held.id, actor_subject, "Dynamic group rule no longer matches this person.", request_id, reason="DYNAMIC_GROUP_NO_LONGER_MATCHES")
                    totals["revoked"] += 1
                except AccessPilotError:
                    continue
    return totals
