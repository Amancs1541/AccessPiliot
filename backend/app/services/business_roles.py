"""Business Roles: a named, owned bundle of real entitlements (groups / directory roles / application roles) —
Step 1 of the Role Management & Entitlement Mapping plan. Deliberately modeled on app.services.packages: the same
create/edit/archive-if-assigned lifecycle, the same owner-record pattern, the same resource-tuple item shape — so
every other system that already understands a package (approval, activation, SoD, notifications) can be extended
to understand a Business Role later with the same small, proven moves, not a parallel engine."""
from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AccessPilotError
from app.models import AccessAssignment, Application, BusinessRole, BusinessRoleItem, BusinessRoleOwner, Group, IdentityProvider, Role, SodPolicyEntity, User
from app.schemas.assignments import AssignmentCreate
from app.schemas.business_roles import BusinessRoleAnalytics, BusinessRoleAssignCreate, BusinessRoleAssignItemResult, BusinessRoleAssignResponse, BusinessRoleCreate, BusinessRoleHolder, BusinessRoleItemResponse, BusinessRoleOwnerInfo, BusinessRoleResponse, BusinessRoleTally, BusinessRoleUpdate, ResourceReferenceUpdate, RoleAssignmentBatch
from app.services.assignments import _app_role_name, _resolve_internal_user_id, _resolve_target, create_assignment, hydrate_display_fields, to_response
from app.services.audit import record_audit

_RESOURCE_MODELS = {"GROUP": Group, "ROLE": Role, "APPLICATION": Application}


async def _validate_items(session: AsyncSession, role_id: UUID | None, items: list) -> None:
    seen: set[tuple[str, UUID, str | None]] = set()
    for item in items:
        key = (item.resource_type, item.resource_id, item.app_role_external_id)
        if key in seen:
            raise AccessPilotError("DUPLICATE_BUSINESS_ROLE_ITEM", "A Business Role cannot contain the same target twice.", 422)
        seen.add(key)
        await _resolve_target(session, item.resource_type, item.resource_id)
        if item.resource_type == "APPLICATION":
            application = await session.get(Application, item.resource_id)
            if not _app_role_name(application, item.app_role_external_id):
                raise AccessPilotError("APPLICATION_ROLE_NOT_FOUND", "The selected application role was not found.", 404)
        existing = (await session.scalars(select(BusinessRoleItem).where(
            BusinessRoleItem.resource_type == item.resource_type, BusinessRoleItem.resource_id == item.resource_id,
            BusinessRoleItem.app_role_external_id == item.app_role_external_id,
        ))).first()
        if existing is not None and existing.role_id != role_id:
            other = await session.get(BusinessRole, existing.role_id)
            raise AccessPilotError("ENTITLEMENT_ALREADY_MAPPED", f"This entitlement is already mapped to the Business Role \"{other.name if other else existing.role_id}\".", 409)


async def _hydrate_item(session: AsyncSession, item: BusinessRoleItem) -> BusinessRoleItemResponse:
    provider_id, resource_name, _ = await _resolve_target(session, item.resource_type, item.resource_id)
    provider = await session.get(IdentityProvider, provider_id)
    resource_code = naming_convention = None
    model = _RESOURCE_MODELS.get(item.resource_type)
    if model is not None:
        resource = await session.get(model, item.resource_id)
        if resource is not None:
            resource_code, naming_convention = resource.resource_code, resource.naming_convention
    if item.resource_type == "APPLICATION" and item.app_role_external_id:
        application = await session.get(Application, item.resource_id)
        role_name = _app_role_name(application, item.app_role_external_id)
        if role_name:
            resource_name = f"{resource_name} — {role_name}"
    return BusinessRoleItemResponse(
        id=item.id, resource_type=item.resource_type, resource_id=item.resource_id, resource_display_name=resource_name,
        app_role_external_id=item.app_role_external_id, it_role_label=item.it_role_label,
        provider_id=provider_id, provider_name=provider.name if provider else None,
        resource_code=resource_code, naming_convention=naming_convention,
    )


async def _hydrate_owners(session: AsyncSession, role_id: UUID) -> list[BusinessRoleOwnerInfo]:
    rows = list((await session.scalars(select(BusinessRoleOwner).where(BusinessRoleOwner.role_id == role_id).order_by(BusinessRoleOwner.created_at))).all())
    owners = []
    for row in rows:
        user = await session.get(User, row.user_id)
        owners.append(BusinessRoleOwnerInfo(user_id=row.user_id, display_name=user.display_name if user else None, email=user.email if user else None))
    return owners


async def _apply_owners(session: AsyncSession, role: BusinessRole, owner_ids: list[UUID], actor_id: UUID | None) -> None:
    unique_ids = list(dict.fromkeys(owner_ids))
    for user_id in unique_ids:
        if not await session.get(User, user_id):
            raise AccessPilotError("USER_NOT_FOUND", "One of the selected owners was not found.", 404)
    for existing in list((await session.scalars(select(BusinessRoleOwner).where(BusinessRoleOwner.role_id == role.id))).all()):
        await session.delete(existing)
    await session.flush()
    for user_id in unique_ids:
        session.add(BusinessRoleOwner(role_id=role.id, user_id=user_id, assigned_by=actor_id))
    await session.flush()


async def _require_role_owner(session: AsyncSession, role_id: UUID, actor_subject: str) -> tuple[BusinessRole, UUID]:
    """Mirrors app.services.packages._require_owner exactly — no new permission, the caller just needs a real
    AccessPilot account AND a BusinessRoleOwner row for this specific role."""
    role = await get_business_role(session, role_id)
    actor_id = await _resolve_internal_user_id(session, actor_subject)
    is_owner = actor_id is not None and (await session.scalars(select(BusinessRoleOwner.id).where(BusinessRoleOwner.role_id == role_id, BusinessRoleOwner.user_id == actor_id))).first() is not None
    if not is_owner:
        raise AccessPilotError("ACCESS_DENIED", "Only an owner of this Business Role can manage it here.", 403)
    return role, actor_id


async def list_owned_business_roles(session: AsyncSession, actor_subject: str) -> list[BusinessRoleResponse]:
    actor_id = await _resolve_internal_user_id(session, actor_subject)
    if actor_id is None:
        return []
    roles = list((await session.scalars(select(BusinessRole).join(BusinessRoleOwner, BusinessRoleOwner.role_id == BusinessRole.id).where(BusinessRoleOwner.user_id == actor_id).order_by(BusinessRole.name))).all())
    return [await _to_response(session, role) for role in roles]


async def owner_rename_business_role(session: AsyncSession, role_id: UUID, new_name: str, actor_subject: str, request_id: str) -> BusinessRoleResponse:
    """The owner portal's first of two powers, mirroring owner_rename_package. Renaming only; status, items,
    owners, approvers and everything else about the role stays exactly as an Admin set it."""
    role, actor_id = await _require_role_owner(session, role_id, actor_subject)
    if new_name != role.name:
        clash = (await session.execute(select(BusinessRole).where(BusinessRole.name == new_name, BusinessRole.id != role_id))).scalars().first()
        if clash:
            raise AccessPilotError("BUSINESS_ROLE_ALREADY_EXISTS", "A Business Role with this name already exists.", 409)
        old_name = role.name
        role.name = new_name
        await record_audit(session, action="BUSINESS_ROLE_RENAMED_BY_OWNER", target_type="BUSINESS_ROLE", target_id=role_id, actor_user_id=actor_id, request_id=request_id, metadata={"old_name": old_name, "new_name": new_name})
    await session.commit()
    await session.refresh(role)
    return await _to_response(session, role)


async def owner_remove_item(session: AsyncSession, role_id: UUID, item_id: UUID, actor_subject: str, request_id: str) -> BusinessRoleResponse:
    """The owner portal's second power, mirroring owner_remove_item for packages: remove ONE mapped item. Only
    affects FUTURE assignments of this role; access already granted is untouched. A role can never be emptied
    this way — ask an Admin to archive/delete it instead."""
    role, actor_id = await _require_role_owner(session, role_id, actor_subject)
    items = list((await session.scalars(select(BusinessRoleItem).where(BusinessRoleItem.role_id == role_id))).all())
    target = next((item for item in items if item.id == item_id), None)
    if target is None:
        raise AccessPilotError("BUSINESS_ROLE_ITEM_NOT_FOUND", "That item is not mapped to this Business Role.", 404)
    if len(items) <= 1:
        raise AccessPilotError("BUSINESS_ROLE_MUST_HAVE_ITEM", "A Business Role must keep at least one mapped item. Ask an administrator to delete the role instead.", 409)
    await session.delete(target)
    await record_audit(session, action="BUSINESS_ROLE_ITEM_REMOVED_BY_OWNER", target_type="BUSINESS_ROLE", target_id=role_id, actor_user_id=actor_id, request_id=request_id, metadata={"resource_type": target.resource_type, "resource_id": str(target.resource_id), "app_role_external_id": target.app_role_external_id})
    await session.commit()
    await session.refresh(role)
    return await _to_response(session, role)


_HELD_STATUSES = ("ELIGIBLE", "PENDING_APPROVAL", "ACTIVE")


async def _assigned_user_count(session: AsyncSession, role_id: UUID) -> int:
    rows = (await session.execute(select(AccessAssignment.user_id).where(AccessAssignment.business_role_id == role_id, AccessAssignment.status.in_(_HELD_STATUSES)).distinct())).all()
    return len(rows)


async def _to_response(session: AsyncSession, role: BusinessRole) -> BusinessRoleResponse:
    items = list((await session.scalars(select(BusinessRoleItem).where(BusinessRoleItem.role_id == role.id).order_by(BusinessRoleItem.created_at))).all())
    return BusinessRoleResponse(
        id=role.id, name=role.name, description=role.description, role_type=role.role_type, department=role.department,
        status=role.status, risk_level=role.risk_level, is_privileged=role.is_privileged,
        items=[await _hydrate_item(session, item) for item in items], owners=await _hydrate_owners(session, role.id),
        default_approver_id=role.default_approver_id, default_fallback_approver_id=role.default_fallback_approver_id,
        fallback_unlock_hours=role.fallback_unlock_hours, review_frequency_days=role.review_frequency_days,
        assigned_user_count=await _assigned_user_count(session, role.id), created_at=role.created_at, updated_at=role.updated_at,
    )


async def create_business_role(session: AsyncSession, data: BusinessRoleCreate, actor_subject: str, request_id: str) -> BusinessRoleResponse:
    existing = (await session.execute(select(BusinessRole).where(BusinessRole.name == data.name))).scalars().first()
    if existing:
        raise AccessPilotError("BUSINESS_ROLE_ALREADY_EXISTS", "A Business Role with this name already exists.", 409)
    await _validate_items(session, None, data.items)

    role = BusinessRole(
        name=data.name, description=data.description, role_type=data.role_type, department=data.department,
        status="DRAFT", risk_level=data.risk_level, is_privileged=data.is_privileged,
        default_approver_id=data.default_approver_id, default_fallback_approver_id=data.default_fallback_approver_id,
        fallback_unlock_hours=data.fallback_unlock_hours, review_frequency_days=data.review_frequency_days,
    )
    session.add(role)
    await session.flush()
    for item in data.items:
        session.add(BusinessRoleItem(role_id=role.id, resource_type=item.resource_type, resource_id=item.resource_id, app_role_external_id=item.app_role_external_id, it_role_label=item.it_role_label))
    await session.flush()

    actor_id = await _resolve_internal_user_id(session, actor_subject)
    await _apply_owners(session, role, data.owner_ids, actor_id)
    await record_audit(session, action="BUSINESS_ROLE_CREATED", target_type="BUSINESS_ROLE", target_id=role.id, actor_user_id=actor_id, request_id=request_id, metadata={"name": role.name, "item_count": len(data.items)})
    await session.commit()
    await session.refresh(role)
    return await _to_response(session, role)


async def get_business_role(session: AsyncSession, role_id: UUID) -> BusinessRole:
    role = await session.get(BusinessRole, role_id)
    if not role:
        raise AccessPilotError("BUSINESS_ROLE_NOT_FOUND", "The Business Role was not found.", 404)
    return role


async def get_business_role_response(session: AsyncSession, role_id: UUID) -> BusinessRoleResponse:
    return await _to_response(session, await get_business_role(session, role_id))


async def list_business_roles(session: AsyncSession) -> list[BusinessRoleResponse]:
    roles = list((await session.scalars(select(BusinessRole).order_by(BusinessRole.name))).all())
    return [await _to_response(session, role) for role in roles]


async def update_business_role(session: AsyncSession, role_id: UUID, data: BusinessRoleUpdate, actor_subject: str, request_id: str) -> BusinessRoleResponse:
    role = await get_business_role(session, role_id)
    fields = data.model_dump(exclude_unset=True)

    if "name" in fields and fields["name"] != role.name:
        existing = (await session.execute(select(BusinessRole).where(BusinessRole.name == fields["name"], BusinessRole.id != role_id))).scalars().first()
        if existing:
            raise AccessPilotError("BUSINESS_ROLE_ALREADY_EXISTS", "A Business Role with this name already exists.", 409)
        role.name = fields["name"]

    for field in ("description", "role_type", "department", "risk_level", "is_privileged", "default_approver_id", "default_fallback_approver_id", "fallback_unlock_hours", "review_frequency_days"):
        if field in fields:
            setattr(role, field, fields[field])

    if "status" in fields and fields["status"] != role.status:
        if role.status == "ARCHIVED" and fields["status"] != "ARCHIVED":
            raise AccessPilotError("VALIDATION_ERROR", "An archived Business Role cannot be reactivated — create a new one.", 422)
        role.status = fields["status"]

    if data.items is not None:
        await _validate_items(session, role_id, data.items)
        for existing_item in list((await session.scalars(select(BusinessRoleItem).where(BusinessRoleItem.role_id == role_id))).all()):
            await session.delete(existing_item)
        await session.flush()
        for item in data.items:
            session.add(BusinessRoleItem(role_id=role_id, resource_type=item.resource_type, resource_id=item.resource_id, app_role_external_id=item.app_role_external_id, it_role_label=item.it_role_label))
        await session.flush()

    actor_id = await _resolve_internal_user_id(session, actor_subject)
    if data.owner_ids is not None:
        await _apply_owners(session, role, data.owner_ids, actor_id)
    await record_audit(session, action="BUSINESS_ROLE_UPDATED", target_type="BUSINESS_ROLE", target_id=role_id, actor_user_id=actor_id, request_id=request_id, metadata={k: v for k, v in fields.items() if k != "items"})
    await session.commit()
    await session.refresh(role)
    return await _to_response(session, role)


async def delete_business_role(session: AsyncSession, role_id: UUID, actor_subject: str, request_id: str) -> BusinessRoleResponse | None:
    """Deletes outright if nobody has ever been assigned this role (Step 3 adds that history); until then, every
    Business Role is safe to hard-delete. Once assignment history exists this will archive instead, exactly like
    delete_package()."""
    role = await get_business_role(session, role_id)
    actor_id = await _resolve_internal_user_id(session, actor_subject)
    has_history = await _assigned_user_count(session, role_id) > 0

    if has_history:
        role.status = "ARCHIVED"
        await record_audit(session, action="BUSINESS_ROLE_ARCHIVED", target_type="BUSINESS_ROLE", target_id=role.id, actor_user_id=actor_id, request_id=request_id)
        await session.commit()
        await session.refresh(role)
        return await _to_response(session, role)

    for item in list((await session.scalars(select(BusinessRoleItem).where(BusinessRoleItem.role_id == role_id))).all()):
        await session.delete(item)
    for owner in list((await session.scalars(select(BusinessRoleOwner).where(BusinessRoleOwner.role_id == role_id))).all()):
        await session.delete(owner)
    await session.flush()
    await record_audit(session, action="BUSINESS_ROLE_DELETED", target_type="BUSINESS_ROLE", target_id=role.id, actor_user_id=actor_id, request_id=request_id, metadata={"name": role.name})
    await session.delete(role)
    await session.commit()
    return None


# ---------------------------------------------------------------- entitlement catalog (reference fields + unmapped list)

async def set_resource_reference(session: AsyncSession, resource_type: str, resource_id: UUID, data: ResourceReferenceUpdate, actor_subject: str, request_id: str):
    model = _RESOURCE_MODELS.get(resource_type)
    if model is None:
        raise AccessPilotError("VALIDATION_ERROR", "resource_type must be GROUP, ROLE, or APPLICATION.", 422)
    resource = await session.get(model, resource_id)
    if resource is None:
        raise AccessPilotError("RESOURCE_NOT_FOUND", "The resource was not found.", 404)
    if "resource_code" in data.model_fields_set:
        new_code = data.resource_code or None
        if new_code is not None and new_code != resource.resource_code:
            existing = (await session.execute(select(model).where(model.resource_code == new_code, model.id != resource_id))).scalars().first()
            if existing:
                raise AccessPilotError("RESOURCE_CODE_TAKEN", "This resource code is already used by another resource.", 409)
        resource.resource_code = new_code
    if "naming_convention" in data.model_fields_set:
        resource.naming_convention = data.naming_convention or None
    actor_id = await _resolve_internal_user_id(session, actor_subject)
    await record_audit(session, action="RESOURCE_REFERENCE_UPDATED", target_type=resource_type, target_id=resource.id, actor_user_id=actor_id, request_id=request_id, metadata={"resource_code": resource.resource_code, "naming_convention": resource.naming_convention})
    await session.commit()
    await session.refresh(resource)
    return resource


async def list_unmapped_entitlements(session: AsyncSession) -> list[BusinessRoleItemResponse]:
    """Every Group / Role / Application (whole app, not per-app-role) not currently mapped to any Business Role —
    a live query, never a stored flag, matching this app's convention for views of current state (see
    AccessReview's dashboard, SoD's detective scan)."""
    mapped = {(item.resource_type, item.resource_id, item.app_role_external_id) for item in (await session.scalars(select(BusinessRoleItem))).all()}
    results: list[BusinessRoleItemResponse] = []
    for resource_type, model in _RESOURCE_MODELS.items():
        for resource in (await session.scalars(select(model))).all():
            if (resource_type, resource.id, None) in mapped:
                continue
            provider = await session.get(IdentityProvider, resource.provider_id)
            results.append(BusinessRoleItemResponse(
                id=resource.id, resource_type=resource_type, resource_id=resource.id, resource_display_name=resource.name,
                app_role_external_id=None, it_role_label=None, provider_id=resource.provider_id,
                provider_name=provider.name if provider else None, resource_code=resource.resource_code, naming_convention=resource.naming_convention,
            ))
    return results


# ---------------------------------------------------------------- assignment (Step 3)

async def assign_business_role(session: AsyncSession, role_id: UUID, data: BusinessRoleAssignCreate, actor_subject: str, request_id: str) -> BusinessRoleAssignResponse:
    """Admin direct-assign: fans out to one ordinary create_assignment() call per mapped item, exactly like
    assign_package() does for AccessPackage — approval, activation, SoD, and notifications all apply with no new
    code in those systems. Every item this call creates shares one role_assignment_id batch id and carries
    business_role_id, the same provenance tag birthright/group-role-mapping grants already use."""
    role = await get_business_role(session, role_id)
    if role.status != "ACTIVE":
        raise AccessPilotError("BUSINESS_ROLE_NOT_ACTIVE", "This Business Role is not active and cannot be assigned.", 409)
    items = list((await session.scalars(select(BusinessRoleItem).where(BusinessRoleItem.role_id == role_id))).all())
    if not items:
        raise AccessPilotError("BUSINESS_ROLE_EMPTY", "This Business Role has no mapped entitlements.", 409)
    if not await session.get(User, data.user_id):
        raise AccessPilotError("USER_NOT_FOUND", "The user was not found.", 404)

    batch_id = uuid4()
    results: list[BusinessRoleAssignItemResult] = []
    for item in items:
        payload = AssignmentCreate(
            user_id=data.user_id, resource_type=item.resource_type, resource_id=item.resource_id,
            app_role_external_id=item.app_role_external_id, assignment_type=data.assignment_type,
            start_time=data.start_time, expiration_time=data.expiration_time,
            approver_id=data.approver_id, justification=data.justification,
        )
        try:
            assignment, hydrated = await create_assignment(session, payload, actor_subject, request_id, check_sod_at_creation=True, business_role_id=role.id, role_assignment_id=batch_id)
        except AccessPilotError as exc:
            results.append(BusinessRoleAssignItemResult(item_id=item.id, resource_type=item.resource_type, resource_id=item.resource_id, status="FAILED", error_code=exc.code, error_message=exc.message))
            continue
        results.append(BusinessRoleAssignItemResult(item_id=item.id, resource_type=item.resource_type, resource_id=item.resource_id, status="CREATED", assignment=to_response(assignment, hydrated)))

    actor_id = await _resolve_internal_user_id(session, actor_subject)
    created_count = sum(1 for r in results if r.status == "CREATED")
    await record_audit(session, action="BUSINESS_ROLE_ASSIGNED", target_type="BUSINESS_ROLE", target_id=role_id, actor_user_id=actor_id, request_id=request_id, metadata={"user_id": str(data.user_id), "role_assignment_id": str(batch_id), "created": created_count, "failed": len(results) - created_count})
    await session.commit()
    user = await session.get(User, data.user_id)
    return BusinessRoleAssignResponse(role_id=role_id, user_id=data.user_id, user_display_name=user.display_name if user else None, role_assignment_id=batch_id, results=results)


async def list_role_holders(session: AsyncSession, role_id: UUID) -> list[BusinessRoleHolder]:
    """Every person currently holding this Business Role, grouped by the role_assignment_id batch that granted it
    — the effective-access view. One row per batch (normally one per holder, unless assigned more than once)."""
    rows = list((await session.scalars(select(AccessAssignment).where(AccessAssignment.business_role_id == role_id, AccessAssignment.status.in_(_HELD_STATUSES)).order_by(AccessAssignment.created_at))).all())
    batches: dict[UUID, list[AccessAssignment]] = {}
    for row in rows:
        batches.setdefault(row.role_assignment_id, []).append(row)

    holders: list[BusinessRoleHolder] = []
    for batch_id, batch_rows in batches.items():
        user = await session.get(User, batch_rows[0].user_id)
        item_results = []
        for assignment in batch_rows:
            hydrated = await hydrate_display_fields(session, assignment)
            item_results.append(BusinessRoleAssignItemResult(item_id=assignment.id, resource_type=assignment.resource_type, resource_id=assignment.resource_id, status=assignment.status, assignment=to_response(assignment, hydrated)))
        holders.append(BusinessRoleHolder(user_id=batch_rows[0].user_id, user_display_name=user.display_name if user else None, user_email=user.email if user else None, role_assignment_id=batch_id, assigned_at=min(a.created_at for a in batch_rows), items=item_results))
    holders.sort(key=lambda h: h.assigned_at, reverse=True)
    return holders


def _group_role_batch_rows(rows) -> list[RoleAssignmentBatch]:
    grouped: dict[UUID, RoleAssignmentBatch] = {}
    for role_assignment_id, role_id, user_id, assignment_id, role_name in rows:
        batch = grouped.get(role_assignment_id)
        if batch is None:
            batch = RoleAssignmentBatch(role_assignment_id=role_assignment_id, role_id=role_id, role_name=role_name, user_id=user_id, assignment_ids=[])
            grouped[role_assignment_id] = batch
        batch.assignment_ids.append(assignment_id)
    return list(grouped.values())


async def get_business_role_analytics(session: AsyncSession) -> BusinessRoleAnalytics:
    """Plan Step 6 — a live-computed stats panel, same convention as AccessReview's own dashboard and SoD's
    detective scan: nothing here is stored or cached, so it's always correct, never stale and never needs a
    migration/backfill when the underlying data changes."""
    roles = list((await session.scalars(select(BusinessRole))).all())
    status_counts = {"DRAFT": 0, "ACTIVE": 0, "DISABLED": 0, "ARCHIVED": 0}
    for role in roles:
        status_counts[role.status] = status_counts.get(role.status, 0) + 1
    privileged_roles = sum(1 for role in roles if role.is_privileged)

    owned_role_ids = set((await session.scalars(select(BusinessRoleOwner.role_id).distinct())).all())
    roles_with_no_owner = sum(1 for role in roles if role.id not in owned_role_ids)

    unmapped_entitlements = len(await list_unmapped_entitlements(session))

    total_assigned_users = len((await session.execute(
        select(AccessAssignment.user_id).where(AccessAssignment.business_role_id.isnot(None), AccessAssignment.status.in_(_HELD_STATUSES)).distinct()
    )).all())

    # Open SoD conflicts touching a Business Role: reuses the existing live violations scan (never a second
    # detection path) and reads back which BUSINESS_ROLE entities belong to a policy with at least one real,
    # un-excepted violation right now.
    from app.services.sod import get_sod_violations
    violations = await get_sod_violations(session)
    open_policy_ids = {v.policy_id for v in violations if not v.exception_active}
    roles_with_open_sod_conflicts = 0
    if open_policy_ids:
        role_ids = (await session.scalars(select(SodPolicyEntity.entity_id).where(SodPolicyEntity.entity_type == "BUSINESS_ROLE", SodPolicyEntity.sod_policy_id.in_(open_policy_ids)).distinct())).all()
        roles_with_open_sod_conflicts = len(set(role_ids))

    top_rows = (await session.execute(
        select(AccessAssignment.business_role_id, func.count(func.distinct(AccessAssignment.user_id)))
        .where(AccessAssignment.business_role_id.isnot(None), AccessAssignment.status.in_(_HELD_STATUSES))
        .group_by(AccessAssignment.business_role_id).order_by(func.count(func.distinct(AccessAssignment.user_id)).desc()).limit(5)
    )).all()
    top_roles_by_holders: list[BusinessRoleTally] = []
    for role_id, count in top_rows:
        role = await session.get(BusinessRole, role_id)
        top_roles_by_holders.append(BusinessRoleTally(name=role.name if role else str(role_id), count=count))

    return BusinessRoleAnalytics(
        total_roles=len(roles), active_roles=status_counts["ACTIVE"], draft_roles=status_counts["DRAFT"],
        disabled_roles=status_counts["DISABLED"], archived_roles=status_counts["ARCHIVED"], privileged_roles=privileged_roles,
        roles_with_no_owner=roles_with_no_owner, roles_with_open_sod_conflicts=roles_with_open_sod_conflicts,
        unmapped_entitlements=unmapped_entitlements, total_assigned_users=total_assigned_users, top_roles_by_holders=top_roles_by_holders,
    )


async def list_role_assignment_batches(session: AsyncSession) -> list[RoleAssignmentBatch]:
    """All Business Role assignment batches — Admin-only (see BUSINESS_ROLE_READ), used by the Assignments admin
    page to collapse one role's mapped-item rows into a single row, mirroring packages' list_assignment_batches."""
    rows = (await session.execute(
        select(AccessAssignment.role_assignment_id, AccessAssignment.business_role_id, AccessAssignment.user_id, AccessAssignment.id, BusinessRole.name)
        .join(BusinessRole, BusinessRole.id == AccessAssignment.business_role_id)
        .where(AccessAssignment.role_assignment_id.isnot(None))
        .order_by(AccessAssignment.created_at)
    )).all()
    return _group_role_batch_rows(rows)


async def list_my_role_assignment_batches(session: AsyncSession, actor_subject: str) -> list[RoleAssignmentBatch]:
    """Business Role assignment batches where the caller is the designated approver OR the configured fallback
    approver — available to any authenticated user, used by My Approvals. A batch's items always share one approver
    pair (assign_business_role applies the same approver_id/fallback to every item), so filtering by either
    naturally scopes to whole batches. Mirrors packages' list_my_assignment_batches exactly."""
    actor_id = await _resolve_internal_user_id(session, actor_subject)
    if actor_id is None:
        return []
    rows = (await session.execute(
        select(AccessAssignment.role_assignment_id, AccessAssignment.business_role_id, AccessAssignment.user_id, AccessAssignment.id, BusinessRole.name)
        .join(BusinessRole, BusinessRole.id == AccessAssignment.business_role_id)
        .where(AccessAssignment.role_assignment_id.isnot(None), or_(AccessAssignment.approved_by == actor_id, AccessAssignment.fallback_approver_id == actor_id))
        .order_by(AccessAssignment.created_at)
    )).all()
    return _group_role_batch_rows(rows)
