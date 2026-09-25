from __future__ import annotations

from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AccessPilotError
from app.models import AccessAssignment, AccessPackage, AccessPackageAssignment, AccessPackageItem, Application, BirthrightPolicy, Group, Role, User
from app.schemas.assignments import AssignmentCreate
from app.schemas.policies import BirthrightActionJson, BirthrightPolicyCreate, BirthrightPolicyJson, BirthrightPolicyUpdate
from app.services.assignments import _resolve_target, create_assignment, revoke_assignment
from app.services.audit import record_audit

NON_FINAL_ASSIGNMENT_STATUSES = ("REJECTED", "REVOKED", "EXPIRED")

# The JSON rule engine's condition "field" values (the requested schema's own names, e.g. "employmentStatus")
# mapped to the real User attribute they read — a small, explicit allowlist rather than a raw getattr(user,
# field), so an unrecognized field name is a clear 400 at save time, not a rule that silently never matches.
CONDITION_FIELD_MAP = {"department": "department", "job_title": "job_title", "employmentStatus": "status", "status": "status", "email": "email"}
CONDITION_OPERATORS = ("EQUALS", "NOT_EQUALS")
ACTION_RESOURCE_TYPES = ("GROUP", "ROLE", "APPLICATION", "PACKAGE")


async def list_birthright_policies(session: AsyncSession) -> list[BirthrightPolicy]:
    return list((await session.execute(select(BirthrightPolicy).order_by(BirthrightPolicy.created_at.desc()))).scalars().all())


async def create_birthright_policy(session: AsyncSession, data: BirthrightPolicyCreate, request_id: str) -> BirthrightPolicy:
    existing = (await session.execute(select(BirthrightPolicy).where(BirthrightPolicy.name == data.name))).scalar_one_or_none()
    if existing is not None:
        raise AccessPilotError("POLICY_NAME_TAKEN", "A birthright policy with this name already exists.", 409)
    if data.resource_type == "PACKAGE":
        # PACKAGE isn't a target _resolve_target knows about (that helper is shared with plain single-resource
        # assignments — see app.services.assignments) — validate it exists here instead, the same "own, local
        # check, nothing shared touched" approach app.services.sod already uses for its own PACKAGE entities.
        if await session.get(AccessPackage, data.resource_id) is None:
            raise AccessPilotError("PACKAGE_NOT_FOUND", "The access package was not found.", 404)
    else:
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


async def _grant_birthright_target(session: AsyncSession, user: User, policy: BirthrightPolicy, resource_type: str, resource_id: UUID, app_role_external_id: Optional[str], assignment_type: str, actor_subject: str, request_id: str, bypass_activation: bool) -> Optional[UUID]:
    """One target grant, tagged birthright_policy_id — used for every single grant this module ever makes,
    whether it came from a legacy policy's own single resource_type/resource_id, one entry of an advanced
    (JSON) policy's actions list, or one item of a PACKAGE action's expansion. Returns None (no-op) if already
    held, or if the grant itself fails — never raises, so one bad target never blocks the rest of this policy's
    actions or any other policy."""
    already_held = (await session.execute(select(AccessAssignment.id).where(
        AccessAssignment.user_id == user.id,
        AccessAssignment.resource_type == resource_type,
        AccessAssignment.resource_id == resource_id,
        AccessAssignment.app_role_external_id == app_role_external_id,
        AccessAssignment.status.notin_(NON_FINAL_ASSIGNMENT_STATUSES),
    ))).scalars().first()
    if already_held:
        return None
    data = AssignmentCreate(user_id=user.id, resource_type=resource_type, resource_id=resource_id, app_role_external_id=app_role_external_id, assignment_type=assignment_type, justification=f"Birthright policy: {policy.name}", bypass_activation=bypass_activation)
    try:
        assignment, _ = await create_assignment(session, data, actor_subject, request_id, birthright_policy_id=policy.id)
        return assignment.id
    except AccessPilotError:
        return None


def _condition_matches(user: User, condition: dict) -> bool:
    attr_name = CONDITION_FIELD_MAP.get(condition["field"])
    user_value = getattr(user, attr_name, None) if attr_name else None
    if not user_value:
        return False
    equal = user_value.strip().lower() == str(condition["value"]).strip().lower()
    return equal if condition["operator"] == "EQUALS" else not equal


def _policy_matches_user(user: User, policy: BirthrightPolicy) -> bool:
    """Advanced (JSON) policies evaluate their AND/OR condition list; a legacy policy keeps its original single
    department/job_title equality check, byte-for-byte unchanged."""
    if policy.conditions_json:
        results = [_condition_matches(user, condition) for condition in policy.conditions_json]
        return all(results) if (policy.conditions_operator or "AND") == "AND" else any(results)
    user_value = getattr(user, policy.match_field, None) if policy.match_field else None
    return bool(user_value) and bool(policy.match_value) and user_value.strip().lower() == policy.match_value.strip().lower()


def _policy_action_specs(policy: BirthrightPolicy) -> list[dict]:
    """One dict per grant action, uniform regardless of source: an advanced policy's own actions_json list, or a
    legacy policy's single resource_type/resource_id/app_role_external_id/assignment_type wrapped as a one-item
    list — so evaluate_birthright_policies below has exactly one loop, not two parallel code paths."""
    if policy.actions_json:
        return policy.actions_json
    return [{"resource_type": policy.resource_type, "resource_id": str(policy.resource_id), "app_role_external_id": policy.app_role_external_id, "assignment_type": policy.assignment_type}]


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
    # Privileged (PU) / Test (TU) shadow accounts never inherit attribute-based rules meant for real hires —
    # their access is always a deliberate, individual Admin decision (see app.services.privileged_accounts).
    # Applies unconditionally, regardless of an advanced policy's own scope.identity_type — see that field's
    # comment on the model for why this existing exclusion is never reopened by it.
    if user.account_type != "NORMAL":
        return []

    active_policies = (await session.execute(select(BirthrightPolicy).where(BirthrightPolicy.status == "ACTIVE"))).scalars().all()
    created_ids: list[UUID] = []
    for policy in active_policies:
        if not _policy_matches_user(user, policy):
            continue
        for spec in _policy_action_specs(policy):
            resource_type = spec["resource_type"]
            resource_id = spec["resource_id"] if isinstance(spec["resource_id"], UUID) else UUID(str(spec["resource_id"]))
            app_role_external_id = spec.get("app_role_external_id")
            assignment_type = spec.get("assignment_type") or policy.assignment_type
            if resource_type == "PACKAGE":
                # Live-resolved against the package's current items, never duplicated/cached — same convention
                # app.services.sod already uses for its own PACKAGE entities, so editing a package's items is
                # automatically reflected the next time this evaluates, no migration/backfill needed. Each item
                # becomes its OWN birthright_policy_id-tagged AccessAssignment (not a batched PACKAGE assignment
                # via app.services.packages) so reconcile_birthright_policies_for_user below — which revokes by
                # birthright_policy_id alone, regardless of resource_type — covers every item automatically with
                # no changes of its own. The tradeoff: a package-sourced grant shows as N individual assignments
                # rather than one grouped 📦 row in My Access, since it deliberately never touches packages.py's
                # own batch-tracking (AccessPackageAssignment) — reuse without disturbing that flow, not a fork.
                items = list((await session.scalars(select(AccessPackageItem).where(AccessPackageItem.package_id == resource_id))).all())
                batch_id = uuid4()
                for item in items:
                    granted_id = await _grant_birthright_target(session, user, policy, item.resource_type, item.resource_id, item.app_role_external_id, assignment_type, actor_subject, request_id, bypass_activation)
                    if granted_id:
                        created_ids.append(granted_id)
                        # Provenance only: records "this grant came from package X" so My Access, User Detail and
                        # Access Review's PACKAGE scope can see it. Does not change how the grant is tagged or
                        # reconciled (that stays birthright_policy_id-driven).
                        session.add(AccessPackageAssignment(package_id=resource_id, package_assignment_id=batch_id, assignment_id=granted_id, user_id=user.id))
                        await session.commit()
                continue
            granted_id = await _grant_birthright_target(session, user, policy, resource_type, resource_id, app_role_external_id, assignment_type, actor_subject, request_id, bypass_activation)
            if granted_id:
                created_ids.append(granted_id)
    return created_ids


async def backfill_birthright_package_links(session: AsyncSession) -> int:
    """One-time repair for grants made before evaluate_birthright_policies started recording package provenance:
    for every birthright policy with a PACKAGE action, link each still-unlinked, birthright-tagged assignment
    that matches one of that package's items back to the package. Idempotent (skips anything already linked).
    Returns how many links were created."""
    created = 0
    policies = (await session.execute(select(BirthrightPolicy))).scalars().all()
    for policy in policies:
        for spec in _policy_action_specs(policy):
            if spec.get("resource_type") != "PACKAGE":
                continue
            package_id = spec["resource_id"] if isinstance(spec["resource_id"], UUID) else UUID(str(spec["resource_id"]))
            items = list((await session.scalars(select(AccessPackageItem).where(AccessPackageItem.package_id == package_id))).all())
            batches: dict[UUID, UUID] = {}
            for item in items:
                rows = (await session.scalars(select(AccessAssignment).where(
                    AccessAssignment.birthright_policy_id == policy.id,
                    AccessAssignment.resource_type == item.resource_type,
                    AccessAssignment.resource_id == item.resource_id,
                    AccessAssignment.app_role_external_id == item.app_role_external_id,
                    AccessAssignment.status.notin_(NON_FINAL_ASSIGNMENT_STATUSES),  # (name is historical: these are the FINAL states)
                ))).all()
                for assignment in rows:
                    already = (await session.scalars(select(AccessPackageAssignment.id).where(AccessPackageAssignment.assignment_id == assignment.id))).first()
                    if already:
                        continue
                    batch_id = batches.setdefault(assignment.user_id, uuid4())
                    session.add(AccessPackageAssignment(package_id=package_id, package_assignment_id=batch_id, assignment_id=assignment.id, user_id=assignment.user_id))
                    created += 1
    await session.commit()
    return created


async def reconcile_birthright_policies_for_user(session: AsyncSession, user_id: UUID, actor_subject: str, request_id: str) -> dict:
    """Mover reconciliation: call this whenever a user's department/job_title has just changed (from either
    direction — a directory sync picking up a change made in Entra/Okta, or an admin editing it inside
    AccessPilot itself). Diffs what birthright policies match the user's CURRENT attributes against what's
    currently granted specifically BY a birthright policy (identified via AccessAssignment.birthright_policy_id,
    never a manual grant — see that column's comment on the model):
      - A live (non-final) birthright-granted assignment whose policy no longer matches the user (policy
        disabled, deleted, or the attribute value changed) is revoked for real — same universal revoke path an
        Admin's own revoke button uses, so it removes the real Entra/Graph grant too.
      - Any policy that newly matches and isn't already held gets granted via evaluate_birthright_policies
        (ELIGIBLE-only, same as every other birthright grant — a mover doesn't get instant real access any more
        than a joiner does).
    One resource's failure (e.g. a real Graph error revoking a specific group) never blocks the others, the same
    "don't let one bad target block the rest" reasoning evaluate_birthright_policies already uses for grants."""
    user = await session.get(User, user_id)
    if user is None:
        raise AccessPilotError("USER_NOT_FOUND", "The user was not found.", 404)

    active_policies = (await session.execute(select(BirthrightPolicy).where(BirthrightPolicy.status == "ACTIVE"))).scalars().all()
    matching_policy_ids = {policy.id for policy in active_policies if _policy_matches_user(user, policy)}

    live_birthright_assignments = (await session.execute(select(AccessAssignment).where(
        AccessAssignment.user_id == user_id,
        AccessAssignment.birthright_policy_id.isnot(None),
        AccessAssignment.status.notin_(NON_FINAL_ASSIGNMENT_STATUSES),
    ))).scalars().all()

    revoked_ids: list[UUID] = []
    for assignment in live_birthright_assignments:
        if assignment.birthright_policy_id in matching_policy_ids:
            continue
        owning_policy = await session.get(BirthrightPolicy, assignment.birthright_policy_id)
        # reconciliation_enabled=False makes a grant "sticky" — the requested schema's reconciliation.enabled /
        # removeWhenConditionFails knob (this app treats the two as one setting, see the model's own comment):
        # once granted, this specific policy's access is never taken back automatically, only a manual Admin
        # revoke can. Defaults True, so every pre-existing policy (which has no way to set this False) keeps
        # today's always-auto-revoke behavior exactly as before.
        if owning_policy is not None and not owning_policy.reconciliation_enabled:
            continue
        try:
            await revoke_assignment(session, assignment.id, actor_subject, "Birthright policy no longer applies — the user's department/job title changed.", request_id, reason="BIRTHRIGHT_POLICY_NO_LONGER_APPLIES")
            revoked_ids.append(assignment.id)
        except AccessPilotError:
            continue

    granted_ids = await evaluate_birthright_policies(session, user_id, actor_subject, request_id)
    return {"revoked": revoked_ids, "granted": granted_ids}


_RESOURCE_MODELS = {"GROUP": Group, "ROLE": Role, "APPLICATION": Application, "PACKAGE": AccessPackage}


async def _resolve_resource_by_key(session: AsyncSession, resource_type: str, key: str) -> tuple[UUID, str]:
    """Turns the JSON schema's human-readable `resource` string (e.g. "GRP-IT-EMPLOYEES", "Microsoft-365") into
    a real internal (id, display_name) pair — matched against the target's own external_id or name (Group/Role/
    Application) or just name (AccessPackage, which has no external_id). A literal UUID is also accepted
    directly, so a policy can reference a resource unambiguously even if its name/external_id ever changes.
    Raises a 404 AccessPilotError if nothing matches — same "the target must genuinely exist" guarantee the
    legacy dropdown-driven create path already has via _resolve_target."""
    model = _RESOURCE_MODELS.get(resource_type)
    if model is None:
        raise AccessPilotError("VALIDATION_ERROR", f"Unsupported resourceType '{resource_type}'.", 400)
    try:
        row = await session.get(model, UUID(key))
    except (ValueError, TypeError):
        row = None
    if row is None:
        condition = model.name == key if resource_type == "PACKAGE" else or_(model.external_id == key, model.name == key)
        row = (await session.execute(select(model).where(condition))).scalars().first()
    if row is None:
        raise AccessPilotError(f"{resource_type}_NOT_FOUND", f"No {resource_type.lower()} matches '{key}'.", 404)
    return row.id, row.name


async def _display_name_for_action(session: AsyncSession, resource_type: str, resource_id: UUID) -> str:
    model = _RESOURCE_MODELS.get(resource_type)
    row = await session.get(model, resource_id) if model else None
    return row.name if row else str(resource_id)


async def resolve_action_resources(session: AsyncSession, actions: list[BirthrightActionJson]) -> list[dict[str, Any]]:
    """The JSON editor's 'Check resources' button: for each action, does `resource` actually match something
    real right now, and what does it resolve to — WITHOUT saving anything or requiring the rest of the policy
    (conditions, name, etc.) to be valid yet. Every action is resolved independently; one not-found resource
    never stops the others from reporting their own real result, so an admin editing a multi-action policy sees
    exactly which ones need fixing."""
    results: list[dict[str, Any]] = []
    for action in actions:
        entry: dict[str, Any] = {"resourceType": action.resourceType, "resource": action.resource}
        if action.resourceType not in ACTION_RESOURCE_TYPES:
            entry.update(found=False, error=f"Unsupported resourceType '{action.resourceType}'.")
        elif not action.resource.strip():
            entry.update(found=False, error="No resource name given.")
        else:
            try:
                resource_id, name = await _resolve_resource_by_key(session, action.resourceType, action.resource.strip())
                entry.update(found=True, resolvedId=resource_id, resolvedName=name)
            except AccessPilotError as exc:
                entry.update(found=False, error=exc.message)
        results.append(entry)
    return results


def _validate_json_payload(payload: BirthrightPolicyJson) -> None:
    if payload.policyType != "BIRTHRIGHT":
        raise AccessPilotError("VALIDATION_ERROR", "policyType must be \"BIRTHRIGHT\" — this endpoint doesn't support any other policy type.", 400)
    if payload.rule.operator not in ("AND", "OR"):
        raise AccessPilotError("VALIDATION_ERROR", "rule.operator must be \"AND\" or \"OR\".", 400)
    for condition in payload.rule.conditions:
        if condition.field not in CONDITION_FIELD_MAP:
            raise AccessPilotError("VALIDATION_ERROR", f"Unsupported condition field '{condition.field}'. Supported: {sorted(CONDITION_FIELD_MAP)}.", 400)
        if condition.operator not in CONDITION_OPERATORS:
            raise AccessPilotError("VALIDATION_ERROR", f"Unsupported condition operator '{condition.operator}'. Supported: {', '.join(CONDITION_OPERATORS)}.", 400)
        if not condition.value.strip():
            raise AccessPilotError("VALIDATION_ERROR", "Every condition needs a non-empty value.", 400)
    for action in payload.actions:
        if action.action != "ASSIGN":
            raise AccessPilotError("VALIDATION_ERROR", "Only the \"ASSIGN\" action is supported — birthright policies only ever grant, never revoke directly (see reconciliation.enabled for automatic removal).", 400)
        if action.resourceType not in ACTION_RESOURCE_TYPES:
            raise AccessPilotError("VALIDATION_ERROR", f"Unsupported action resourceType '{action.resourceType}'. Supported: {', '.join(ACTION_RESOURCE_TYPES)}.", 400)
        if not action.resource.strip():
            raise AccessPilotError("VALIDATION_ERROR", "Every action needs a non-empty resource.", 400)
        if action.resourceType == "APPLICATION" and not (action.appRoleExternalId or "").strip():
            # Mirrors AssignmentCreate's own hard requirement (schemas/assignments.py) — catching it here, with a
            # clean 400, is what stops a malformed APPLICATION action from surfacing as an unhandled pydantic
            # ValidationError deep inside evaluate_birthright_policies instead.
            raise AccessPilotError("VALIDATION_ERROR", "An APPLICATION action needs appRoleExternalId (which application role to grant).", 400)
    if payload.audit.enabled is False:
        # A real, deliberate refusal, not a silent no-op — AccessPilot audits every policy-driven grant/revoke
        # unconditionally (app.services.audit), so honoring "please turn it off" would be a lie the API told back.
        raise AccessPilotError("AUDIT_CANNOT_BE_DISABLED", "AccessPilot always audits policy-driven access changes — audit.enabled cannot be set to false.", 400)


async def _build_fields_from_json(session: AsyncSession, payload: BirthrightPolicyJson) -> dict[str, Any]:
    _validate_json_payload(payload)
    actions_json = []
    for action in payload.actions:
        resource_id, _ = await _resolve_resource_by_key(session, action.resourceType, action.resource.strip())
        actions_json.append({"resource_type": action.resourceType, "resource_id": str(resource_id), "app_role_external_id": action.appRoleExternalId, "assignment_type": action.assignmentType})
    return {
        "name": payload.name.strip(),
        "status": payload.status,
        "external_policy_id": (payload.policyId or "").strip() or None,
        "scope_identity_type": payload.scope.identityType,
        "conditions_json": [{"field": c.field, "operator": c.operator, "value": c.value.strip()} for c in payload.rule.conditions],
        "conditions_operator": payload.rule.operator,
        "actions_json": actions_json,
        "reconciliation_enabled": bool(payload.reconciliation.enabled),
    }


async def _assert_name_and_policy_id_available(session: AsyncSession, name: str, external_policy_id: Optional[str], exclude_id: Optional[UUID] = None) -> None:
    name_query = select(BirthrightPolicy.id).where(BirthrightPolicy.name == name)
    if exclude_id is not None:
        name_query = name_query.where(BirthrightPolicy.id != exclude_id)
    if (await session.execute(name_query)).scalar_one_or_none() is not None:
        raise AccessPilotError("POLICY_NAME_TAKEN", "A birthright policy with this name already exists.", 409)
    if external_policy_id:
        id_query = select(BirthrightPolicy.id).where(BirthrightPolicy.external_policy_id == external_policy_id)
        if exclude_id is not None:
            id_query = id_query.where(BirthrightPolicy.id != exclude_id)
        if (await session.execute(id_query)).scalar_one_or_none() is not None:
            raise AccessPilotError("POLICY_ID_TAKEN", "A birthright policy with this policyId already exists.", 409)


async def create_birthright_policy_from_json(session: AsyncSession, payload: BirthrightPolicyJson, request_id: str) -> BirthrightPolicy:
    """The JSON-authoring counterpart to create_birthright_policy — same name-uniqueness rule, same real-target-
    must-exist guarantee (via _resolve_resource_by_key instead of _resolve_target), but supports the full
    multi-condition/multi-action shape in one call instead of the legacy form's single condition/single grant."""
    fields = await _build_fields_from_json(session, payload)
    await _assert_name_and_policy_id_available(session, fields["name"], fields["external_policy_id"])
    row = BirthrightPolicy(**fields)
    session.add(row)
    await session.flush()
    await record_audit(session, action="BIRTHRIGHT_POLICY_CREATED", target_type="BIRTHRIGHT_POLICY", target_id=row.id, request_id=request_id, metadata={"name": row.name, "mode": "json", "conditions": fields["conditions_json"], "actions": fields["actions_json"]})
    await session.commit()
    await session.refresh(row)
    return row


async def update_birthright_policy_from_json(session: AsyncSession, policy_id: UUID, payload: BirthrightPolicyJson, request_id: str) -> BirthrightPolicy:
    """Replaces a policy's entire condition/action shape wholesale from JSON — including a LEGACY (simple-form)
    policy, which this converts to advanced/JSON mode in the same move (its old match_field/match_value/
    resource_type/resource_id are cleared so evaluate_birthright_policies/reconcile_birthright_policies_for_user
    unambiguously prefer the new JSON columns going forward, never a mixed state)."""
    row = await _get_policy(session, policy_id)
    fields = await _build_fields_from_json(session, payload)
    await _assert_name_and_policy_id_available(session, fields["name"], fields["external_policy_id"], exclude_id=policy_id)
    for key, value in fields.items():
        setattr(row, key, value)
    row.match_field = None
    row.match_value = None
    row.resource_type = None
    row.resource_id = None
    row.app_role_external_id = None
    await record_audit(session, action="BIRTHRIGHT_POLICY_UPDATED", target_type="BIRTHRIGHT_POLICY", target_id=row.id, request_id=request_id, metadata={"mode": "json", "conditions": fields["conditions_json"], "actions": fields["actions_json"]})
    await session.commit()
    await session.refresh(row)
    return row


async def to_birthright_policy_json(session: AsyncSession, policy: BirthrightPolicy) -> BirthrightPolicyJson:
    """The read side — represents ANY policy, legacy or advanced, in the requested JSON shape, so the frontend's
    'view/edit as JSON' works uniformly regardless of how the policy was originally created. A legacy policy's
    single match_field/match_value becomes a one-condition rule; its single resource_type/resource_id becomes a
    one-entry actions list — editing and saving that JSON back converts it to advanced mode (see
    update_birthright_policy_from_json), which is expected, not a bug."""
    if policy.conditions_json:
        conditions = [{"field": c["field"], "operator": c["operator"], "value": c["value"]} for c in policy.conditions_json]
        operator = policy.conditions_operator or "AND"
    else:
        conditions = [{"field": policy.match_field, "operator": "EQUALS", "value": policy.match_value}]
        operator = "AND"
    actions = []
    for spec in _policy_action_specs(policy):
        resource_id = UUID(str(spec["resource_id"]))
        display_name = await _display_name_for_action(session, spec["resource_type"], resource_id)
        action: dict[str, Any] = {"action": "ASSIGN", "resourceType": spec["resource_type"], "resource": display_name, "assignmentType": spec.get("assignment_type") or policy.assignment_type}
        if spec.get("app_role_external_id"):
            action["appRoleExternalId"] = spec["app_role_external_id"]
        actions.append(action)
    return BirthrightPolicyJson(
        id=policy.id,
        policyId=policy.external_policy_id or f"BR-{str(policy.id)[:8].upper()}",
        policyType="BIRTHRIGHT",
        name=policy.name,
        status=policy.status,
        scope={"identityType": policy.scope_identity_type},
        rule={"operator": operator, "conditions": conditions},
        actions=actions,
        reconciliation={"enabled": policy.reconciliation_enabled, "removeWhenConditionFails": policy.reconciliation_enabled},
        audit={"enabled": True},
    )
