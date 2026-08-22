from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AccessPilotError
from app.models import AuditLog, IdentityProvider
from app.providers.entra import EntraProvider
from app.providers.mock import MockProvider
from app.schemas.providers import ProviderCreate, ProviderUpdate


def _connector(provider: IdentityProvider):
    return MockProvider() if provider.type == "MOCK" else EntraProvider(provider)

async def list_providers(session: AsyncSession) -> list[IdentityProvider]:
    return list((await session.scalars(select(IdentityProvider).order_by(IdentityProvider.created_at))).all())

async def get_provider(session: AsyncSession, provider_id: UUID) -> IdentityProvider:
    provider = await session.get(IdentityProvider, provider_id)
    if not provider: raise AccessPilotError("PROVIDER_NOT_FOUND", "The provider was not found.", 404)
    return provider

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
    await _audit(session, provider, "PROVIDER_DELETED", request_id); await session.delete(provider); await session.commit()

async def test_provider(session: AsyncSession, provider_id: UUID, request_id: str) -> IdentityProvider:
    provider = await get_provider(session, provider_id)
    try:
        connected = await _connector(provider).test_connection()
    except (NotImplementedError, ValueError, ConnectionError, TimeoutError) as exc:
        provider.status = "ERROR"; await _audit(session, provider, "PROVIDER_CONNECTION_TESTED", request_id, "FAILURE"); await session.commit()
        raise AccessPilotError("PROVIDER_AUTHENTICATION_FAILED", "Provider credentials are not configured or could not be verified.", 502) from exc
    provider.status = "CONNECTED" if connected else "ERROR"
    await _audit(session, provider, "PROVIDER_CONNECTION_TESTED", request_id, "SUCCESS" if connected else "FAILURE"); await session.commit(); await session.refresh(provider); return provider
