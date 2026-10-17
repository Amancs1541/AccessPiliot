from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AccessPilotError
from app.models import Application, ApplicationOwner, IdentityProvider, NhiRiskException, User
from app.providers.base import NewApplicationRequest, ProviderConflictError
from app.providers.graph_client import GraphError
from app.services.assignments import _resolve_internal_user_id
from app.services.audit import record_audit
from app.services.audit_read import list_audit_logs_by_target
from app.services.provider_configuration import _connector

# How far ahead a soon-to-expire credential is flagged — same "warn before it becomes an incident" idea as the
# SoD engine's own exception-expiring-warning window (see SodNotificationSettings), just a fixed constant here
# rather than an admin-tunable setting, since this is the first pass of NHI risk detection.
CREDENTIAL_EXPIRING_SOON_WINDOW_DAYS = 30

RISK_NO_OWNER = "NO_OWNER"
RISK_CREDENTIAL_EXPIRED = "CREDENTIAL_EXPIRED"
RISK_CREDENTIAL_EXPIRING_SOON = "CREDENTIAL_EXPIRING_SOON"
VALID_RISK_TYPES = {RISK_NO_OWNER, RISK_CREDENTIAL_EXPIRED, RISK_CREDENTIAL_EXPIRING_SOON}

# SERVICE_PRINCIPAL/MANAGED_IDENTITY/OKTA_SERVICE_APP are set automatically from what the connector actually
# reports (see EntraProvider._application_from_graph / OktaProvider._application_from_okta). AI_AGENT/API/BOT/
# OTHER have no reliable auto-detection signal today from either provider's API — they only ever get set via a
# deliberate NHIAdmin call to set_nhi_type below, never invented by a sync.
NHI_TYPE_SERVICE_PRINCIPAL = "SERVICE_PRINCIPAL"
NHI_TYPE_MANAGED_IDENTITY = "MANAGED_IDENTITY"
NHI_TYPE_OKTA_SERVICE_APP = "OKTA_SERVICE_APP"
NHI_TYPE_AI_AGENT = "AI_AGENT"
NHI_TYPE_API = "API"
NHI_TYPE_BOT = "BOT"
NHI_TYPE_OTHER = "OTHER"
VALID_NHI_TYPES = {NHI_TYPE_SERVICE_PRINCIPAL, NHI_TYPE_MANAGED_IDENTITY, NHI_TYPE_OKTA_SERVICE_APP, NHI_TYPE_AI_AGENT, NHI_TYPE_API, NHI_TYPE_BOT, NHI_TYPE_OTHER}


def compute_risk_flags(application: Application, has_owner: bool, now: datetime, warning_days: int = CREDENTIAL_EXPIRING_SOON_WINDOW_DAYS) -> list[str]:
    """Live-computed against current state — same "compute live, persist only accepted exceptions" pattern as
    the SoD engine (see app.services.sod). Callers subtract any currently-active NhiRiskException risk_types
    from this list before showing it to a viewer. warning_days defaults to the original fixed constant so any
    caller that doesn't thread through NhiNotificationSettings.credential_expiring_warning_days (e.g. a direct
    test call) keeps today's exact behavior; real callers pass the admin-configured value instead."""
    flags: list[str] = []
    if not has_owner:
        flags.append(RISK_NO_OWNER)
    expires_at = application.credential_expires_at
    if expires_at is not None:
        # SQLite (used in tests) doesn't round-trip tzinfo on a DateTime(timezone=True) column the way real
        # Postgres does — same defensive normalization already used by workers/expiration.py's _is_expired().
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= now:
            flags.append(RISK_CREDENTIAL_EXPIRED)
        elif expires_at <= now + timedelta(days=warning_days):
            flags.append(RISK_CREDENTIAL_EXPIRING_SOON)
    return flags


async def _get_application(session: AsyncSession, application_id: UUID) -> Application:
    application = await session.get(Application, application_id)
    if application is None:
        raise AccessPilotError("APPLICATION_NOT_FOUND", "The application was not found.", 404)
    return application


async def _active_exceptions_by_application(session: AsyncSession, application_ids: list[UUID], now: datetime) -> dict[UUID, set[str]]:
    if not application_ids:
        return {}
    rows = (await session.execute(
        select(NhiRiskException).where(
            NhiRiskException.application_id.in_(application_ids),
            NhiRiskException.revoked_at.is_(None),
            NhiRiskException.expires_at > now,
        )
    )).scalars().all()
    result: dict[UUID, set[str]] = {}
    for row in rows:
        result.setdefault(row.application_id, set()).add(row.risk_type)
    return result


async def _owners_by_application(session: AsyncSession, application_ids: list[UUID]) -> dict[UUID, list[ApplicationOwner]]:
    if not application_ids:
        return {}
    rows = (await session.execute(select(ApplicationOwner).where(ApplicationOwner.application_id.in_(application_ids)))).scalars().all()
    result: dict[UUID, list[ApplicationOwner]] = {}
    for row in rows:
        result.setdefault(row.application_id, []).append(row)
    return result


def _to_response_dict(application: Application, owners: list[ApplicationOwner], provider: Optional[IdentityProvider], excepted_risk_types: set[str], users_by_id: dict[UUID, User], now: datetime, warning_days: int = CREDENTIAL_EXPIRING_SOON_WINDOW_DAYS) -> dict:
    has_owner = len(owners) > 0
    risk_flags = [flag for flag in compute_risk_flags(application, has_owner, now, warning_days) if flag not in excepted_risk_types]
    return {
        "id": application.id,
        "provider_id": application.provider_id,
        "provider_name": provider.name if provider else "",
        "provider_type": provider.type if provider else "",
        "external_id": application.external_id,
        "name": application.name,
        "status": application.status,
        "nhi_type": application.nhi_type,
        "nhi_type_overridden": application.nhi_type_overridden,
        "credential_expires_at": application.credential_expires_at,
        "credentials": application.nhi_credentials or [],
        "owners": [
            {"user_id": owner.user_id, "display_name": users_by_id[owner.user_id].display_name, "email": users_by_id[owner.user_id].email}
            for owner in owners if owner.user_id in users_by_id
        ],
        "risk_flags": risk_flags,
        "last_synced_at": application.last_synced_at,
    }


async def list_non_human_identities(session: AsyncSession) -> list[dict]:
    # Local import to dodge a circular import — nhi_notifications.py itself imports list_non_human_identities/
    # compute_risk_flags from this module, so the reverse direction has to stay function-scoped, same convention
    # as the sod.py/workflows.py imports elsewhere in this codebase.
    from app.services.nhi_notifications import get_nhi_notification_settings
    now = datetime.now(timezone.utc)
    warning_days = (await get_nhi_notification_settings(session)).credential_expiring_warning_days
    applications = (await session.execute(select(Application).order_by(Application.name))).scalars().all()
    application_ids = [application.id for application in applications]

    providers_by_id = {provider.id: provider for provider in (await session.execute(select(IdentityProvider))).scalars().all()}
    owners_by_application = await _owners_by_application(session, application_ids)
    exceptions_by_application = await _active_exceptions_by_application(session, application_ids, now)

    owner_user_ids = {owner.user_id for owners in owners_by_application.values() for owner in owners}
    users_by_id: dict[UUID, User] = {}
    if owner_user_ids:
        users_by_id = {user.id: user for user in (await session.execute(select(User).where(User.id.in_(owner_user_ids)))).scalars().all()}

    return [
        _to_response_dict(
            application, owners_by_application.get(application.id, []), providers_by_id.get(application.provider_id),
            exceptions_by_application.get(application.id, set()), users_by_id, now, warning_days,
        )
        for application in applications
    ]


async def get_non_human_identity(session: AsyncSession, application_id: UUID) -> dict:
    from app.services.nhi_notifications import get_nhi_notification_settings
    application = await _get_application(session, application_id)
    now = datetime.now(timezone.utc)
    warning_days = (await get_nhi_notification_settings(session)).credential_expiring_warning_days
    provider = await session.get(IdentityProvider, application.provider_id)
    owners = (await _owners_by_application(session, [application_id])).get(application_id, [])
    excepted = (await _active_exceptions_by_application(session, [application_id], now)).get(application_id, set())
    users_by_id: dict[UUID, User] = {}
    if owners:
        owner_user_ids = {owner.user_id for owner in owners}
        users_by_id = {user.id: user for user in (await session.execute(select(User).where(User.id.in_(owner_user_ids)))).scalars().all()}
    return _to_response_dict(application, owners, provider, excepted, users_by_id, now, warning_days)


async def get_summary(session: AsyncSession) -> dict:
    identities = await list_non_human_identities(session)
    by_type: dict[str, int] = {nhi_type: 0 for nhi_type in VALID_NHI_TYPES}
    for identity in identities:
        by_type[identity["nhi_type"]] = by_type.get(identity["nhi_type"], 0) + 1
    return {
        "total": len(identities),
        "no_owner": sum(1 for identity in identities if RISK_NO_OWNER in identity["risk_flags"]),
        "credential_expiring_soon": sum(1 for identity in identities if RISK_CREDENTIAL_EXPIRING_SOON in identity["risk_flags"]),
        "credential_expired": sum(1 for identity in identities if RISK_CREDENTIAL_EXPIRED in identity["risk_flags"]),
        "by_type": by_type,
    }


async def set_nhi_type(session: AsyncSession, application_id: UUID, nhi_type: str, actor_user_id: Optional[UUID], request_id: str) -> None:
    """A deliberate NHIAdmin classification (e.g. "this service principal is actually an AI agent/bot/API") —
    marks the row as manually overridden so a future sync (see directory_sync.upsert_application) never
    silently reverts it back to the connector's own auto-detected value."""
    application = await _get_application(session, application_id)
    if nhi_type not in VALID_NHI_TYPES:
        raise AccessPilotError("VALIDATION_ERROR", f"Unsupported NHI type: {nhi_type}", 400)
    previous_type = application.nhi_type
    application.nhi_type = nhi_type
    application.nhi_type_overridden = True
    await record_audit(session, action="NHI_TYPE_RECLASSIFIED", target_type="APPLICATION", target_id=application_id, request_id=request_id, actor_user_id=actor_user_id, metadata={"from": previous_type, "to": nhi_type})
    await session.commit()


async def assign_owner(session: AsyncSession, application_id: UUID, user_id: UUID, actor_user_id: Optional[UUID], request_id: str) -> None:
    await _get_application(session, application_id)
    user = await session.get(User, user_id)
    if user is None:
        raise AccessPilotError("USER_NOT_FOUND", "The user was not found.", 404)
    existing = (await session.execute(select(ApplicationOwner).where(ApplicationOwner.application_id == application_id, ApplicationOwner.user_id == user_id))).scalar_one_or_none()
    if existing is not None:
        return
    session.add(ApplicationOwner(application_id=application_id, user_id=user_id, assigned_by=actor_user_id))
    await record_audit(session, action="NHI_OWNER_ASSIGNED", target_type="APPLICATION", target_id=application_id, request_id=request_id, actor_user_id=actor_user_id, metadata={"user_id": str(user_id)})
    await session.commit()


async def remove_owner(session: AsyncSession, application_id: UUID, user_id: UUID, actor_user_id: Optional[UUID], request_id: str) -> None:
    await _get_application(session, application_id)
    existing = (await session.execute(select(ApplicationOwner).where(ApplicationOwner.application_id == application_id, ApplicationOwner.user_id == user_id))).scalar_one_or_none()
    if existing is None:
        return
    await session.delete(existing)
    await record_audit(session, action="NHI_OWNER_REMOVED", target_type="APPLICATION", target_id=application_id, request_id=request_id, actor_user_id=actor_user_id, metadata={"user_id": str(user_id)})
    await session.commit()


async def _set_enabled(session: AsyncSession, application_id: UUID, enabled: bool, actor_user_id: Optional[UUID], request_id: str) -> None:
    """Real, consequential: disables/enables the identity at the provider itself (Entra or Okta — dispatched via
    the same _connector() every other provider call in this app goes through), not just a local flag. A missing
    write permission (e.g. Entra's Application.ReadWrite.All not yet granted) surfaces as the normal, already-
    established PROVIDER_PERMISSION_DENIED error, never a silent no-op."""
    application = await _get_application(session, application_id)
    provider = await session.get(IdentityProvider, application.provider_id)
    if provider is None:
        raise AccessPilotError("PROVIDER_NOT_FOUND", "The provider for this identity was not found.", 404)
    try:
        await _connector(provider).set_application_enabled(application.external_id, enabled)
    except GraphError as exc:
        raise AccessPilotError(exc.code, exc.message, exc.status_code) from exc
    application.status = "ACTIVE" if enabled else "DISABLED"
    await record_audit(session, action="NHI_ENABLED" if enabled else "NHI_DISABLED", target_type="APPLICATION", target_id=application_id, request_id=request_id, actor_user_id=actor_user_id)
    await session.commit()


async def disable_identity(session: AsyncSession, application_id: UUID, actor_user_id: Optional[UUID], request_id: str) -> None:
    await _set_enabled(session, application_id, False, actor_user_id, request_id)


async def enable_identity(session: AsyncSession, application_id: UUID, actor_user_id: Optional[UUID], request_id: str) -> None:
    await _set_enabled(session, application_id, True, actor_user_id, request_id)


async def get_activity(session: AsyncSession, application_id: UUID, limit: int = 20) -> list[tuple]:
    await _get_application(session, application_id)
    return await list_audit_logs_by_target(session, "APPLICATION", application_id, limit)


async def get_permissions(session: AsyncSession, application_id: UUID) -> list[dict]:
    """Live-fetched, not synced/stored — what this identity is actually granted access to elsewhere right now.
    Entra role names come back from Graph as a GUID (appRoleId); resolved here (not in the provider layer, which
    has no DB access) against the resource's own already-synced app_roles, when that resource is itself a
    synced Application — otherwise the raw id is shown rather than a fabricated name."""
    application = await _get_application(session, application_id)
    provider = await session.get(IdentityProvider, application.provider_id)
    if provider is None:
        raise AccessPilotError("PROVIDER_NOT_FOUND", "The provider for this identity was not found.", 404)
    try:
        permissions = await _connector(provider).get_application_permissions(application.external_id)
    except GraphError as exc:
        raise AccessPilotError(exc.code, exc.message, exc.status_code) from exc
    resource_ids = {permission.resource_external_id for permission in permissions}
    resources_by_external_id: dict[str, Application] = {}
    if resource_ids:
        rows = (await session.execute(select(Application).where(Application.provider_id == application.provider_id, Application.external_id.in_(resource_ids)))).scalars().all()
        resources_by_external_id = {row.external_id: row for row in rows}
    results: list[dict] = []
    for permission in permissions:
        role_name = permission.role_name
        resource = resources_by_external_id.get(permission.resource_external_id)
        if resource and resource.app_roles:
            match = next((role for role in resource.app_roles if role.get("id") == permission.role_name), None)
            if match:
                role_name = match.get("name") or role_name
        results.append({"resource_display_name": permission.resource_display_name, "role_name": role_name})
    return results


async def get_directory_activity(session: AsyncSession, application_id: UUID) -> list[dict]:
    """Live-fetched, not synced/stored — this identity's real audit trail straight from the provider's own
    directory audit log (distinct from AccessPilot's own activity log, see get_activity above). A provider that
    doesn't support this (Okta, Active Directory, the mock connector) raises a clean 422 rather than a silent
    empty list, same PROVIDER_OPERATION_NOT_SUPPORTED contract register_new_identity already uses."""
    application = await _get_application(session, application_id)
    provider = await session.get(IdentityProvider, application.provider_id)
    if provider is None:
        raise AccessPilotError("PROVIDER_NOT_FOUND", "The provider for this identity was not found.", 404)
    try:
        events = await _connector(provider).get_directory_activity(application.external_id)
    except GraphError as exc:
        raise AccessPilotError(exc.code, exc.message, exc.status_code) from exc
    except NotImplementedError as exc:
        raise AccessPilotError("PROVIDER_OPERATION_NOT_SUPPORTED", f"Live directory activity isn't supported for {provider.type} yet.", 422) from exc
    return [{"timestamp": event.timestamp, "activity": event.activity, "actor": event.actor, "result": event.result, "detail": event.detail} for event in events]


async def create_risk_exception(session: AsyncSession, application_id: UUID, risk_type: str, justification: str, expires_at: datetime, actor_user_id: Optional[UUID], request_id: str) -> NhiRiskException:
    await _get_application(session, application_id)
    if risk_type not in VALID_RISK_TYPES:
        raise AccessPilotError("VALIDATION_ERROR", f"Unsupported NHI risk type: {risk_type}", 400)
    exception = NhiRiskException(application_id=application_id, risk_type=risk_type, justification=justification, approved_by=actor_user_id, expires_at=expires_at)
    session.add(exception)
    await session.flush()
    await record_audit(session, action="NHI_RISK_EXCEPTION_GRANTED", target_type="APPLICATION", target_id=application_id, request_id=request_id, actor_user_id=actor_user_id, metadata={"risk_type": risk_type})
    await session.commit()
    await session.refresh(exception)
    return exception


async def list_risk_exceptions(session: AsyncSession, application_id: UUID) -> list[NhiRiskException]:
    await _get_application(session, application_id)
    return list((await session.execute(select(NhiRiskException).where(NhiRiskException.application_id == application_id).order_by(NhiRiskException.created_at.desc()))).scalars().all())


async def revoke_risk_exception(session: AsyncSession, application_id: UUID, exception_id: UUID, actor_user_id: Optional[UUID], request_id: str) -> NhiRiskException:
    """Ends an accepted risk exception early. No cascading access change needed — unlike a SoD exception, an NHI
    risk exception never gated real access, only suppressed a risk FLAG (see compute_risk_flags) — so revoking
    it just lets that flag reappear next time risk is computed, live, same as today."""
    await _get_application(session, application_id)
    exception = await session.get(NhiRiskException, exception_id)
    if exception is None or exception.application_id != application_id:
        raise AccessPilotError("NHI_RISK_EXCEPTION_NOT_FOUND", "The risk exception was not found.", 404)
    if exception.revoked_at is not None:
        raise AccessPilotError("REQUEST_ALREADY_PROCESSED", "This risk exception has already been revoked.", 409)
    exception.revoked_at = datetime.now(timezone.utc)
    await record_audit(session, action="NHI_RISK_EXCEPTION_REVOKED", target_type="APPLICATION", target_id=application_id, request_id=request_id, actor_user_id=actor_user_id, metadata={"risk_type": exception.risk_type})
    await session.commit()
    await session.refresh(exception)
    return exception


async def list_my_non_human_identities(session: AsyncSession, actor_subject: str) -> list[dict]:
    """The caller's own owned identities, any status — the self-service 'My Owned Identities' dashboard. Gated
    only on being authenticated (no NHI_READ/NHIAdmin required), same convention as
    app.services.assignments.list_my_assignments's own /assignments/mine."""
    actor_id = await _resolve_internal_user_id(session, actor_subject)
    if actor_id is None:
        return []
    application_ids = list((await session.scalars(select(ApplicationOwner.application_id).where(ApplicationOwner.user_id == actor_id))).all())
    if not application_ids:
        return []
    from app.services.nhi_notifications import get_nhi_notification_settings
    now = datetime.now(timezone.utc)
    warning_days = (await get_nhi_notification_settings(session)).credential_expiring_warning_days
    applications = (await session.execute(select(Application).where(Application.id.in_(application_ids)).order_by(Application.name))).scalars().all()
    providers_by_id = {provider.id: provider for provider in (await session.execute(select(IdentityProvider))).scalars().all()}
    owners_by_application = await _owners_by_application(session, application_ids)
    exceptions_by_application = await _active_exceptions_by_application(session, application_ids, now)
    owner_user_ids = {owner.user_id for owners in owners_by_application.values() for owner in owners}
    users_by_id = {user.id: user for user in (await session.execute(select(User).where(User.id.in_(owner_user_ids)))).scalars().all()} if owner_user_ids else {}
    return [
        _to_response_dict(application, owners_by_application.get(application.id, []), providers_by_id.get(application.provider_id), exceptions_by_application.get(application.id, set()), users_by_id, now, warning_days)
        for application in applications
    ]


async def register_new_identity(session: AsyncSession, provider_id: UUID, display_name: str, nhi_type: str, owner_user_id: UUID, actor_user_id: Optional[UUID], request_id: str) -> Application:
    """Registers a brand-new NHI directly at the provider, instead of only ever sync-discovering one. Owner is
    mandatory — a brand-new NHI should never start life already flagged NO_OWNER. The local Application row is
    only ever created on a REAL connector-side success; a provider rejection (e.g. PROVIDER_PERMISSION_DENIED if
    Application.ReadWrite.All isn't granted — see EntraProvider.create_application's own docstring) never leaves
    behind a local row pointing at nothing real."""
    if nhi_type not in VALID_NHI_TYPES:
        raise AccessPilotError("VALIDATION_ERROR", f"Unsupported NHI type: {nhi_type}", 400)
    provider = await session.get(IdentityProvider, provider_id)
    if provider is None:
        raise AccessPilotError("PROVIDER_NOT_FOUND", "The provider was not found.", 404)
    owner = await session.get(User, owner_user_id)
    if owner is None:
        raise AccessPilotError("USER_NOT_FOUND", "The selected owner was not found.", 404)
    try:
        normalized = await _connector(provider).create_application(NewApplicationRequest(display_name=display_name))
    except ProviderConflictError as exc:
        raise AccessPilotError("NHI_NAME_TAKEN", f"A non-human identity named {display_name} already exists in {provider.name} ({exc}).", 409) from exc
    except GraphError as exc:
        raise AccessPilotError(exc.code, exc.message, exc.status_code) from exc
    except NotImplementedError as exc:
        raise AccessPilotError("PROVIDER_OPERATION_NOT_SUPPORTED", f"Registering a new non-human identity isn't supported for {provider.type} yet.", 422) from exc
    application = Application(
        provider_id=provider_id, external_id=normalized.external_id, name=normalized.name, status=normalized.status,
        app_roles=[{"id": role.external_id, "name": role.name, "description": role.description} for role in normalized.app_roles] or None,
        nhi_type=nhi_type, nhi_type_overridden=True, credential_expires_at=normalized.credential_expires_at,
        nhi_credentials=[{"credential_type": c.credential_type, "display_name": c.display_name, "expires_at": c.expires_at.isoformat() if c.expires_at else None} for c in normalized.credentials] or None,
        last_synced_at=datetime.now(timezone.utc),
    )
    session.add(application)
    await session.flush()
    session.add(ApplicationOwner(application_id=application.id, user_id=owner_user_id, assigned_by=actor_user_id))
    await record_audit(session, action="NHI_REGISTERED", target_type="APPLICATION", target_id=application.id, provider_id=provider_id, request_id=request_id, actor_user_id=actor_user_id, metadata={"name": display_name, "nhi_type": nhi_type, "owner_user_id": str(owner_user_id)})
    await session.commit()
    await session.refresh(application)
    return application


async def refresh_application_from_provider(session: AsyncSession, application_id: UUID, actor_user_id: Optional[UUID], request_id: str) -> dict:
    """On-demand, single-identity live refresh — re-fetches this one identity's current state directly from the
    provider (status, app roles, and critically its real credentials — see EntraProvider._application_from_graph's
    own docstring for why the synced copy can lag behind what Entra's "Certificates & secrets" blade actually
    shows) without waiting for the next scheduled tenant-wide sync. Reuses the existing bulk get_applications()
    (there's no single-entity fetch on the connector interface, and NHI counts are small enough that this is a
    reasonable on-demand admin action, not a hot path) and updates this one row via the same upsert_application()
    the real sync already uses, so a manual refresh can never drift from normal sync behavior."""
    application = await _get_application(session, application_id)
    provider = await session.get(IdentityProvider, application.provider_id)
    if provider is None:
        raise AccessPilotError("PROVIDER_NOT_FOUND", "The provider for this identity was not found.", 404)
    try:
        normalized_list = await _connector(provider).get_applications()
    except GraphError as exc:
        raise AccessPilotError(exc.code, exc.message, exc.status_code) from exc
    matched = next((item for item in normalized_list if item.external_id == application.external_id), None)
    if matched is None:
        raise AccessPilotError("NHI_NOT_FOUND_AT_PROVIDER", "This identity could not be found at the provider anymore — it may have been deleted there.", 404)
    from app.services.directory_sync import upsert_application
    await upsert_application(session, provider.id, matched)
    await record_audit(session, action="NHI_REFRESHED_FROM_PROVIDER", target_type="APPLICATION", target_id=application_id, provider_id=provider.id, request_id=request_id, actor_user_id=actor_user_id)
    await session.commit()
    return await get_non_human_identity(session, application_id)
