from __future__ import annotations

from typing import Optional
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AccessPilotError
from app.models import AccessAssignment, AccessPackage, AccessPackageAssignment, AccessPackageItem, Application, BirthrightPolicy, Group, IdentityProvider, Role, SodPolicy, SodPolicyEntity, User, UserGroup
from app.providers.entra import EntraProvider
from app.providers.graph_client import GraphError
from app.schemas.directory import GroupAccessSummary, NamedPolicyRef, UserAccessItem, UserAccessSummary, UserLicense


async def list_users(session: AsyncSession, query: Optional[str] = None) -> list[User]:
    statement = select(User).order_by(User.display_name)
    if query:
        like = f"%{query}%"
        statement = statement.where(User.display_name.ilike(like) | User.email.ilike(like))
    return list((await session.scalars(statement)).all())


async def get_user(session: AsyncSession, user_id: UUID) -> User:
    user = await session.get(User, user_id)
    if not user:
        raise AccessPilotError("USER_NOT_FOUND", "The user was not found.", 404)
    return user


async def list_groups(session: AsyncSession, query: Optional[str] = None) -> list[Group]:
    statement = select(Group).order_by(Group.name)
    if query:
        statement = statement.where(Group.name.ilike(f"%{query}%"))
    return list((await session.scalars(statement)).all())


async def get_group(session: AsyncSession, group_id: UUID) -> Group:
    group = await session.get(Group, group_id)
    if not group:
        raise AccessPilotError("GROUP_NOT_FOUND", "The group was not found.", 404)
    return group


async def list_group_members(session: AsyncSession, group_id: UUID) -> list[User]:
    await get_group(session, group_id)
    statement = select(User).join(UserGroup, UserGroup.user_id == User.id).where(UserGroup.group_id == group_id).order_by(User.display_name)
    return list((await session.scalars(statement)).all())


async def get_group_access_summary(session: AsyncSession, group_id: UUID) -> GroupAccessSummary:
    """Answers "what's actually attached to this group" — real synced membership, how many of those are
    AccessPilot-tracked grants (vs. membership added directly in Entra/Okta), and every OTHER piece of
    governance config that references this group by resource/entity id: birthright policies that grant it,
    SoD rules that treat it as a conflict-side entity, and access packages that bundle it alongside other
    resources (packages are the natural "which apps/roles travel with this group" answer, since a group has no
    native "associated app/role" concept of its own)."""
    await get_group(session, group_id)

    member_count = (await session.execute(select(func.count()).select_from(UserGroup).where(UserGroup.group_id == group_id))).scalar_one()
    active_assignment_count = (await session.execute(select(func.count()).select_from(AccessAssignment).where(AccessAssignment.resource_type == "GROUP", AccessAssignment.resource_id == group_id, AccessAssignment.status == "ACTIVE"))).scalar_one()

    birthright_rows = (await session.execute(select(BirthrightPolicy.id, BirthrightPolicy.name).where(BirthrightPolicy.resource_type == "GROUP", BirthrightPolicy.resource_id == group_id, BirthrightPolicy.status == "ACTIVE"))).all()
    sod_rows = (await session.execute(
        select(SodPolicy.id, SodPolicy.name).join(SodPolicyEntity, SodPolicyEntity.sod_policy_id == SodPolicy.id)
        .where(SodPolicyEntity.entity_type == "GROUP", SodPolicyEntity.entity_id == group_id, SodPolicy.status == "ACTIVE")
        .distinct()
    )).all()
    package_rows = (await session.execute(
        select(AccessPackage.id, AccessPackage.name).join(AccessPackageItem, AccessPackageItem.package_id == AccessPackage.id)
        .where(AccessPackageItem.resource_type == "GROUP", AccessPackageItem.resource_id == group_id)
        .distinct()
    )).all()

    return GroupAccessSummary(
        member_count=member_count,
        active_assignment_count=active_assignment_count,
        birthright_policies=[NamedPolicyRef(id=row.id, name=row.name) for row in birthright_rows],
        sod_policies=[NamedPolicyRef(id=row.id, name=row.name) for row in sod_rows],
        access_packages=[NamedPolicyRef(id=row.id, name=row.name) for row in package_rows],
    )


async def list_roles(session: AsyncSession, query: Optional[str] = None) -> list[Role]:
    statement = select(Role).order_by(Role.name)
    if query:
        statement = statement.where(Role.name.ilike(f"%{query}%"))
    return list((await session.scalars(statement)).all())


async def list_applications(session: AsyncSession, query: Optional[str] = None) -> list[Application]:
    statement = select(Application).order_by(Application.name)
    if query:
        statement = statement.where(Application.name.ilike(f"%{query}%"))
    return list((await session.scalars(statement)).all())


async def get_user_access_summary(session: AsyncSession, user_id: UUID) -> UserAccessSummary:
    from app.services.assignments import _app_role_name, hydrate_display_fields  # local import: avoids a top-level cycle (assignments.py -> provider_configuration.py, not back to this module)
    from app.services.provider_configuration import _connector

    user = await get_user(session, user_id)

    assignments = list((await session.scalars(
        select(AccessAssignment).where(AccessAssignment.user_id == user_id, AccessAssignment.status.in_(("ACTIVE", "SCHEDULED", "PENDING_APPROVAL"))).order_by(AccessAssignment.created_at.desc())
    )).all())
    items: list[UserAccessItem] = []
    tracked_group_ids: set[UUID] = set()
    tracked_app_keys: set[tuple[UUID, Optional[str]]] = set()
    for assignment in assignments:
        hydrated = await hydrate_display_fields(session, assignment)
        package_name = (await session.execute(
            select(AccessPackage.name).join(AccessPackageAssignment, AccessPackageAssignment.package_id == AccessPackage.id).where(AccessPackageAssignment.assignment_id == assignment.id)
        )).scalar_one_or_none()
        items.append(UserAccessItem(
            id=assignment.id, resource_type=assignment.resource_type, resource_display_name=hydrated.get("resource_display_name"),
            status=assignment.status, assignment_type=assignment.assignment_type, expiration_time=assignment.expiration_time, package_name=package_name,
        ))
        if assignment.status == "ACTIVE" and assignment.resource_type == "GROUP":
            tracked_group_ids.add(assignment.resource_id)
        if assignment.status == "ACTIVE" and assignment.resource_type == "APPLICATION":
            tracked_app_keys.add((assignment.resource_id, assignment.app_role_external_id))

    # Real synced group membership — catches groups the user belongs to that were added directly in Entra,
    # not just ones AccessPilot itself granted. UserGroup reflects the last full sync, source-agnostic.
    member_groups = (await session.execute(select(Group).join(UserGroup, UserGroup.group_id == Group.id).where(UserGroup.user_id == user_id))).scalars().all()
    for group in member_groups:
        if group.id not in tracked_group_ids:
            items.append(UserAccessItem(resource_type="GROUP", resource_display_name=group.name, status="ACTIVE", assignment_type="DIRECT", expiration_time=None, source="DIRECT_IN_ENTRA"))

    licenses: list[UserLicense] = []
    provider = await session.get(IdentityProvider, user.provider_id)
    if provider:
        connector = _connector(provider)
        if isinstance(connector, EntraProvider):
            try:
                licenses = [UserLicense(**entry) for entry in await connector.get_user_licenses(user.external_id)]
            except GraphError:
                licenses = []

            # Real live application role assignments — same idea as groups above, but there's no synced per-user
            # table for these, so read directly from Graph. Never blocks the rest of the summary if it fails.
            try:
                live_app_roles = await connector.get_user_app_role_assignments(user.external_id)
            except GraphError:
                live_app_roles = []
            for entry in live_app_roles:
                application = (await session.execute(select(Application).where(Application.provider_id == provider.id, Application.external_id == entry["resource_id"]))).scalars().first()
                key = (application.id if application else None, entry["app_role_id"] or None)
                if key in tracked_app_keys:
                    continue
                role_name = _app_role_name(application, entry["app_role_id"]) if application else None
                display_name = f"{entry['resource_display_name']} — {role_name}" if role_name else entry["resource_display_name"]
                items.append(UserAccessItem(resource_type="APPLICATION", resource_display_name=display_name, status="ACTIVE", assignment_type="DIRECT", expiration_time=None, source="DIRECT_IN_ENTRA"))

    return UserAccessSummary(assignments=items, licenses=licenses)
