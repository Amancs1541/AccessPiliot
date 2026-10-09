from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AccessPilotError
from app.models import AccessAssignment, User, WorkflowDefinition, WorkflowInstance, WorkflowRequest, WorkflowStageDecision, WorkflowStageDefinition, WorkflowStageInstance
from app.schemas.workflows import (
    WorkflowApproverInfo, WorkflowDecideRequest, WorkflowDefinitionCreate, WorkflowDefinitionResponse,
    WorkflowDefinitionUpdate, WorkflowRequestCreate, WorkflowRequestResponse, WorkflowStageCreate,
    WorkflowStageDecisionInfo, WorkflowStageDefinitionResponse, WorkflowStageInstanceResponse,
)
from app.services.assignments import _resolve_internal_user_id, _resolve_target
from app.services.audit import record_audit
from app.services.notifications import create_notification


def build_user_condition_payload(user: User) -> dict:
    """The real, trusted source of condition data for a workflow instance routed from create_assignment() — read
    straight off the TARGET user's own directory record, never anything a requester could type into a form.
    Mirrors app.services.birthright's own CONDITION_FIELD_MAP trust model: whatever is in these columns is taken
    as-is, no separate validation layer, but critically it is read from the User row, not client input."""
    return {
        "department": user.department or "",
        "job_title": user.job_title or "",
        "employment_type": user.employment_type or "",
        "account_type": user.account_type or "",
    }


# ---------------------------------------------------------------- helpers shared by definitions and instances

async def _validate_users_exist(session: AsyncSession, user_ids: list[UUID], label: str) -> None:
    for user_id in user_ids:
        if await session.get(User, user_id) is None:
            raise AccessPilotError("USER_NOT_FOUND", f"One of the selected {label}s was not found.", 404)


async def _hydrate_approvers(session: AsyncSession, user_ids: Optional[list]) -> list[WorkflowApproverInfo]:
    result = []
    for raw_id in user_ids or []:
        user_id = raw_id if isinstance(raw_id, UUID) else UUID(str(raw_id))
        user = await session.get(User, user_id)
        result.append(WorkflowApproverInfo(user_id=user_id, display_name=user.display_name if user else None))
    return result


def _add_stage_rows(session: AsyncSession, definition_id: UUID, stages: list[WorkflowStageCreate]) -> None:
    for index, stage in enumerate(stages, start=1):
        session.add(WorkflowStageDefinition(
            workflow_definition_id=definition_id, stage_number=index, name=stage.name, approval_mode=stage.approval_mode,
            approver_user_ids=[str(u) for u in stage.approver_user_ids],
            fallback_approver_ids=[str(u) for u in stage.fallback_approver_ids] if stage.fallback_approver_ids else None,
            escalate_after_hours=stage.escalate_after_hours,
            condition_field=stage.condition_field, condition_operator=stage.condition_operator, condition_value=stage.condition_value,
        ))


# ---------------------------------------------------------------- workflow definitions (admin CRUD)

async def _get_definition(session: AsyncSession, definition_id: UUID) -> WorkflowDefinition:
    definition = await session.get(WorkflowDefinition, definition_id)
    if definition is None:
        raise AccessPilotError("WORKFLOW_DEFINITION_NOT_FOUND", "The workflow definition was not found.", 404)
    return definition


async def _to_definition_response(session: AsyncSession, definition: WorkflowDefinition) -> WorkflowDefinitionResponse:
    stages = list((await session.scalars(select(WorkflowStageDefinition).where(WorkflowStageDefinition.workflow_definition_id == definition.id).order_by(WorkflowStageDefinition.stage_number))).all())
    stage_responses = [
        WorkflowStageDefinitionResponse(
            id=stage.id, stage_number=stage.stage_number, name=stage.name, approval_mode=stage.approval_mode,
            approvers=await _hydrate_approvers(session, stage.approver_user_ids),
            fallback_approvers=await _hydrate_approvers(session, stage.fallback_approver_ids),
            escalate_after_hours=stage.escalate_after_hours,
            condition_field=stage.condition_field, condition_operator=stage.condition_operator, condition_value=stage.condition_value,
        )
        for stage in stages
    ]
    in_flight_count = len((await session.scalars(select(WorkflowInstance.id).where(WorkflowInstance.workflow_definition_id == definition.id, WorkflowInstance.status == "PENDING"))).all())
    return WorkflowDefinitionResponse(
        id=definition.id, name=definition.name, description=definition.description, status=definition.status,
        stages=stage_responses, in_flight_count=in_flight_count, created_at=definition.created_at, updated_at=definition.updated_at,
    )


async def create_workflow_definition(session: AsyncSession, data: WorkflowDefinitionCreate, actor_subject: str, request_id: str) -> WorkflowDefinitionResponse:
    existing = (await session.execute(select(WorkflowDefinition).where(WorkflowDefinition.name == data.name))).scalars().first()
    if existing is not None:
        raise AccessPilotError("WORKFLOW_DEFINITION_ALREADY_EXISTS", "A workflow definition with this name already exists.", 409)
    for stage in data.stages:
        await _validate_users_exist(session, stage.approver_user_ids, "approver")
        if stage.fallback_approver_ids:
            await _validate_users_exist(session, stage.fallback_approver_ids, "fallback approver")

    actor_id = await _resolve_internal_user_id(session, actor_subject)
    definition = WorkflowDefinition(name=data.name, description=data.description, status="DRAFT", created_by=actor_id)
    session.add(definition)
    await session.flush()
    _add_stage_rows(session, definition.id, data.stages)
    await record_audit(session, action="WORKFLOW_DEFINITION_CREATED", target_type="WORKFLOW_DEFINITION", target_id=definition.id, actor_user_id=actor_id, request_id=request_id, metadata={"name": data.name, "stage_count": len(data.stages)})
    await session.commit()
    await session.refresh(definition)
    return await _to_definition_response(session, definition)


async def update_workflow_definition(session: AsyncSession, definition_id: UUID, data: WorkflowDefinitionUpdate, actor_subject: str, request_id: str) -> WorkflowDefinitionResponse:
    definition = await _get_definition(session, definition_id)
    if data.name is not None and data.name != definition.name:
        clash = (await session.execute(select(WorkflowDefinition).where(WorkflowDefinition.name == data.name, WorkflowDefinition.id != definition_id))).scalars().first()
        if clash is not None:
            raise AccessPilotError("WORKFLOW_DEFINITION_ALREADY_EXISTS", "A workflow definition with this name already exists.", 409)
        definition.name = data.name
    if data.description is not None:
        definition.description = data.description
    if data.status is not None:
        definition.status = data.status
    if data.stages is not None:
        for stage in data.stages:
            await _validate_users_exist(session, stage.approver_user_ids, "approver")
            if stage.fallback_approver_ids:
                await _validate_users_exist(session, stage.fallback_approver_ids, "fallback approver")
        # Editing stages never rewrites in-flight/historical WorkflowStageInstance rows (those are snapshots) —
        # an instance already past a stage keeps what it saw; an instance that hasn't reached a later stage yet
        # will pick up whatever this edit left behind once it actually gets there.
        for existing_stage in list((await session.scalars(select(WorkflowStageDefinition).where(WorkflowStageDefinition.workflow_definition_id == definition_id))).all()):
            await session.delete(existing_stage)
        await session.flush()
        _add_stage_rows(session, definition_id, data.stages)

    actor_id = await _resolve_internal_user_id(session, actor_subject)
    await record_audit(session, action="WORKFLOW_DEFINITION_UPDATED", target_type="WORKFLOW_DEFINITION", target_id=definition_id, actor_user_id=actor_id, request_id=request_id, metadata={k: v for k, v in data.model_dump(exclude_none=True).items() if k != "stages"})
    await session.commit()
    await session.refresh(definition)
    return await _to_definition_response(session, definition)


async def list_workflow_definitions(session: AsyncSession) -> list[WorkflowDefinitionResponse]:
    definitions = list((await session.scalars(select(WorkflowDefinition).order_by(WorkflowDefinition.name))).all())
    return [await _to_definition_response(session, definition) for definition in definitions]


async def get_workflow_definition_response(session: AsyncSession, definition_id: UUID) -> WorkflowDefinitionResponse:
    return await _to_definition_response(session, await _get_definition(session, definition_id))


async def list_active_workflow_definitions(session: AsyncSession) -> list[WorkflowDefinitionResponse]:
    """Open to any authenticated user (unlike list_workflow_definitions, which needs WORKFLOW_READ) — this is how
    a requester picks which workflow to submit a request against, so it only ever returns ACTIVE ones."""
    definitions = list((await session.scalars(select(WorkflowDefinition).where(WorkflowDefinition.status == "ACTIVE").order_by(WorkflowDefinition.name))).all())
    return [await _to_definition_response(session, definition) for definition in definitions]


async def delete_workflow_definition(session: AsyncSession, definition_id: UUID, actor_subject: str, request_id: str) -> None:
    definition = await _get_definition(session, definition_id)
    has_history = (await session.execute(select(WorkflowInstance.id).where(WorkflowInstance.workflow_definition_id == definition_id).limit(1))).first() is not None
    if has_history:
        raise AccessPilotError("WORKFLOW_DEFINITION_HAS_HISTORY", "This workflow has requests against it — set its status to DISABLED instead of deleting it, so that history stays intact.", 409)
    for stage in list((await session.scalars(select(WorkflowStageDefinition).where(WorkflowStageDefinition.workflow_definition_id == definition_id))).all()):
        await session.delete(stage)
    actor_id = await _resolve_internal_user_id(session, actor_subject)
    await record_audit(session, action="WORKFLOW_DEFINITION_DELETED", target_type="WORKFLOW_DEFINITION", target_id=definition_id, actor_user_id=actor_id, request_id=request_id, metadata={"name": definition.name})
    await session.delete(definition)
    await session.commit()


# ---------------------------------------------------------------- the engine itself: instances and stages

def _condition_matches(payload: dict, field: str, operator: str, value: str) -> bool:
    """Mirrors app.services.birthright._condition_matches exactly: a missing/falsy payload value can never match,
    for EITHER operator — the safe default that stops a NOT_EQUALS condition from silently matching data that was
    never actually provided."""
    actual = (payload or {}).get(field)
    if not actual:
        return False
    equal = str(actual).strip().lower() == str(value).strip().lower()
    return equal if operator == "EQUALS" else not equal


async def _advance(session: AsyncSession, instance: WorkflowInstance, stage_defs: list[WorkflowStageDefinition], from_stage_number: int, subject_label: str) -> None:
    """Walks forward from from_stage_number, skipping (and recording as SKIPPED, for an honest history) any stage
    whose condition doesn't match instance.payload, and stops at the first stage that actually needs a human
    decision. If every remaining stage is skipped, the instance completes APPROVED with zero decisions ever
    required — the same safe default Birthright's own condition engine uses for a policy that matches nobody."""
    for stage_def in stage_defs:
        if stage_def.stage_number < from_stage_number:
            continue
        if stage_def.condition_field and not _condition_matches(instance.payload or {}, stage_def.condition_field, stage_def.condition_operator, stage_def.condition_value):
            session.add(WorkflowStageInstance(
                workflow_instance_id=instance.id, stage_number=stage_def.stage_number, name=stage_def.name,
                approval_mode=stage_def.approval_mode, required_approver_ids=stage_def.approver_user_ids,
                fallback_approver_ids=stage_def.fallback_approver_ids, status="SKIPPED", completed_at=datetime.now(timezone.utc),
            ))
            continue
        escalates_at = datetime.now(timezone.utc) + timedelta(hours=stage_def.escalate_after_hours) if stage_def.escalate_after_hours else None
        session.add(WorkflowStageInstance(
            workflow_instance_id=instance.id, stage_number=stage_def.stage_number, name=stage_def.name,
            approval_mode=stage_def.approval_mode, required_approver_ids=stage_def.approver_user_ids,
            fallback_approver_ids=stage_def.fallback_approver_ids, status="PENDING", escalates_at=escalates_at,
        ))
        instance.current_stage_number = stage_def.stage_number
        for raw_id in stage_def.approver_user_ids:
            await create_notification(session, UUID(str(raw_id)), "WORKFLOW_STAGE_PENDING", f'"{subject_label}" needs your decision.', link="/workflow-requests")
        return
    instance.status = "APPROVED"
    instance.completed_at = datetime.now(timezone.utc)
    instance.current_stage_number = (stage_defs[-1].stage_number + 1) if stage_defs else 1


async def _apply_assignment_outcome(session: AsyncSession, instance: WorkflowInstance, actor_id: Optional[UUID], request_id: str) -> None:
    """The ASSIGNMENT branch of the completion dispatcher: once the instance finishes (APPROVED, REJECTED, or
    CANCELLED — all three mean the grant either happens or it doesn't), apply the exact same two possible outcomes
    approve_assignment()/reject_assignment() already produce for the human-decided path — ELIGIBLE or REJECTED,
    never straight to ACTIVE, the same custom-PIM rule every other grant in this app follows. Deliberately does
    NOT call into those two functions (their own _authorize_decision already ran a human past; here the workflow's
    own stages already provided that authorization) — this reads/writes the AccessAssignment row directly via the
    ORM, the same way app.services.business_roles/birthright already do elsewhere in this codebase. A no-op if the
    assignment was already decided some other way in the meantime."""
    assignment = await session.get(AccessAssignment, instance.subject_id)
    if assignment is None or assignment.status != "PENDING_APPROVAL":
        return
    _, resource_name, _ = await _resolve_target(session, assignment.resource_type, assignment.resource_id)
    if instance.status == "APPROVED":
        assignment.status = "ELIGIBLE"
        await record_audit(session, action="ASSIGNMENT_APPROVED", target_type="ASSIGNMENT", target_id=assignment.id, provider_id=assignment.provider_id, actor_user_id=actor_id, request_id=request_id, metadata={"decision": "APPROVED", "via_workflow": True})
        await create_notification(session, assignment.user_id, "ASSIGNMENT_APPROVED", f"Your request for {resource_name} was approved — you can now activate it from My Access.", link="/my-access")
    else:  # REJECTED or CANCELLED — either way, the grant never happens
        assignment.status = "REJECTED"
        await record_audit(session, action="ASSIGNMENT_REJECTED", target_type="ASSIGNMENT", target_id=assignment.id, provider_id=assignment.provider_id, actor_user_id=actor_id, request_id=request_id, metadata={"decision": "REJECTED", "via_workflow": True, "workflow_status": instance.status})
        await create_notification(session, assignment.user_id, "ASSIGNMENT_REJECTED", f"Your request for {resource_name} was rejected.", link="/my-requests")


async def _apply_user_attribute_outcome(session: AsyncSession, instance: WorkflowInstance, actor_id: Optional[UUID], request_id: str) -> None:
    """The USER_ATTRIBUTES branch of the completion dispatcher: once the instance finishes, either apply the
    deferred department/job_title edit for real (the same connector write + reconcile + mover sequence the instant
    edit path already runs — see app.services.identity_attributes.apply_user_attribute_change) or leave the user
    untouched. A no-op if the change request was already decided some other way in the meantime."""
    from app.models import UserAttributeChangeRequest
    change_request = await session.get(UserAttributeChangeRequest, instance.subject_id)
    if change_request is None or change_request.status != "PENDING":
        return
    actor_user = await session.get(User, actor_id) if actor_id is not None else None
    actor_subject = actor_user.external_id if actor_user is not None else "system:workflow"
    if instance.status == "APPROVED":
        from app.services.identity_attributes import apply_user_attribute_change
        await apply_user_attribute_change(session, change_request.user_id, change_request.requested_department, change_request.requested_job_title, actor_subject, request_id, via_workflow=True)
        change_request.status = "APPLIED"
        if change_request.created_by is not None:
            await create_notification(session, change_request.created_by, "USER_ATTRIBUTES_APPROVED", "Your requested attribute change was approved and applied.", link=f"/users/{change_request.user_id}")
    else:  # REJECTED or CANCELLED — the edit never happens
        change_request.status = "REJECTED"
        if change_request.created_by is not None:
            await create_notification(session, change_request.created_by, "USER_ATTRIBUTES_REJECTED", "Your requested attribute change was rejected.", link=f"/users/{change_request.user_id}")
    change_request.decided_at = datetime.now(timezone.utc)


async def _apply_access_review_item_outcome(session: AsyncSession, instance: WorkflowInstance, actor_id: Optional[UUID], request_id: str) -> None:
    """The ACCESS_REVIEW_ITEM branch: mirrors app.services.access_reviews.decide_item's own two outcomes exactly —
    APPROVED certifies the item (no-op on the real grant); REJECTED calls the same, unmodified revoke_assignment()
    every reviewer-decided revoke already uses. A third case decide_item never has to handle: CANCELLED, meaning
    the owning campaign closed (due date passed, or an admin force-completed it) before this item's own workflow
    finished — resolved exactly the way complete_campaign() already resolves every other still-pending item at
    that point, via the campaign's own on_no_response (KEEP -> APPROVED, REVOKE -> AUTO_REVOKED + a real revoke).
    A no-op if the item was already decided some other way in the meantime. Local imports throughout: access_reviews
    imports this module for the reverse direction (starting an instance per item), so this import has to stay
    function-scoped to avoid a circular import, the same convention used everywhere else this session."""
    from app.models import AccessReviewCampaign, AccessReviewItem
    item = await session.get(AccessReviewItem, instance.subject_id)
    if item is None or item.decision != "PENDING":
        return
    campaign = await session.get(AccessReviewCampaign, item.campaign_id)
    actor_user = await session.get(User, actor_id) if actor_id is not None else None
    actor_subject = actor_user.external_id if actor_user is not None else "system:workflow"

    if instance.status == "APPROVED":
        item.decision = "APPROVED"
    elif instance.status == "REJECTED":
        item.decision = "REVOKED"
    else:  # CANCELLED — the campaign closed this out before the item's own workflow finished
        item.decision = "APPROVED" if (campaign is not None and campaign.on_no_response == "KEEP") else "AUTO_REVOKED"

    if item.decision in ("REVOKED", "AUTO_REVOKED"):
        from app.services.assignments import revoke_assignment
        reason = "ACCESS_REVIEW_REVOKED" if item.decision == "REVOKED" else "ACCESS_REVIEW_AUTO_REVOKED"
        justification = "Revoked via workflow decision." if item.decision == "REVOKED" else "Access review campaign closed with no decision — auto-revoked."
        try:
            await revoke_assignment(session, item.assignment_id, actor_subject, justification, request_id, reason=reason)
        except AccessPilotError as exc:
            if exc.code != "REQUEST_ALREADY_PROCESSED":  # already revoked/expired independently — still record the decision
                raise

    item.decided_by = actor_id
    item.decided_at = datetime.now(timezone.utc)
    await record_audit(session, action="ACCESS_REVIEW_ITEM_DECIDED", target_type="ACCESS_REVIEW_ITEM", target_id=item.id, actor_user_id=actor_id, request_id=request_id, metadata={"decision": item.decision, "campaign_id": str(item.campaign_id), "via_workflow": True})
    if campaign is not None:
        if campaign.created_by is not None and campaign.created_by != actor_id:
            from app.services.access_reviews import _resolve_display_name
            resource_name = await _resolve_display_name(session, item.resource_type, item.resource_id, item.app_role_external_id)
            verb = "approved" if item.decision == "APPROVED" else "revoked"
            await create_notification(session, campaign.created_by, "ACCESS_REVIEW_ITEM_DECIDED", f"An item in \"{campaign.name}\" was {verb}: {resource_name or item.resource_type} for the reviewed user.", link=f"/admin/access-reviews/{campaign.id}")
        from app.services.access_reviews import _maybe_spawn_recurrence, campaign_progress
        total, decided = await campaign_progress(session, campaign.id)
        if decided >= total and campaign.status == "ACTIVE":
            campaign.status = "COMPLETED"
            campaign.completed_at = datetime.now(timezone.utc)
            await session.commit()
            await _maybe_spawn_recurrence(session, campaign, request_id)


async def _apply_subject_outcome(session: AsyncSession, instance: WorkflowInstance, actor_id: Optional[UUID], request_id: str) -> None:
    """Dispatches a finished instance's outcome (APPROVED/REJECTED/CANCELLED) to whatever it's actually governing.
    A no-op for any subject_type with no branch below (e.g. the standalone WORKFLOW_REQUEST self-service pilot,
    which has nothing further to apply)."""
    if instance.subject_id is None:
        return
    if instance.subject_type == "ASSIGNMENT":
        await _apply_assignment_outcome(session, instance, actor_id, request_id)
    elif instance.subject_type == "USER_ATTRIBUTES":
        await _apply_user_attribute_outcome(session, instance, actor_id, request_id)
    elif instance.subject_type == "ACCESS_REVIEW_ITEM":
        await _apply_access_review_item_outcome(session, instance, actor_id, request_id)


async def start_workflow_instance(session: AsyncSession, definition: WorkflowDefinition, requester_id: UUID, payload: dict, subject_type: str, subject_label: str, subject_id: Optional[UUID] = None) -> WorkflowInstance:
    """subject_id is set here directly when the caller already has it (e.g. create_assignment, which flushes its
    new AccessAssignment row before calling this) — WorkflowRequest is the one exception, set afterward by
    submit_workflow_request, since that row doesn't exist yet until the instance it wraps does."""
    instance = WorkflowInstance(workflow_definition_id=definition.id, subject_type=subject_type, subject_id=subject_id, requested_by=requester_id, payload=payload or {}, status="PENDING", current_stage_number=0)
    session.add(instance)
    await session.flush()
    stage_defs = list((await session.scalars(select(WorkflowStageDefinition).where(WorkflowStageDefinition.workflow_definition_id == definition.id).order_by(WorkflowStageDefinition.stage_number))).all())
    await _advance(session, instance, stage_defs, from_stage_number=1, subject_label=subject_label)
    if instance.status == "APPROVED":
        # Every stage was skipped (or there were none) — the instance never reaches decide_stage at all, so the
        # completion hook that would normally apply there has to run here too.
        await _apply_subject_outcome(session, instance, requester_id, "workflow-auto-approve")
    return instance


# ---------------------------------------------------------------- generic instance read/display (any subject_type)

async def _get_instance(session: AsyncSession, instance_id: UUID) -> WorkflowInstance:
    instance = await session.get(WorkflowInstance, instance_id)
    if instance is None:
        raise AccessPilotError("WORKFLOW_REQUEST_NOT_FOUND", "The workflow request was not found.", 404)
    return instance


async def _resolve_batch_info(session: AsyncSession, assignment: AccessAssignment) -> tuple[Optional[UUID], Optional[str]]:
    """An ASSIGNMENT-subject instance's (batch_id, batch_label) if it's one item of a multi-item Package or
    Business-Role assignment — each item gets its own independent WorkflowInstance (see create_assignment), but
    they share a package_assignment_id/role_assignment_id, so the frontend can bundle them. (None, None) for a
    plain, standalone assignment."""
    from app.models import AccessPackage, AccessPackageAssignment, BusinessRole
    pkg_link = (await session.scalars(select(AccessPackageAssignment).where(AccessPackageAssignment.assignment_id == assignment.id))).first()
    if pkg_link is not None:
        package = await session.get(AccessPackage, pkg_link.package_id)
        return pkg_link.package_assignment_id, f"📦 {package.name}" if package else "📦 Package"
    if assignment.business_role_id is not None and assignment.role_assignment_id is not None:
        role = await session.get(BusinessRole, assignment.business_role_id)
        return assignment.role_assignment_id, f"🏷 {role.name}" if role else "🏷 Business Role"
    return None, None


async def _resolve_subject_details(session: AsyncSession, instance: WorkflowInstance) -> tuple[str, Optional[str], Optional[str], Optional[UUID], Optional[str]]:
    """(title, description, justification, batch_id, batch_label) for display — resolved differently depending on
    what this instance is actually governing, so the same generic engine can show something sensible for both the
    standalone self-service WorkflowRequest pilot and an ASSIGNMENT routed through create_assignment()."""
    if instance.subject_type == "ASSIGNMENT" and instance.subject_id is not None:
        assignment = await session.get(AccessAssignment, instance.subject_id)
        if assignment is not None:
            _, resource_name, _ = await _resolve_target(session, assignment.resource_type, assignment.resource_id)
            batch_id, batch_label = await _resolve_batch_info(session, assignment)
            return f"Access to {resource_name}", None, assignment.justification, batch_id, batch_label
        return "Access request", None, None, None, None
    if instance.subject_type == "USER_ATTRIBUTES" and instance.subject_id is not None:
        from app.models import UserAttributeChangeRequest
        change_request = await session.get(UserAttributeChangeRequest, instance.subject_id)
        if change_request is not None:
            target = await session.get(User, change_request.user_id)
            target_name = target.display_name if target else "Unknown user"
            changes = []
            if change_request.requested_department != change_request.previous_department:
                changes.append(f"department: {change_request.previous_department or '—'} → {change_request.requested_department or '—'}")
            if change_request.requested_job_title != change_request.previous_job_title:
                changes.append(f"job title: {change_request.previous_job_title or '—'} → {change_request.requested_job_title or '—'}")
            return f"Change {', '.join(changes) or 'attributes'} for {target_name}", None, None, None, None
        return "Identity attribute change", None, None, None, None
    if instance.subject_type == "ACCESS_REVIEW_ITEM" and instance.subject_id is not None:
        from app.models import AccessReviewItem
        from app.services.access_reviews import _resolve_display_name
        item = await session.get(AccessReviewItem, instance.subject_id)
        if item is not None:
            target = await session.get(User, item.user_id)
            resource_name = await _resolve_display_name(session, item.resource_type, item.resource_id, item.app_role_external_id)
            return f"Review: {resource_name or item.resource_type} for {target.display_name if target else 'Unknown user'}", None, None, item.campaign_id, "📋 Access Review"
        return "Access review item", None, None, None, None
    workflow_request = (await session.scalars(select(WorkflowRequest).where(WorkflowRequest.workflow_instance_id == instance.id))).first()
    if workflow_request is not None:
        return workflow_request.title, workflow_request.description, workflow_request.justification, None, None
    return "Workflow request", None, None, None, None


async def _resolve_subject_label(session: AsyncSession, instance: WorkflowInstance) -> str:
    title, _, _, _, _ = await _resolve_subject_details(session, instance)
    return title


async def _authorize_stage_decision(session: AsyncSession, stage_instance: WorkflowStageInstance, actor_subject: str, actor_roles: tuple[str, ...]) -> tuple[UUID, bool]:
    """Returns (actor_id, overrides). Admin always overrides and finalizes the stage outright — the same universal
    power Admin already has over every other approval entity in this app. A named primary approver decides
    normally (overrides=False, so ANY_OF/ALL_OF rules apply). A named fallback approver may only act once
    escalated_at has been set by the sweep (never before — a fallback can't jump ahead of the notification that's
    supposed to tell them they're needed), and when they do act, their decision finalizes the stage outright too."""
    actor_id = await _resolve_internal_user_id(session, actor_subject)
    if actor_id is None:
        raise AccessPilotError("ACCESS_DENIED", "Your account could not be resolved.", 403)
    if "AccessPilot.Admin" in actor_roles:
        return actor_id, True
    required_ids = {str(u) for u in stage_instance.required_approver_ids}
    if str(actor_id) in required_ids:
        return actor_id, False
    fallback_ids = {str(u) for u in (stage_instance.fallback_approver_ids or [])}
    if str(actor_id) in fallback_ids:
        if stage_instance.escalated_at is None:
            raise AccessPilotError("FALLBACK_NOT_YET_AVAILABLE", "The fallback approver may only act once this stage has escalated.", 403)
        return actor_id, True
    raise AccessPilotError("ACCESS_DENIED", "Only this stage's named approvers, its fallback approvers (once escalated), or an administrator can decide it.", 403)


async def _to_instance_response(session: AsyncSession, instance: WorkflowInstance, actor_subject: Optional[str], actor_roles: tuple[str, ...] = ()) -> WorkflowRequestResponse:
    definition = await session.get(WorkflowDefinition, instance.workflow_definition_id)
    requester = await session.get(User, instance.requested_by)
    title, description, justification, batch_id, batch_label = await _resolve_subject_details(session, instance)
    stage_instances = list((await session.scalars(select(WorkflowStageInstance).where(WorkflowStageInstance.workflow_instance_id == instance.id).order_by(WorkflowStageInstance.stage_number))).all())

    stage_responses = []
    can_decide = False
    for stage in stage_instances:
        decisions = list((await session.scalars(select(WorkflowStageDecision).where(WorkflowStageDecision.workflow_stage_instance_id == stage.id).order_by(WorkflowStageDecision.decided_at))).all())
        decision_infos = []
        for decision in decisions:
            decider = await session.get(User, decision.decided_by)
            decision_infos.append(WorkflowStageDecisionInfo(decided_by=decision.decided_by, decided_by_display_name=decider.display_name if decider else None, decision=decision.decision, justification=decision.justification, decided_at=decision.decided_at))
        if stage.status == "PENDING" and actor_subject is not None and not can_decide:
            try:
                await _authorize_stage_decision(session, stage, actor_subject, actor_roles)
                can_decide = True
            except AccessPilotError:
                pass
        stage_responses.append(WorkflowStageInstanceResponse(
            id=stage.id, stage_number=stage.stage_number, name=stage.name, approval_mode=stage.approval_mode,
            required_approvers=await _hydrate_approvers(session, stage.required_approver_ids),
            status=stage.status, escalates_at=stage.escalates_at, escalated_at=stage.escalated_at,
            decisions=decision_infos, created_at=stage.created_at, completed_at=stage.completed_at,
        ))

    return WorkflowRequestResponse(
        id=instance.id, workflow_definition_id=instance.workflow_definition_id, workflow_definition_name=definition.name if definition else "Unknown",
        title=title, description=description, justification=justification, batch_id=batch_id, batch_label=batch_label,
        payload=instance.payload or {}, requested_by=instance.requested_by, requested_by_display_name=requester.display_name if requester else None,
        instance_status=instance.status, current_stage_number=instance.current_stage_number, stages=stage_responses,
        can_decide=can_decide, created_at=instance.started_at, completed_at=instance.completed_at,
    )


async def submit_workflow_request(session: AsyncSession, data: WorkflowRequestCreate, actor_subject: str, request_id: str) -> WorkflowRequestResponse:
    definition = await _get_definition(session, data.workflow_definition_id)
    if definition.status != "ACTIVE":
        raise AccessPilotError("WORKFLOW_DEFINITION_NOT_ACTIVE", "This workflow is not currently accepting requests.", 409)
    requester_id = await _resolve_internal_user_id(session, actor_subject)
    if requester_id is None:
        raise AccessPilotError("USER_NOT_FOUND", "Your account could not be resolved.", 404)

    instance = await start_workflow_instance(session, definition, requester_id, data.payload, "WORKFLOW_REQUEST", data.title)
    workflow_request = WorkflowRequest(workflow_definition_id=definition.id, workflow_instance_id=instance.id, title=data.title, description=data.description, justification=data.justification, requested_by=requester_id)
    session.add(workflow_request)
    await session.flush()
    instance.subject_id = workflow_request.id

    await record_audit(session, action="WORKFLOW_REQUEST_SUBMITTED", target_type="WORKFLOW_REQUEST", target_id=workflow_request.id, actor_user_id=requester_id, request_id=request_id, metadata={"workflow_definition_id": str(definition.id), "title": data.title})
    await session.commit()
    await session.refresh(instance)
    return await _to_instance_response(session, instance, actor_subject)


async def decide_stage(session: AsyncSession, instance_id: UUID, stage_instance_id: UUID, data: WorkflowDecideRequest, actor_subject: str, actor_roles: tuple[str, ...], request_id: str) -> WorkflowRequestResponse:
    instance = await _get_instance(session, instance_id)
    stage_instance = await session.get(WorkflowStageInstance, stage_instance_id)
    if stage_instance is None or stage_instance.workflow_instance_id != instance.id:
        raise AccessPilotError("WORKFLOW_STAGE_NOT_FOUND", "That stage does not belong to this workflow request.", 404)
    if stage_instance.status != "PENDING":
        raise AccessPilotError("WORKFLOW_STAGE_ALREADY_DECIDED", "This stage has already been decided.", 409)

    actor_id, overrides = await _authorize_stage_decision(session, stage_instance, actor_subject, actor_roles)

    already_voted = (await session.scalars(select(WorkflowStageDecision.id).where(WorkflowStageDecision.workflow_stage_instance_id == stage_instance.id, WorkflowStageDecision.decided_by == actor_id))).first()
    if already_voted is not None:
        raise AccessPilotError("WORKFLOW_STAGE_ALREADY_DECIDED", "You have already decided this stage.", 409)

    session.add(WorkflowStageDecision(workflow_stage_instance_id=stage_instance.id, decided_by=actor_id, decision=data.decision, justification=data.justification))
    await session.flush()

    finalize_as: Optional[str] = None
    if overrides:
        # Admin, or an escalated fallback approver: a complete substitute decision-maker for the whole stage,
        # regardless of approval_mode or how many primary votes are already in.
        finalize_as = data.decision
    elif stage_instance.approval_mode == "ANY_OF":
        finalize_as = data.decision
    else:  # ALL_OF
        if data.decision == "REJECTED":
            finalize_as = "REJECTED"
        else:
            approved_ids = {str(d) for d in (await session.scalars(select(WorkflowStageDecision.decided_by).where(WorkflowStageDecision.workflow_stage_instance_id == stage_instance.id, WorkflowStageDecision.decision == "APPROVED")))}
            required_ids = {str(u) for u in stage_instance.required_approver_ids}
            if required_ids <= approved_ids:
                finalize_as = "APPROVED"

    if finalize_as is None:
        # ALL_OF, still waiting on the rest of the named approvers — record the vote, finalize nothing yet.
        await record_audit(session, action="WORKFLOW_STAGE_VOTE_RECORDED", target_type="WORKFLOW_STAGE_INSTANCE", target_id=stage_instance.id, actor_user_id=actor_id, request_id=request_id, metadata={"decision": data.decision})
        await session.commit()
        return await _to_instance_response(session, instance, actor_subject, actor_roles)

    stage_instance.status = finalize_as
    stage_instance.completed_at = datetime.now(timezone.utc)
    await record_audit(session, action=f"WORKFLOW_STAGE_{finalize_as}", target_type="WORKFLOW_STAGE_INSTANCE", target_id=stage_instance.id, actor_user_id=actor_id, request_id=request_id, metadata={"justification": data.justification, "via_override": overrides})

    if finalize_as == "REJECTED":
        instance.status = "REJECTED"
        instance.completed_at = datetime.now(timezone.utc)
        if instance.subject_type not in ("ASSIGNMENT", "USER_ATTRIBUTES", "ACCESS_REVIEW_ITEM") and instance.requested_by != actor_id:
            title = await _resolve_subject_label(session, instance)
            await create_notification(session, instance.requested_by, "WORKFLOW_REQUEST_REJECTED", f'Your request "{title}" was rejected.', link="/workflow-requests")
    else:
        stage_defs = list((await session.scalars(select(WorkflowStageDefinition).where(WorkflowStageDefinition.workflow_definition_id == instance.workflow_definition_id).order_by(WorkflowStageDefinition.stage_number))).all())
        subject_label = await _resolve_subject_label(session, instance)
        await _advance(session, instance, stage_defs, from_stage_number=stage_instance.stage_number + 1, subject_label=subject_label)
        if instance.status == "APPROVED" and instance.subject_type not in ("ASSIGNMENT", "USER_ATTRIBUTES", "ACCESS_REVIEW_ITEM") and instance.requested_by != actor_id:
            await create_notification(session, instance.requested_by, "WORKFLOW_REQUEST_APPROVED", f'Your request "{subject_label}" was approved.', link="/workflow-requests")

    if instance.status in ("APPROVED", "REJECTED"):
        await _apply_subject_outcome(session, instance, actor_id, request_id)

    await session.commit()
    await session.refresh(instance)
    return await _to_instance_response(session, instance, actor_subject, actor_roles)


async def cancel_workflow_instance(session: AsyncSession, instance_id: UUID, actor_subject: str, actor_roles: tuple[str, ...], request_id: str) -> WorkflowRequestResponse:
    instance = await _get_instance(session, instance_id)
    actor_id = await _resolve_internal_user_id(session, actor_subject)
    if actor_id != instance.requested_by and "AccessPilot.Admin" not in actor_roles:
        raise AccessPilotError("ACCESS_DENIED", "Only the requester or an administrator can cancel this request.", 403)
    if instance.status != "PENDING":
        raise AccessPilotError("WORKFLOW_REQUEST_ALREADY_DECIDED", "This request is no longer pending.", 409)

    current_stage = (await session.scalars(select(WorkflowStageInstance).where(WorkflowStageInstance.workflow_instance_id == instance.id, WorkflowStageInstance.status == "PENDING"))).first()
    if current_stage is not None:
        current_stage.status = "CANCELLED"
        current_stage.completed_at = datetime.now(timezone.utc)
    instance.status = "CANCELLED"
    instance.completed_at = datetime.now(timezone.utc)

    await record_audit(session, action="WORKFLOW_REQUEST_CANCELLED", target_type="WORKFLOW_INSTANCE", target_id=instance.id, actor_user_id=actor_id, request_id=request_id)
    await _apply_subject_outcome(session, instance, actor_id, request_id)
    await session.commit()
    await session.refresh(instance)
    return await _to_instance_response(session, instance, actor_subject, actor_roles)


async def cancel_system_instance(session: AsyncSession, instance_id: UUID, request_id: str) -> None:
    """System-initiated cancel — no requester/Admin authorization check, unlike cancel_workflow_instance above.
    Used when an owning process closes out a still-PENDING instance on its own authority, not a human's (e.g. an
    Access Review campaign closing with an item's workflow still mid-flight — see
    app.services.access_reviews.complete_campaign). Same state transition as cancel_workflow_instance, minus the
    actor check, and does not return a response (nothing is waiting on one)."""
    instance = await session.get(WorkflowInstance, instance_id)
    if instance is None or instance.status != "PENDING":
        return
    current_stage = (await session.scalars(select(WorkflowStageInstance).where(WorkflowStageInstance.workflow_instance_id == instance.id, WorkflowStageInstance.status == "PENDING"))).first()
    if current_stage is not None:
        current_stage.status = "CANCELLED"
        current_stage.completed_at = datetime.now(timezone.utc)
    instance.status = "CANCELLED"
    instance.completed_at = datetime.now(timezone.utc)
    await record_audit(session, action="WORKFLOW_REQUEST_CANCELLED", target_type="WORKFLOW_INSTANCE", target_id=instance.id, actor_user_id=None, request_id=request_id, metadata={"via_system": True})
    await _apply_subject_outcome(session, instance, None, request_id)


async def list_my_workflow_requests(session: AsyncSession, actor_subject: str) -> list[WorkflowRequestResponse]:
    actor_id = await _resolve_internal_user_id(session, actor_subject)
    if actor_id is None:
        return []
    instances = list((await session.scalars(select(WorkflowInstance).where(WorkflowInstance.requested_by == actor_id).order_by(WorkflowInstance.started_at.desc()))).all())
    return [await _to_instance_response(session, instance, actor_subject) for instance in instances]


async def list_pending_my_decision(session: AsyncSession, actor_subject: str, actor_roles: tuple[str, ...]) -> list[WorkflowRequestResponse]:
    actor_id = await _resolve_internal_user_id(session, actor_subject)
    if actor_id is None:
        return []
    actor_str = str(actor_id)
    is_admin = "AccessPilot.Admin" in actor_roles
    pending_stages = list((await session.scalars(select(WorkflowStageInstance).where(WorkflowStageInstance.status == "PENDING"))).all())
    matching_instance_ids: set[UUID] = set()
    for stage in pending_stages:
        if is_admin:
            matching_instance_ids.add(stage.workflow_instance_id)
            continue
        required = {str(u) for u in stage.required_approver_ids}
        fallback = {str(u) for u in (stage.fallback_approver_ids or [])}
        if actor_str in required or (actor_str in fallback and stage.escalated_at is not None):
            matching_instance_ids.add(stage.workflow_instance_id)
    if not matching_instance_ids:
        return []
    instances = list((await session.scalars(select(WorkflowInstance).where(WorkflowInstance.id.in_(matching_instance_ids)))).all())
    return [await _to_instance_response(session, instance, actor_subject, actor_roles) for instance in instances]


async def list_all_workflow_requests(session: AsyncSession) -> list[WorkflowRequestResponse]:
    instances = list((await session.scalars(select(WorkflowInstance).order_by(WorkflowInstance.started_at.desc()))).all())
    return [await _to_instance_response(session, instance, None) for instance in instances]


# ---------------------------------------------------------------- escalation sweep (called by the worker)

async def sweep_workflow_escalations(session: AsyncSession) -> int:
    """Finds PENDING stage instances past their escalates_at threshold that haven't been escalated yet, notifies
    their fallback approvers, and marks them escalated — exactly once each (escalated_at IS NULL is the only
    guard needed). One stage's failure never blocks the others, the same 'don't let one bad row stop the rest'
    convention every other periodic sweep in this app already uses."""
    now = datetime.now(timezone.utc)
    due = list((await session.scalars(select(WorkflowStageInstance).where(
        WorkflowStageInstance.status == "PENDING",
        WorkflowStageInstance.escalates_at.isnot(None),
        WorkflowStageInstance.escalates_at <= now,
        WorkflowStageInstance.escalated_at.is_(None),
    ))).all())
    escalated_count = 0
    for stage in due:
        try:
            stage.escalated_at = now
            for raw_id in (stage.fallback_approver_ids or []):
                await create_notification(session, UUID(str(raw_id)), "WORKFLOW_STAGE_ESCALATED", f'A workflow stage ("{stage.name}") needs your decision — the primary approver has not responded in time.', link="/workflow-requests")
            await record_audit(session, action="WORKFLOW_STAGE_ESCALATED", target_type="WORKFLOW_STAGE_INSTANCE", target_id=stage.id, request_id="workflow-escalation-sweep", metadata={"stage_name": stage.name})
            await session.commit()
            escalated_count += 1
        except Exception:
            await session.rollback()
            continue
    return escalated_count
