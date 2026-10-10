from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AccessPilotError
from app.db.session import get_db
from app.providers.base import NewGroupRequest, ProviderConflictError
from app.providers.graph_client import GraphError
from app.schemas.directory import ApplicationResponse, GroupAccessSummary, GroupCreate, GroupResponse, RoleResponse, UserAccessSummary, UserAttributeUpdate, UserResponse
from app.schemas.privileged_accounts import LinkedAccountResponse, SetEnabledRequest
from app.schemas.user_hierarchy import UserHierarchyNode, UserHierarchyUpdate
from app.security.auth import AuthenticatedUser, require_authenticated_user, require_permission
from app.services import directory_read
from app.services import privileged_accounts as privileged_accounts_service
from app.services import user_hierarchy as user_hierarchy_service
from app.services.audit import record_audit
from app.services.dashboard import admin_dashboard, get_privileged_role_activation_timeline, get_user_access_segment_members, get_user_access_segments
from app.services.directory_sync import upsert_group
from app.services.provider_configuration import _connector, list_providers

from app.schemas.group_owners import GroupOwnerInfo, GroupOwnerSelfServiceUpdate, GroupOwnersUpdate
from app.services import group_owners as group_owner_service

router = APIRouter(tags=["directory"])
user_read = require_permission("USER_READ")
group_read = require_permission("GROUP_READ")
group_manage = require_permission("GROUP_MANAGE")
role_read = require_permission("ROLE_READ")
dashboard_admin_read = require_permission("DASHBOARD_ADMIN_READ")
dashboard_user_read = require_permission("DASHBOARD_USER_READ")


async def _primary_provider(db: AsyncSession):
    providers = await list_providers(db)
    entra = next((provider for provider in providers if provider.type == "ENTRA"), None)
    return entra or (providers[0] if providers else None)


@router.get("/users", response_model=list[UserResponse])
async def users(q: str | None = Query(default=None), _: AuthenticatedUser = Depends(user_read), db: AsyncSession = Depends(get_db)):
    return await directory_read.label_users_with_provider(db, await directory_read.list_users(db, q))


@router.get("/users/hierarchy-tree", response_model=list[UserHierarchyNode])
async def users_hierarchy_tree(_: AuthenticatedUser = Depends(user_read), db: AsyncSession = Depends(get_db)):
    """Every user, flat — the Org Chart page assembles the actual tree client-side from manager_id. Registered
    before /users/{user_id} so this literal path is never swallowed by that route's own id lookup."""
    return await user_hierarchy_service.get_hierarchy_tree(db)


@router.get("/users/{user_id}", response_model=UserResponse)
async def user_detail(user_id: UUID, _: AuthenticatedUser = Depends(user_read), db: AsyncSession = Depends(get_db)):
    labeled = await directory_read.label_users_with_provider(db, [await directory_read.get_user(db, user_id)])
    return labeled[0]


@router.get("/users/{user_id}/access-summary", response_model=UserAccessSummary)
async def user_access_summary(user_id: UUID, _: AuthenticatedUser = Depends(user_read), db: AsyncSession = Depends(get_db)):
    return await directory_read.get_user_access_summary(db, user_id)


@router.get("/users/{user_id}/linked-accounts", response_model=list[LinkedAccountResponse])
async def user_linked_accounts(user_id: UUID, _: AuthenticatedUser = Depends(user_read), db: AsyncSession = Depends(get_db)):
    """PU/TU shadow accounts (User.linked_user_id) belonging to this real user — see app.services.privileged_accounts."""
    accounts = await privileged_accounts_service.list_linked_accounts(db, user_id)
    return [LinkedAccountResponse(id=a.id, display_name=a.display_name, email=a.email, account_type=a.account_type, status=a.status) for a in accounts]


@router.post("/users/{user_id}/enabled", response_model=UserResponse)
async def set_user_account_enabled(user_id: UUID, data: SetEnabledRequest, request: Request, actor: AuthenticatedUser = Depends(group_manage), db: AsyncSession = Depends(get_db)):
    """Enable/disable a PU/TU account's real Entra/Okta accountEnabled flag — used from the linked real user's detail page."""
    return await privileged_accounts_service.set_account_enabled(db, user_id, data.enabled, actor.directory_object_id, request.state.request_id)


@router.patch("/users/{user_id}/attributes", response_model=UserResponse)
async def update_user_attributes(user_id: UUID, data: UserAttributeUpdate, request: Request, actor: AuthenticatedUser = Depends(group_manage), db: AsyncSession = Depends(get_db)):
    """The AccessPilot -> Entra/Okta direction of attribute editing: pushes a real write to the identity's own
    provider first (so Entra/Okta stays the source of truth, never just a local-only edit), then updates the
    local row to match, then runs the same birthright mover reconciliation a directory sync would trigger for
    the same change arriving from the other direction — see app.services.identity_attributes. If
    workflow_definition_id is set, none of that happens yet: a UserAttributeChangeRequest is created and a
    workflow instance started instead, and the real write/reconcile only happen once it's approved — see
    app.services.workflows's USER_ATTRIBUTES completion branch."""
    from app.services.assignments import _resolve_internal_user_id

    user = await directory_read.get_user(db, user_id)

    workflow_definition = None
    if data.workflow_definition_id is not None:
        from app.models import WorkflowDefinition
        workflow_definition = await db.get(WorkflowDefinition, data.workflow_definition_id)
        if workflow_definition is None:
            raise AccessPilotError("WORKFLOW_DEFINITION_NOT_FOUND", "The selected workflow was not found.", 404)
        if workflow_definition.status != "ACTIVE":
            raise AccessPilotError("WORKFLOW_DEFINITION_NOT_ACTIVE", "This workflow is not currently accepting requests.", 409)

    if workflow_definition is not None:
        from app.models import UserAttributeChangeRequest
        from app.services.workflows import build_user_condition_payload, start_workflow_instance

        requester_id = await _resolve_internal_user_id(db, actor.directory_object_id)
        change_request = UserAttributeChangeRequest(
            user_id=user.id, previous_department=user.department, previous_job_title=user.job_title,
            requested_department=data.department, requested_job_title=data.job_title, status="PENDING",
            created_by=requester_id,
        )
        db.add(change_request)
        await db.flush()
        instance = await start_workflow_instance(db, workflow_definition, requester_id, build_user_condition_payload(user), "USER_ATTRIBUTES", f"Change department/job title for {user.display_name}", subject_id=change_request.id)
        change_request.workflow_instance_id = instance.id
        await record_audit(db, action="USER_ATTRIBUTE_CHANGE_REQUESTED", target_type="USER", target_id=user.id, provider_id=user.provider_id, actor_user_id=requester_id, request_id=request.state.request_id, metadata={"requested_department": data.department, "requested_job_title": data.job_title, "workflow_definition_id": str(workflow_definition.id)})
        await db.commit()
        await db.refresh(user)
        response = UserResponse.model_validate(user)
        response.pending_attribute_change = True
        return response

    from app.services.identity_attributes import apply_user_attribute_change
    return await apply_user_attribute_change(db, user.id, data.department, data.job_title, actor.directory_object_id, request.state.request_id)


@router.patch("/users/{user_id}/hierarchy", response_model=UserResponse)
async def update_user_hierarchy(user_id: UUID, data: UserHierarchyUpdate, request: Request, actor: AuthenticatedUser = Depends(group_manage), db: AsyncSession = Depends(get_db)):
    """AccessPilot-internal org tagging (Employee/Manager + who they report to) — purely a local write, never
    pushed to Entra/Okta (see app.services.user_hierarchy)."""
    from app.services.assignments import _resolve_internal_user_id
    actor_id = await _resolve_internal_user_id(db, actor.directory_object_id)
    return await user_hierarchy_service.update_user_hierarchy(db, user_id, data, actor_id, request.state.request_id)


@router.get("/groups", response_model=list[GroupResponse])
async def groups(q: str | None = Query(default=None), _: AuthenticatedUser = Depends(group_read), db: AsyncSession = Depends(get_db)):
    return await directory_read.label_groups_with_provider(db, await directory_read.list_groups(db, q))


@router.get("/groups/owned", response_model=list[GroupResponse])
async def list_owned_groups(actor: AuthenticatedUser = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    """Self-authorizing, like GET /packages/owned and GET /business-roles/owned — any authenticated user sees
    the groups they're an AccessPilot-side owner of, Admin or not."""
    return await group_owner_service.list_owned_groups(db, actor.directory_object_id)


@router.patch("/groups/{group_id}/owner-update", response_model=GroupResponse)
async def owner_update_group(group_id: UUID, data: GroupOwnerSelfServiceUpdate, request: Request, actor: AuthenticatedUser = Depends(require_authenticated_user), db: AsyncSession = Depends(get_db)):
    """The group owner portal's only edit power — description and Group Label. Mirrors owner_rename_package /
    owner_rename_business_role: self-authorizing (no GROUP_MANAGE permission needed), only a GroupOwner row for
    this specific group, enforced inside the service."""
    return await group_owner_service.owner_update_group(db, group_id, data, actor.directory_object_id, request.state.request_id)


@router.get("/groups/{group_id}", response_model=GroupResponse)
async def group_detail(group_id: UUID, _: AuthenticatedUser = Depends(group_read), db: AsyncSession = Depends(get_db)):
    labeled = await directory_read.label_groups_with_provider(db, [await directory_read.get_group(db, group_id)])
    return labeled[0]


@router.get("/groups/{group_id}/owners", response_model=list[GroupOwnerInfo])
async def group_owners(group_id: UUID, _: AuthenticatedUser = Depends(group_read), db: AsyncSession = Depends(get_db)):
    return await group_owner_service.list_group_owners(db, group_id)


@router.put("/groups/{group_id}/owners", response_model=list[GroupOwnerInfo])
async def set_group_owners(group_id: UUID, data: GroupOwnersUpdate, request: Request, actor: AuthenticatedUser = Depends(group_manage), db: AsyncSession = Depends(get_db)):
    """AccessPilot-side owners only — nothing is written to Entra."""
    return await group_owner_service.set_group_owners(db, group_id, data.user_ids, actor.directory_object_id, request.state.request_id)


@router.get("/groups/{group_id}/members", response_model=list[UserResponse])
async def group_members(group_id: UUID, _: AuthenticatedUser = Depends(group_read), db: AsyncSession = Depends(get_db)):
    return await directory_read.label_users_with_provider(db, await directory_read.list_group_members(db, group_id))


@router.get("/groups/{group_id}/access-summary", response_model=GroupAccessSummary)
async def group_access_summary(group_id: UUID, _: AuthenticatedUser = Depends(group_read), db: AsyncSession = Depends(get_db)):
    return await directory_read.get_group_access_summary(db, group_id)


@router.post("/groups", response_model=GroupResponse, status_code=201)
async def create_group(data: GroupCreate, request: Request, _: AuthenticatedUser = Depends(group_manage), db: AsyncSession = Depends(get_db)):
    provider = await _primary_provider(db)
    if not provider:
        raise AccessPilotError("PROVIDER_NOT_FOUND", "No identity provider is configured.", 404)
    connector = _connector(provider)
    try:
        created = await connector.create_group(NewGroupRequest(display_name=data.display_name, description=data.description, mail_nickname=data.mail_nickname))
    except ProviderConflictError as exc:
        raise AccessPilotError("GROUP_ALREADY_EXISTS", str(exc), 409) from exc
    except GraphError as exc:
        raise AccessPilotError(exc.code, exc.message, exc.status_code) from exc
    row = await upsert_group(db, provider.id, created)
    row.group_label = data.group_label
    await record_audit(db, action="GROUP_CREATED", target_type="GROUP", target_id=row.id, provider_id=provider.id, request_id=request.state.request_id, metadata={"group_label": data.group_label})
    await db.commit()
    await db.refresh(row)
    return GroupResponse.model_validate(row)


@router.get("/roles", response_model=list[RoleResponse])
async def roles(q: str | None = Query(default=None), _: AuthenticatedUser = Depends(role_read), db: AsyncSession = Depends(get_db)):
    return await directory_read.label_roles_with_provider(db, await directory_read.list_roles(db, q))


@router.get("/applications", response_model=list[ApplicationResponse])
async def applications(q: str | None = Query(default=None), _: AuthenticatedUser = Depends(role_read), db: AsyncSession = Depends(get_db)):
    return await directory_read.label_applications_with_provider(db, await directory_read.list_applications(db, q))


@router.get("/dashboard/admin")
async def dashboard_admin(_: AuthenticatedUser = Depends(dashboard_admin_read), db: AsyncSession = Depends(get_db)):
    return await admin_dashboard(db)


@router.get("/dashboard/privileged-role-activations")
async def dashboard_privileged_role_activations(days: int = Query(default=30), _: AuthenticatedUser = Depends(dashboard_admin_read), db: AsyncSession = Depends(get_db)):
    return await get_privileged_role_activation_timeline(db, days)


@router.get("/dashboard/user-access-segments")
async def dashboard_user_access_segments(_: AuthenticatedUser = Depends(dashboard_admin_read), db: AsyncSession = Depends(get_db)):
    return await get_user_access_segments(db)


@router.get("/dashboard/user-access-segments/{segment}")
async def dashboard_user_access_segment_members(segment: str, _: AuthenticatedUser = Depends(dashboard_admin_read), db: AsyncSession = Depends(get_db)):
    return await get_user_access_segment_members(db, segment)


@router.get("/dashboard/user")
async def dashboard_user(_: AuthenticatedUser = Depends(dashboard_user_read)):
    return {"data": {}, "meta": {"scope": "user", "status": "foundation_only"}}
