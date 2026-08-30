from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import delete as sql_delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AccessPilotError
from app.models import AuditLog, IdentityProvider, SyncError, SyncRun
from app.providers.base import NormalizedDomain
from app.providers.entra import EntraProvider
from app.providers.graph_client import GraphError
from app.providers.mock import MockProvider
from app.schemas.providers import ProviderCreate, ProviderUpdate
from app.security.credential_encryption import CredentialEncryptionError, encrypt_credential


def _connector(provider: IdentityProvider):
    return MockProvider() if provider.type == "MOCK" else EntraProvider(provider)

async def list_providers(session: AsyncSession) -> list[IdentityProvider]:
    return list((await session.scalars(select(IdentityProvider).order_by(IdentityProvider.created_at))).all())

async def get_provider(session: AsyncSession, provider_id: UUID) -> IdentityProvider:
    provider = await session.get(IdentityProvider, provider_id)
    if not provider: raise AccessPilotError("PROVIDER_NOT_FOUND", "The provider was not found.", 404)
    return provider

async def list_sync_runs(session: AsyncSession, provider_id: UUID) -> list[SyncRun]:
    return list((await session.scalars(select(SyncRun).where(SyncRun.provider_id == provider_id).order_by(SyncRun.started_at.desc()))).all())

async def _audit(session: AsyncSession, provider: IdentityProvider, action: str, request_id: str, result: str = "SUCCESS") -> None:
    session.add(AuditLog(action=action, target_type="PROVIDER", target_id=provider.id, provider_id=provider.id, request_id=request_id, result=result))

async def create_provider(session: AsyncSession, data: ProviderCreate, request_id: str) -> IdentityProvider:
    provider = IdentityProvider(name=data.name, type=data.provider_type, status="CONFIGURED", tenant_id=data.tenant_id, client_id=data.client_id, authority=str(data.authority) if data.authority else None, api_audience=data.api_audience, api_scope=data.api_scope, redirect_uri_metadata=data.redirect_uri_metadata, configuration_ref=data.configuration_ref)
    session.add(provider); await session.flush(); await _audit(session, provider, "PROVIDER_CREATED", request_id); await session.commit(); await session.refresh(provider); return provider

async def update_provider(session: AsyncSession, provider_id: UUID, data: ProviderUpdate, request_id: str) -> IdentityProvider:
    provider = await get_provider(session, provider_id)
    for key, value in data.model_dump(exclude_unset=True).items():
        if key == "authority" and value is not None: value = str(value)
        if key == "provider_type": key = "type"
        setattr(provider, key, value)
    if provider.status == "CONNECTED": provider.status = "CONFIGURED"
    await _audit(session, provider, "PROVIDER_UPDATED", request_id); await session.commit(); await session.refresh(provider); return provider

async def delete_provider(session: AsyncSession, provider_id: UUID, request_id: str) -> None:
    provider = await get_provider(session, provider_id)
    deleted_id = provider.id
    # Audit history is preserved (never deleted) but its provider_id FK is cleared so the row can be removed.
    await session.execute(update(AuditLog).where(AuditLog.provider_id == deleted_id).values(provider_id=None))
    sync_run_ids = (await session.execute(select(SyncRun.id).where(SyncRun.provider_id == deleted_id))).scalars().all()
    if sync_run_ids:
        await session.execute(sql_delete(SyncError).where(SyncError.sync_run_id.in_(sync_run_ids)))
        await session.execute(sql_delete(SyncRun).where(SyncRun.provider_id == deleted_id))
    try:
        await session.delete(provider)
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise AccessPilotError("PROVIDER_CONFLICT", "This provider still has synced users, groups, or roles and cannot be deleted.", 409) from exc
    session.add(AuditLog(action="PROVIDER_DELETED", target_type="PROVIDER", target_id=deleted_id, provider_id=None, request_id=request_id, result="SUCCESS"))
    await session.commit()

async def set_provider_credentials(session: AsyncSession, provider_id: UUID, graph_client_id: str | None, graph_client_secret: str, request_id: str) -> IdentityProvider:
    provider = await get_provider(session, provider_id)
    try:
        encrypted = encrypt_credential(graph_client_secret)
    except CredentialEncryptionError as exc:
        raise AccessPilotError("PROVIDER_UNAVAILABLE", str(exc), 503) from exc
    provider.graph_client_secret_encrypted = encrypted
    if graph_client_id:
        provider.graph_client_id = graph_client_id
    provider.configuration_ref = "DATABASE_ENCRYPTED"
    if provider.status == "CONNECTED":
        provider.status = "CONFIGURED"
    await _audit(session, provider, "PROVIDER_CREDENTIAL_CONFIGURED", request_id)
    await session.commit()
    await session.refresh(provider)
    return provider

async def test_provider(session: AsyncSession, provider_id: UUID, request_id: str) -> IdentityProvider:
    provider = await get_provider(session, provider_id)
    try:
        connected = await _connector(provider).test_connection()
    except (NotImplementedError, ValueError, ConnectionError, TimeoutError) as exc:
        provider.status = "ERROR"; await _audit(session, provider, "PROVIDER_CONNECTION_TESTED", request_id, "FAILURE"); await session.commit()
        raise AccessPilotError("PROVIDER_AUTHENTICATION_FAILED", "Provider credentials are not configured or could not be verified.", 502) from exc
    provider.status = "CONNECTED" if connected else "ERROR"
    await _audit(session, provider, "PROVIDER_CONNECTION_TESTED", request_id, "SUCCESS" if connected else "FAILURE"); await session.commit(); await session.refresh(provider); return provider

async def list_domains(session: AsyncSession, provider_id: UUID) -> list[NormalizedDomain]:
    """Live-fetches the connector's registered domains — the mapping engine's "fetch their fields and domain"
    step — so an Admin can pick a KNOWN VERIFIED one for provisioning instead of trusting an arbitrary email
    domain from a CSV row (which the target IdP would otherwise silently reject at account-creation time)."""
    provider = await get_provider(session, provider_id)
    try:
        return await _connector(provider).get_domains()
    except GraphError as exc:
        raise AccessPilotError(exc.code, exc.message, exc.status_code) from exc
