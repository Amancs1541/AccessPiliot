from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AccessPilotError
from app.models import AccessAssignment, Application, Group, IdentityProvider, Role, SyncError, SyncRun, User, UserGroup
from app.providers.base import NormalizedApplication, NormalizedGroup, NormalizedRole, NormalizedUser
from app.providers.graph_client import GraphError
from app.services.audit import record_audit
from app.services.provider_configuration import _connector


async def upsert_user(session: AsyncSession, provider_id: UUID, normalized: NormalizedUser) -> User:
    row = (await session.execute(select(User).where(User.provider_id == provider_id, User.external_id == normalized.external_id))).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if row is None:
        row = User(provider_id=provider_id, external_id=normalized.external_id, email=normalized.email, display_name=normalized.display_name, given_name=normalized.given_name, surname=normalized.surname, department=normalized.department, job_title=normalized.job_title, status=normalized.status, last_synced_at=now)
        session.add(row)
    else:
        row.email, row.display_name, row.given_name, row.surname = normalized.email, normalized.display_name, normalized.given_name, normalized.surname
        row.department, row.job_title, row.status, row.last_synced_at = normalized.department, normalized.job_title, normalized.status, now
    await session.flush()
    return row


async def upsert_group(session: AsyncSession, provider_id: UUID, normalized: NormalizedGroup) -> Group:
    row = (await session.execute(select(Group).where(Group.provider_id == provider_id, Group.external_id == normalized.external_id))).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if row is None:
        row = Group(provider_id=provider_id, external_id=normalized.external_id, name=normalized.name, description=normalized.description, is_privileged=normalized.is_privileged, status=normalized.status, last_synced_at=now)
        session.add(row)
    else:
        row.name, row.description, row.is_privileged, row.status, row.last_synced_at = normalized.name, normalized.description, normalized.is_privileged, normalized.status, now
    await session.flush()
    return row


async def upsert_role(session: AsyncSession, provider_id: UUID, normalized: NormalizedRole) -> Role:
    row = (await session.execute(select(Role).where(Role.provider_id == provider_id, Role.external_id == normalized.external_id))).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if row is None:
        row = Role(provider_id=provider_id, external_id=normalized.external_id, name=normalized.name, description=normalized.description, role_type=normalized.role_type, is_privileged=normalized.is_privileged, status=normalized.status, last_synced_at=now)
        session.add(row)
    else:
        row.name, row.description, row.role_type, row.is_privileged, row.status, row.last_synced_at = normalized.name, normalized.description, normalized.role_type, normalized.is_privileged, normalized.status, now
    await session.flush()
    return row


async def upsert_application(session: AsyncSession, provider_id: UUID, normalized: NormalizedApplication) -> Application:
    row = (await session.execute(select(Application).where(Application.provider_id == provider_id, Application.external_id == normalized.external_id))).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    app_roles_json = [{"id": role.external_id, "name": role.name, "description": role.description} for role in normalized.app_roles]
    if row is None:
        row = Application(provider_id=provider_id, external_id=normalized.external_id, name=normalized.name, status=normalized.status, app_roles=app_roles_json, last_synced_at=now)
        session.add(row)
    else:
        row.name, row.status, row.app_roles, row.last_synced_at = normalized.name, normalized.status, app_roles_json, now
    await session.flush()
    return row


async def _upsert_membership(session: AsyncSession, user_id: UUID, group_id: UUID) -> None:
    row = (await session.execute(select(UserGroup).where(UserGroup.user_id == user_id, UserGroup.group_id == group_id))).scalar_one_or_none()
    if row is None:
        session.add(UserGroup(user_id=user_id, group_id=group_id, source="SYNC"))
        await session.flush()


async def _remove_stale_memberships(session: AsyncSession, group_id: UUID, current_user_ids: set[UUID], request_id: str) -> None:
    rows = (await session.execute(select(UserGroup).where(UserGroup.group_id == group_id))).scalars().all()
    for row in rows:
        if row.user_id not in current_user_ids:
            await session.delete(row)
            # Reconcile: if AccessPilot still thinks this user has ACTIVE access to this group, that's now stale —
            # the real membership is gone (removed directly in Entra, or any other way that bypassed AccessPilot).
            # Correct our own record to match reality rather than leaving a permanently-wrong "ACTIVE" status.
            stale_assignment = (await session.execute(select(AccessAssignment).where(
                AccessAssignment.user_id == row.user_id, AccessAssignment.resource_type == "GROUP",
                AccessAssignment.resource_id == group_id, AccessAssignment.status == "ACTIVE",
            ))).scalars().first()
            if stale_assignment:
                stale_assignment.status = "REVOKED"
                stale_assignment.revoked_at = datetime.now(timezone.utc)
                await record_audit(session, action="ASSIGNMENT_REVOKED", target_type="ASSIGNMENT", target_id=stale_assignment.id, provider_id=stale_assignment.provider_id, request_id=request_id, metadata={"reason": "MEMBERSHIP_REMOVED_OUTSIDE_ACCESSPILOT"})
    await session.flush()


async def run_sync(session: AsyncSession, provider: IdentityProvider, request_id: str) -> SyncRun:
    connector = _connector(provider)
    sync_run = SyncRun(provider_id=provider.id, status="RUNNING", started_at=datetime.now(timezone.utc))
    session.add(sync_run)
    await session.flush()
    await record_audit(session, action="SYNC_STARTED", target_type="PROVIDER", target_id=provider.id, provider_id=provider.id, request_id=request_id)
    await session.commit()

    errors_count = 0
    try:
        users = await connector.get_users()
        user_by_external_id: dict[str, User] = {}
        for normalized_user in users:
            user_by_external_id[normalized_user.external_id] = await upsert_user(session, provider.id, normalized_user)

        groups = await connector.get_groups()
        for normalized_group in groups:
            group_row = await upsert_group(session, provider.id, normalized_group)
            try:
                members = await connector.get_group_members(normalized_group.external_id)
            except GraphError as exc:
                errors_count += 1
                session.add(SyncError(sync_run_id=sync_run.id, resource_type="GROUP_MEMBER", external_id=normalized_group.external_id, error_code=exc.code, error_message=exc.message))
                continue
            member_ids: set[UUID] = set()
            for member in members:
                user_row = user_by_external_id.get(member.external_id) or await upsert_user(session, provider.id, member)
                member_ids.add(user_row.id)
                await _upsert_membership(session, user_row.id, group_row.id)
            await _remove_stale_memberships(session, group_row.id, member_ids, request_id)

        roles = await connector.get_roles()
        for normalized_role in roles:
            await upsert_role(session, provider.id, normalized_role)

        applications = await connector.get_applications()
        for normalized_application in applications:
            await upsert_application(session, provider.id, normalized_application)

        sync_run.status = "COMPLETED"
        sync_run.completed_at = datetime.now(timezone.utc)
        sync_run.users_processed = len(users)
        sync_run.groups_processed = len(groups)
        sync_run.roles_processed = len(roles)
        sync_run.errors_count = errors_count
        provider.last_sync_at = sync_run.completed_at

        await record_audit(session, action="USER_SYNCED", target_type="PROVIDER", target_id=provider.id, provider_id=provider.id, request_id=request_id, metadata={"count": len(users)})
        await record_audit(session, action="GROUP_SYNCED", target_type="PROVIDER", target_id=provider.id, provider_id=provider.id, request_id=request_id, metadata={"count": len(groups)})
        await record_audit(session, action="GROUP_MEMBERSHIP_SYNCED", target_type="PROVIDER", target_id=provider.id, provider_id=provider.id, request_id=request_id)
        await record_audit(session, action="ROLE_SYNCED", target_type="PROVIDER", target_id=provider.id, provider_id=provider.id, request_id=request_id, metadata={"count": len(roles)})
        await record_audit(session, action="APPLICATION_SYNCED", target_type="PROVIDER", target_id=provider.id, provider_id=provider.id, request_id=request_id, metadata={"count": len(applications)})
        await record_audit(session, action="SYNC_COMPLETED", target_type="PROVIDER", target_id=provider.id, provider_id=provider.id, request_id=request_id, metadata={"users": len(users), "groups": len(groups), "roles": len(roles), "applications": len(applications), "errors": errors_count})
        await session.commit()
        await session.refresh(sync_run)
        return sync_run
    except GraphError as exc:
        await session.rollback()
        sync_run_id = sync_run.id
        sync_run = (await session.execute(select(SyncRun).where(SyncRun.id == sync_run_id))).scalar_one()
        sync_run.status = "FAILED"
        sync_run.completed_at = datetime.now(timezone.utc)
        sync_run.errors_count = errors_count + 1
        session.add(SyncError(sync_run_id=sync_run.id, resource_type="SYNC", external_id="-", error_code=exc.code, error_message=exc.message))
        await record_audit(session, action="SYNC_FAILED", target_type="PROVIDER", target_id=provider.id, provider_id=provider.id, request_id=request_id, result="FAILURE", metadata={"code": exc.code})
        await session.commit()
        raise AccessPilotError(exc.code, exc.message, exc.status_code) from exc
