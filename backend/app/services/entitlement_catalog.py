from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AccessPilotError
from app.models import Application, EntitlementCatalogEntry, Group, Role, User
from app.schemas.entitlement_catalog import EntitlementCatalogEntryResponse, EntitlementCatalogEntryUpdate
from app.services.assignments import _app_role_name, _resolve_target
from app.services.audit import record_audit


async def _hydrate_entry(session: AsyncSession, row: EntitlementCatalogEntry) -> EntitlementCatalogEntryResponse:
    try:
        _, display_name, _ = await _resolve_target(session, row.resource_type, row.resource_id)
    except AccessPilotError:
        display_name = row.resource_type
    if row.resource_type == "APPLICATION" and row.app_role_external_id:
        application = await session.get(Application, row.resource_id)
        role_name = _app_role_name(application, row.app_role_external_id)
        if role_name:
            display_name = f"{display_name} — {role_name}"
    owner_display_name = None
    if row.owner_id is not None:
        owner = await session.get(User, row.owner_id)
        owner_display_name = owner.display_name if owner else None
    return EntitlementCatalogEntryResponse(
        id=row.id, resource_type=row.resource_type, resource_id=row.resource_id, app_role_external_id=row.app_role_external_id or None,
        resource_display_name=display_name, description=row.description, risk_tier=row.risk_tier,
        owner_id=row.owner_id, owner_display_name=owner_display_name, updated_at=row.updated_at,
    )


async def _real_entitlement_targets(session: AsyncSession) -> list[tuple[str, UUID, str, str]]:
    """Every real entitlement that should have a catalog row: (resource_type, resource_id, app_role_external_id,
    display_name). Groups/Roles are addressed whole (app_role_external_id=""); an Application with AppRoles is
    addressed per-role, one target per role — an Application with no AppRoles defined is addressed whole, the
    same as a Group/Role."""
    targets: list[tuple[str, UUID, str, str]] = []
    for group in (await session.execute(select(Group))).scalars().all():
        targets.append(("GROUP", group.id, "", group.name))
    for role in (await session.execute(select(Role))).scalars().all():
        targets.append(("ROLE", role.id, "", role.name))
    for application in (await session.execute(select(Application))).scalars().all():
        if application.app_roles:
            for app_role in application.app_roles:
                role_id = app_role.get("id")
                if not role_id:
                    continue
                role_name = app_role.get("name") or role_id
                targets.append(("APPLICATION", application.id, role_id, f"{application.name} — {role_name}"))
        else:
            targets.append(("APPLICATION", application.id, "", application.name))
    return targets


async def list_catalog(session: AsyncSession) -> list[EntitlementCatalogEntryResponse]:
    """Live-joins every real entitlement against its catalog row, creating a blank (LOW risk, no description/
    owner) row on the fly for anything that doesn't have one yet — so the catalog is always complete, including
    for a group/role/app added by directory sync after the catalog was first used, with no separate backfill step
    that could ever go stale."""
    targets = await _real_entitlement_targets(session)

    existing_rows = (await session.execute(select(EntitlementCatalogEntry))).scalars().all()
    existing_by_key = {(row.resource_type, row.resource_id, row.app_role_external_id): row for row in existing_rows}

    created_any = False
    for resource_type, resource_id, app_role_external_id, _ in targets:
        key = (resource_type, resource_id, app_role_external_id)
        if key not in existing_by_key:
            row = EntitlementCatalogEntry(resource_type=resource_type, resource_id=resource_id, app_role_external_id=app_role_external_id)
            session.add(row)
            existing_by_key[key] = row
            created_any = True
    if created_any:
        await session.commit()
        for row in existing_by_key.values():
            await session.refresh(row)

    owner_ids = {row.owner_id for row in existing_by_key.values() if row.owner_id is not None}
    owners: dict[UUID, str] = {}
    if owner_ids:
        owners = {user.id: user.display_name for user in (await session.execute(select(User).where(User.id.in_(owner_ids)))).scalars().all()}

    responses = []
    for resource_type, resource_id, app_role_external_id, display_name in targets:
        row = existing_by_key[(resource_type, resource_id, app_role_external_id)]
        responses.append(EntitlementCatalogEntryResponse(
            id=row.id, resource_type=row.resource_type, resource_id=row.resource_id, app_role_external_id=row.app_role_external_id or None,
            resource_display_name=display_name, description=row.description, risk_tier=row.risk_tier,
            owner_id=row.owner_id, owner_display_name=owners.get(row.owner_id) if row.owner_id else None,
            updated_at=row.updated_at,
        ))
    responses.sort(key=lambda r: r.resource_display_name)
    return responses


async def compute_unclassified_entitlements(session: AsyncSession) -> list[EntitlementCatalogEntryResponse]:
    """Every entitlement still sitting at its auto-created default (LOW risk, no description, no owner) — the
    catalog has no explicit "reviewed" flag distinguishing a deliberate "LOW, nothing to add" classification from
    one nobody has ever looked at, so this uses the create-time default combination as the best honest proxy
    available, same convention as DORMANT_ACCESS_DAYS being a proxy for "unused" with no real usage telemetry.
    Drives the completeness nudge on the Entitlement Catalog page and the matching SoC widget — without this,
    nothing ever prompts an admin to actually classify what they've built a catalog for."""
    catalog = await list_catalog(session)
    return [entry for entry in catalog if entry.risk_tier == "LOW" and entry.description is None and entry.owner_id is None]


async def get_risk_tier(session: AsyncSession, resource_type: str, resource_id: UUID, app_role_external_id: str | None) -> str:
    """An item with no catalog entry yet (nobody has ever risk-rated it) defaults to LOW, matching list_catalog()'s
    own default when it auto-creates a blank row — never blocks on an entitlement nobody has classified."""
    row = (await session.execute(select(EntitlementCatalogEntry).where(
        EntitlementCatalogEntry.resource_type == resource_type, EntitlementCatalogEntry.resource_id == resource_id,
        EntitlementCatalogEntry.app_role_external_id == (app_role_external_id or ""),
    ))).scalars().first()
    return row.risk_tier if row else "LOW"


async def require_workflow_for_high_risk_items(session: AsyncSession, items: list[tuple[str, UUID, str | None]], workflow_definition_id: UUID | None) -> None:
    """A HIGH/CRITICAL-risk entitlement (per the Entitlement Catalog) must always be routed through a workflow —
    never silently granted via a single approver or no approval at all. No-ops entirely once a workflow IS already
    attached (workflow_definition_id is not None): the workflow's own stages are the approval. items is
    (resource_type, resource_id, app_role_external_id) per item being assigned."""
    if workflow_definition_id is not None:
        return
    for resource_type, resource_id, app_role_external_id in items:
        tier = await get_risk_tier(session, resource_type, resource_id, app_role_external_id)
        if tier in ("HIGH", "CRITICAL"):
            try:
                _, display_name, _ = await _resolve_target(session, resource_type, resource_id)
            except AccessPilotError:
                display_name = resource_type
            raise AccessPilotError("WORKFLOW_REQUIRED_FOR_HIGH_RISK_ITEM", f'"{display_name}" is tagged {tier} risk in the Entitlement Catalog and must be routed through a workflow — attach one before assigning.', 409)


async def update_catalog_entry(session: AsyncSession, entry_id: UUID, data: EntitlementCatalogEntryUpdate, request_id: str) -> EntitlementCatalogEntryResponse:
    row = await session.get(EntitlementCatalogEntry, entry_id)
    if row is None:
        raise AccessPilotError("ENTITLEMENT_CATALOG_ENTRY_NOT_FOUND", "The catalog entry was not found.", 404)
    changes = data.model_dump(exclude_unset=True)
    if "owner_id" in changes and changes["owner_id"] is not None and await session.get(User, changes["owner_id"]) is None:
        raise AccessPilotError("USER_NOT_FOUND", "The selected owner was not found.", 404)
    for field, value in changes.items():
        setattr(row, field, value)
    await record_audit(session, action="ENTITLEMENT_CATALOG_ENTRY_UPDATED", target_type="ENTITLEMENT_CATALOG_ENTRY", target_id=row.id, request_id=request_id, metadata={k: (str(v) if isinstance(v, UUID) else v) for k, v in changes.items()})
    await session.commit()
    await session.refresh(row)
    return await _hydrate_entry(session, row)
