from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AccessPilotError
from app.models import AccessAssignment, AccessPackage, AccessPackageAssignment, AccessPackageEligibility, AccessPackageItem, Application, Group, User, UserGroup
from app.schemas.assignments import AssignmentCreate
from app.schemas.packages import PackageAssignCreate, PackageAssignItemResult, PackageAssignMemberResult, PackageAssignResponse, PackageAssignmentBatch, PackageCreate, PackageEligibilityPrincipal, PackageEligibilityUpdate, PackageItemCreate, PackageItemResponse, PackageRequestCreate, PackageResponse, PackageUpdate
from app.services.assignments import _app_role_name, _resolve_internal_user_id, _resolve_target, create_assignment, to_response
from app.services.audit import record_audit
from app.services.directory_read import list_group_members


async def _hydrate_item(session: AsyncSession, item: AccessPackageItem) -> PackageItemResponse:
    _, resource_name, _ = await _resolve_target(session, item.resource_type, item.resource_id)
    if item.resource_type == "APPLICATION" and item.app_role_external_id:
        application = await session.get(Application, item.resource_id)
        role_name = _app_role_name(application, item.app_role_external_id)
        if role_name:
            resource_name = f"{resource_name} — {role_name}"
    return PackageItemResponse(id=item.id, resource_type=item.resource_type, resource_id=item.resource_id, resource_display_name=resource_name, app_role_external_id=item.app_role_external_id)


async def _hydrate_eligibility(session: AsyncSession, package_id: UUID) -> list[PackageEligibilityPrincipal]:
    rows = list((await session.scalars(select(AccessPackageEligibility).where(AccessPackageEligibility.package_id == package_id))).all())
    principals: list[PackageEligibilityPrincipal] = []
    for row in rows:
        display_name = None
        if row.principal_type == "USER":
            user = await session.get(User, row.principal_id)
            display_name = user.display_name if user else None
        else:
            group = await session.get(Group, row.principal_id)
            display_name = group.name if group else None
        principals.append(PackageEligibilityPrincipal(principal_type=row.principal_type, principal_id=row.principal_id, display_name=display_name))
    return principals


async def _to_package_response(session: AsyncSession, package: AccessPackage) -> PackageResponse:
    items = list((await session.scalars(select(AccessPackageItem).where(AccessPackageItem.package_id == package.id))).all())
    eligible_principals = await _hydrate_eligibility(session, package.id)
    return PackageResponse(id=package.id, name=package.name, description=package.description, status=package.status, items=[await _hydrate_item(session, item) for item in items], default_approver_id=package.default_approver_id, default_fallback_approver_id=package.default_fallback_approver_id, fallback_unlock_hours=package.fallback_unlock_hours, eligible_principals=eligible_principals, created_at=package.created_at)


async def _validate_items(session: AsyncSession, items: list[PackageItemCreate]) -> None:
    seen: set[tuple[str, UUID, str | None]] = set()
    for item in items:
        key = (item.resource_type, item.resource_id, item.app_role_external_id)
        if key in seen:
            raise AccessPilotError("DUPLICATE_PACKAGE_ITEM", "A package cannot contain the same target twice.", 422)
        seen.add(key)
        await _resolve_target(session, item.resource_type, item.resource_id)
        if item.resource_type == "APPLICATION":
            application = await session.get(Application, item.resource_id)
            if not _app_role_name(application, item.app_role_external_id):
                raise AccessPilotError("APPLICATION_ROLE_NOT_FOUND", "The selected application role was not found.", 404)


async def _apply_package_eligibility(session: AsyncSession, package: AccessPackage, *, principals, default_approver_id: UUID | None, default_fallback_approver_id: UUID | None, fallback_unlock_hours: int | None) -> None:
    """Shared by create_package() and set_package_eligibility(): validates and writes who may self-request the
    package plus its approver/fallback-approver/escalation-window setup. Setup can happen either during creation
    (one combined flow) or afterward via the dedicated eligibility endpoint — both paths behave identically."""
    # Dedupe by (type, id) before anything else — access_package_eligibility has a real unique constraint on this
    # pair, and submitting the same principal twice (trivially easy from the UI: add a row, its picker still
    # shows an already-used option) previously hit that constraint as an unhandled IntegrityError, silently
    # rolling back the whole update — the visible symptom was "I added someone but the count never changed."
    seen: set[tuple[str, object]] = set()
    deduped_principals = []
    for principal in principals:
        key = (principal.principal_type, principal.principal_id)
        if key in seen:
            continue
        seen.add(key)
        deduped_principals.append(principal)
    principals = deduped_principals

    for principal in principals:
        if principal.principal_type == "USER":
            if not await session.get(User, principal.principal_id):
                raise AccessPilotError("USER_NOT_FOUND", "One of the selected users was not found.", 404)
        else:
            if not await session.get(Group, principal.principal_id):
                raise AccessPilotError("GROUP_NOT_FOUND", "One of the selected groups was not found.", 404)
    if default_approver_id is not None and not await session.get(User, default_approver_id):
        raise AccessPilotError("USER_NOT_FOUND", "The selected default approver was not found.", 404)
    if default_fallback_approver_id is not None and not await session.get(User, default_fallback_approver_id):
        raise AccessPilotError("USER_NOT_FOUND", "The selected fallback approver was not found.", 404)

    for existing_row in list((await session.scalars(select(AccessPackageEligibility).where(AccessPackageEligibility.package_id == package.id))).all()):
        await session.delete(existing_row)
    await session.flush()
    for principal in principals:
        session.add(AccessPackageEligibility(package_id=package.id, principal_type=principal.principal_type, principal_id=principal.principal_id))
    package.default_approver_id = default_approver_id
    package.default_fallback_approver_id = default_fallback_approver_id
    package.fallback_unlock_hours = fallback_unlock_hours


async def create_package(session: AsyncSession, data: PackageCreate, actor_subject: str, request_id: str) -> PackageResponse:
    """One combined setup flow: name/description, who can request it, the approver + optional fallback approver
    (and how long the fallback must wait before it may act), and finally the items — all in a single call."""
    existing = (await session.execute(select(AccessPackage).where(AccessPackage.name == data.name))).scalars().first()
    if existing:
        raise AccessPilotError("PACKAGE_ALREADY_EXISTS", "A package with this name already exists.", 409)
    await _validate_items(session, data.items)

    package = AccessPackage(name=data.name, description=data.description, status="ACTIVE")
    session.add(package)
    await session.flush()
    await _apply_package_eligibility(session, package, principals=data.principals, default_approver_id=data.default_approver_id, default_fallback_approver_id=data.default_fallback_approver_id, fallback_unlock_hours=data.fallback_unlock_hours)
    for item in data.items:
        session.add(AccessPackageItem(package_id=package.id, resource_type=item.resource_type, resource_id=item.resource_id, app_role_external_id=item.app_role_external_id))
    await session.flush()

    actor_id = await _resolve_internal_user_id(session, actor_subject)
    await record_audit(session, action="PACKAGE_CREATED", target_type="PACKAGE", target_id=package.id, actor_user_id=actor_id, request_id=request_id, metadata={"item_count": len(data.items), "principal_count": len(data.principals)})
    await session.commit()
    await session.refresh(package)
    return await _to_package_response(session, package)


async def update_package(session: AsyncSession, package_id: UUID, data: PackageUpdate, actor_subject: str, request_id: str) -> PackageResponse:
    """Renames/redescribes the package and/or replaces its item list. Editing items only affects FUTURE
    assignments — it never touches access already granted from this package, exactly like editing any other
    template. Allowed even on a package that already has assignment history."""
    package = await get_package(session, package_id)
    fields = data.model_dump(exclude_unset=True)

    if "name" in fields and fields["name"] != package.name:
        existing = (await session.execute(select(AccessPackage).where(AccessPackage.name == fields["name"], AccessPackage.id != package_id))).scalars().first()
        if existing:
            raise AccessPilotError("PACKAGE_ALREADY_EXISTS", "A package with this name already exists.", 409)
        package.name = fields["name"]

    if "description" in fields:
        package.description = fields["description"]

    if data.items is not None:
        await _validate_items(session, data.items)
        for existing_item in list((await session.scalars(select(AccessPackageItem).where(AccessPackageItem.package_id == package_id))).all()):
            await session.delete(existing_item)
        await session.flush()
        for item in data.items:
            session.add(AccessPackageItem(package_id=package_id, resource_type=item.resource_type, resource_id=item.resource_id, app_role_external_id=item.app_role_external_id))
        await session.flush()

    actor_id = await _resolve_internal_user_id(session, actor_subject)
    await record_audit(session, action="PACKAGE_UPDATED", target_type="PACKAGE", target_id=package_id, actor_user_id=actor_id, request_id=request_id, metadata={"item_count": len(data.items) if data.items is not None else None})
    await session.commit()
    await session.refresh(package)
    return await _to_package_response(session, package)


async def get_package(session: AsyncSession, package_id: UUID) -> AccessPackage:
    package = await session.get(AccessPackage, package_id)
    if not package:
        raise AccessPilotError("PACKAGE_NOT_FOUND", "The package was not found.", 404)
    return package


async def get_package_response(session: AsyncSession, package_id: UUID) -> PackageResponse:
    package = await get_package(session, package_id)
    return await _to_package_response(session, package)


async def list_packages(session: AsyncSession) -> list[PackageResponse]:
    packages = list((await session.scalars(select(AccessPackage).order_by(AccessPackage.name))).all())
    return [await _to_package_response(session, package) for package in packages]


async def delete_package(session: AsyncSession, package_id: UUID, actor_subject: str, request_id: str) -> PackageResponse | None:
    """Deletes the package outright if it has never been assigned to anyone (safe — nothing references it).
    Otherwise archives it instead (status="ARCHIVED", no longer assignable) since assignment history and audit
    records still point at it. Returns the archived PackageResponse, or None when it was actually deleted."""
    package = await get_package(session, package_id)
    actor_id = await _resolve_internal_user_id(session, actor_subject)
    has_history = (await session.execute(select(AccessPackageAssignment.id).where(AccessPackageAssignment.package_id == package_id).limit(1))).scalar_one_or_none()

    if has_history is not None:
        package.status = "ARCHIVED"
        await record_audit(session, action="PACKAGE_ARCHIVED", target_type="PACKAGE", target_id=package.id, actor_user_id=actor_id, request_id=request_id)
        await session.commit()
        await session.refresh(package)
        return await _to_package_response(session, package)

    for item in list((await session.scalars(select(AccessPackageItem).where(AccessPackageItem.package_id == package_id))).all()):
        await session.delete(item)
    await session.delete(package)
    await record_audit(session, action="PACKAGE_DELETED", target_type="PACKAGE", target_id=package_id, actor_user_id=actor_id, request_id=request_id)
    await session.commit()
    return None


async def _assign_package_to_user(session: AsyncSession, package_id: UUID, items: list[AccessPackageItem], user_id: UUID, data: PackageAssignCreate, actor_subject: str, request_id: str) -> tuple[PackageAssignMemberResult, int]:
    batch_id = uuid4()
    results: list[PackageAssignItemResult] = []
    created_count = 0
    # The package's own configured fallback approver (and its escalation wait, if any) applies regardless of which
    # primary approver_id ends up being used for this particular assign/request (see _authorize_decision).
    package = await session.get(AccessPackage, package_id)
    fallback_approver_id = package.default_fallback_approver_id if package and data.approver_id is not None else None
    fallback_unlock_hours = package.fallback_unlock_hours if package and fallback_approver_id else None
    for item in items:
        payload = AssignmentCreate(
            user_id=user_id, resource_type=item.resource_type, resource_id=item.resource_id,
            app_role_external_id=item.app_role_external_id, assignment_type=data.assignment_type,
            start_time=data.start_time, expiration_time=data.expiration_time,
            approver_id=data.approver_id, fallback_approver_id=fallback_approver_id, fallback_unlock_hours=fallback_unlock_hours, justification=data.justification,
        )
        try:
            assignment, hydrated = await create_assignment(session, payload, actor_subject, request_id)
        except AccessPilotError as exc:
            results.append(PackageAssignItemResult(package_item_id=item.id, resource_type=item.resource_type, resource_id=item.resource_id, status="FAILED", error_code=exc.code, error_message=exc.message))
            continue
        session.add(AccessPackageAssignment(package_id=package_id, package_assignment_id=batch_id, assignment_id=assignment.id, user_id=user_id))
        await session.commit()
        created_count += 1
        results.append(PackageAssignItemResult(package_item_id=item.id, resource_type=item.resource_type, resource_id=item.resource_id, status="CREATED", assignment=to_response(assignment, hydrated)))

    user = await session.get(User, user_id)
    member_result = PackageAssignMemberResult(user_id=user_id, user_display_name=user.display_name if user else None, package_assignment_id=batch_id, results=results)
    return member_result, created_count


async def assign_package(session: AsyncSession, package_id: UUID, data: PackageAssignCreate, actor_subject: str, request_id: str) -> PackageAssignResponse:
    package = await get_package(session, package_id)
    if package.status != "ACTIVE":
        raise AccessPilotError("PACKAGE_ARCHIVED", "This package has been archived and can no longer be assigned.", 409)
    items = list((await session.scalars(select(AccessPackageItem).where(AccessPackageItem.package_id == package_id))).all())
    if not items:
        raise AccessPilotError("PACKAGE_EMPTY", "This package has no items.", 409)

    if data.group_id is not None:
        member_ids = [member.id for member in await list_group_members(session, data.group_id)]
        if not member_ids:
            raise AccessPilotError("GROUP_EMPTY", "This group has no members to assign the package to.", 409)
    else:
        assert data.user_id is not None
        member_ids = [data.user_id]

    member_results: list[PackageAssignMemberResult] = []
    total_created = total_failed = 0
    for user_id in member_ids:
        member_result, created_count = await _assign_package_to_user(session, package_id, items, user_id, data, actor_subject, request_id)
        member_results.append(member_result)
        total_created += created_count
        total_failed += len(member_result.results) - created_count

    actor_id = await _resolve_internal_user_id(session, actor_subject)
    await record_audit(session, action="PACKAGE_ASSIGNED", target_type="PACKAGE", target_id=package_id, actor_user_id=actor_id, request_id=request_id, metadata={
        "user_id": str(data.user_id) if data.user_id else None, "group_id": str(data.group_id) if data.group_id else None,
        "member_count": len(member_ids), "created": total_created, "failed": total_failed,
    })
    await session.commit()
    return PackageAssignResponse(package_id=package_id, members=member_results)


def _group_batch_rows(rows) -> list[PackageAssignmentBatch]:
    grouped: dict[UUID, PackageAssignmentBatch] = {}
    for package_assignment_id, package_id, user_id, assignment_id, package_name in rows:
        batch = grouped.get(package_assignment_id)
        if batch is None:
            batch = PackageAssignmentBatch(package_assignment_id=package_assignment_id, package_id=package_id, package_name=package_name, user_id=user_id, assignment_ids=[])
            grouped[package_assignment_id] = batch
        batch.assignment_ids.append(assignment_id)
    return list(grouped.values())


async def list_assignment_batches(session: AsyncSession) -> list[PackageAssignmentBatch]:
    """All package assignment batches — Admin-only (see PACKAGE_READ), used by the Assignments admin page."""
    rows = (await session.execute(
        select(AccessPackageAssignment.package_assignment_id, AccessPackageAssignment.package_id, AccessPackageAssignment.user_id, AccessPackageAssignment.assignment_id, AccessPackage.name)
        .join(AccessPackage, AccessPackage.id == AccessPackageAssignment.package_id)
        .order_by(AccessPackageAssignment.created_at)
    )).all()
    return _group_batch_rows(rows)


async def list_my_assignment_batches(session: AsyncSession, actor_subject: str) -> list[PackageAssignmentBatch]:
    """Package assignment batches where the caller is the designated approver OR the configured fallback approver —
    available to any authenticated user, not just Admins, mirroring list_my_approvals(). A batch's items always
    share one approver pair (assign_package applies the same approver_id/fallback to every item), so filtering by
    either naturally scopes to whole batches."""
    actor_id = await _resolve_internal_user_id(session, actor_subject)
    if actor_id is None:
        return []
    rows = (await session.execute(
        select(AccessPackageAssignment.package_assignment_id, AccessPackageAssignment.package_id, AccessPackageAssignment.user_id, AccessPackageAssignment.assignment_id, AccessPackage.name)
        .join(AccessPackage, AccessPackage.id == AccessPackageAssignment.package_id)
        .join(AccessAssignment, AccessAssignment.id == AccessPackageAssignment.assignment_id)
        .where(or_(AccessAssignment.approved_by == actor_id, AccessAssignment.fallback_approver_id == actor_id))
        .order_by(AccessPackageAssignment.created_at)
    )).all()
    return _group_batch_rows(rows)


async def list_my_package_batches(session: AsyncSession, actor_subject: str) -> list[PackageAssignmentBatch]:
    """Package assignment batches where the caller IS the target user — lets the end-user My Access page group a
    package's several eligible/active items into one row with one Activate/Deactivate button, instead of one row
    per item. Distinct from list_my_assignment_batches(), which is scoped to the designated approver instead."""
    actor_id = await _resolve_internal_user_id(session, actor_subject)
    if actor_id is None:
        return []
    rows = (await session.execute(
        select(AccessPackageAssignment.package_assignment_id, AccessPackageAssignment.package_id, AccessPackageAssignment.user_id, AccessPackageAssignment.assignment_id, AccessPackage.name)
        .join(AccessPackage, AccessPackage.id == AccessPackageAssignment.package_id)
        .where(AccessPackageAssignment.user_id == actor_id)
        .order_by(AccessPackageAssignment.created_at)
    )).all()
    return _group_batch_rows(rows)


async def set_package_eligibility(session: AsyncSession, package_id: UUID, data: PackageEligibilityUpdate, actor_subject: str, request_id: str) -> PackageResponse:
    """Replaces the full set of who may self-request this package (individual users and/or whole groups), and
    sets the approver automatically applied when someone eligible actually requests it (optional — no approver
    means the request lands eligible immediately, exactly like an admin-created assignment with no approver_id).
    An optional fallback approver may also be set — either the primary or the fallback approving is sufficient
    (immediately, or after fallback_unlock_hours has elapsed with no primary response if that's configured); it
    applies to every assignment made from this package, whether via self-request or an admin's direct assign."""
    package = await get_package(session, package_id)
    await _apply_package_eligibility(session, package, principals=data.principals, default_approver_id=data.default_approver_id, default_fallback_approver_id=data.default_fallback_approver_id, fallback_unlock_hours=data.fallback_unlock_hours)

    actor_id = await _resolve_internal_user_id(session, actor_subject)
    await record_audit(session, action="PACKAGE_ELIGIBILITY_UPDATED", target_type="PACKAGE", target_id=package_id, actor_user_id=actor_id, request_id=request_id, metadata={"principal_count": len(data.principals)})
    await session.commit()
    await session.refresh(package)
    return await _to_package_response(session, package)


async def list_requestable_packages(session: AsyncSession, actor_subject: str) -> list[PackageResponse]:
    """Active packages the caller is eligible to self-request — directly named, or a member of an eligible group."""
    actor_id = await _resolve_internal_user_id(session, actor_subject)
    if actor_id is None:
        return []
    member_group_ids = set((await session.execute(select(UserGroup.group_id).where(UserGroup.user_id == actor_id))).scalars().all())
    rows = list((await session.scalars(select(AccessPackageEligibility))).all())
    eligible_package_ids: set[UUID] = set()
    for row in rows:
        if row.principal_type == "USER" and row.principal_id == actor_id:
            eligible_package_ids.add(row.package_id)
        elif row.principal_type == "GROUP" and row.principal_id in member_group_ids:
            eligible_package_ids.add(row.package_id)
    if not eligible_package_ids:
        return []
    packages = list((await session.scalars(select(AccessPackage).where(AccessPackage.id.in_(eligible_package_ids), AccessPackage.status == "ACTIVE").order_by(AccessPackage.name))).all())
    return [await _to_package_response(session, package) for package in packages]


async def request_package(session: AsyncSession, package_id: UUID, data: PackageRequestCreate, actor_subject: str, request_id: str) -> PackageAssignMemberResult:
    """Self-service: an eligible end user requests this package for themselves. Re-checks eligibility server-side
    (never trust the list endpoint alone) and reuses the exact same per-item create_assignment() path admin-driven
    assignment already uses — the only difference is the approver is the package's own default_approver_id."""
    requester_id = await _resolve_internal_user_id(session, actor_subject)
    if requester_id is None:
        raise AccessPilotError("USER_NOT_FOUND", "Your account was not found in the directory.", 404)

    requestable = await list_requestable_packages(session, actor_subject)
    if not any(package.id == package_id for package in requestable):
        raise AccessPilotError("NOT_ELIGIBLE", "You are not eligible to request this package.", 403)

    package = await get_package(session, package_id)
    items = list((await session.scalars(select(AccessPackageItem).where(AccessPackageItem.package_id == package_id))).all())
    if not items:
        raise AccessPilotError("PACKAGE_EMPTY", "This package has no items.", 409)

    assign_data = PackageAssignCreate(
        user_id=requester_id, assignment_type=data.assignment_type, start_time=data.start_time,
        expiration_time=data.expiration_time, approver_id=package.default_approver_id, justification=data.justification,
    )
    member_result, created_count = await _assign_package_to_user(session, package_id, items, requester_id, assign_data, actor_subject, request_id)

    actor_id = await _resolve_internal_user_id(session, actor_subject)
    await record_audit(session, action="PACKAGE_REQUESTED", target_type="PACKAGE", target_id=package_id, actor_user_id=actor_id, request_id=request_id, metadata={
        "user_id": str(requester_id), "package_assignment_id": str(member_result.package_assignment_id),
        "created": created_count, "failed": len(member_result.results) - created_count,
    })
    await session.commit()
    return member_result
