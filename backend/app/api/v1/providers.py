from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.directory import SyncRunResponse
from app.schemas.providers import DomainResponse, ProviderCreate, ProviderCredentialUpdate, ProviderResponse, ProviderUpdate
from app.security.auth import AuthenticatedUser, require_permission
from app.services.directory_sync import run_sync
from app.services.provider_configuration import create_provider, delete_provider, get_provider, list_domains, list_providers, list_sync_runs, set_provider_credentials, test_provider, update_provider

router = APIRouter(prefix="/providers", tags=["providers"])
provider_read = require_permission("PROVIDER_READ")
provider_manage = require_permission("PROVIDER_MANAGE")
provider_sync = require_permission("PROVIDER_SYNC")
sync_read = require_permission("SYNC_READ")
admin = Depends(provider_read)

@router.get("", response_model=list[ProviderResponse])
async def providers(_: AuthenticatedUser = admin, db: AsyncSession = Depends(get_db)): return await list_providers(db)

@router.get("/{provider_id}", response_model=ProviderResponse)
async def provider(provider_id: UUID, _: AuthenticatedUser = admin, db: AsyncSession = Depends(get_db)): return await get_provider(db, provider_id)

@router.post("", response_model=ProviderResponse, status_code=201)
async def create(data: ProviderCreate, request: Request, _: AuthenticatedUser = Depends(provider_manage), db: AsyncSession = Depends(get_db)): return await create_provider(db, data, request.state.request_id)

@router.patch("/{provider_id}", response_model=ProviderResponse)
async def update(provider_id: UUID, data: ProviderUpdate, request: Request, _: AuthenticatedUser = Depends(provider_manage), db: AsyncSession = Depends(get_db)): return await update_provider(db, provider_id, data, request.state.request_id)

@router.delete("/{provider_id}", status_code=204)
async def delete(provider_id: UUID, request: Request, _: AuthenticatedUser = Depends(provider_manage), db: AsyncSession = Depends(get_db)): await delete_provider(db, provider_id, request.state.request_id)

@router.patch("/{provider_id}/credentials", response_model=ProviderResponse)
async def update_credentials(provider_id: UUID, data: ProviderCredentialUpdate, request: Request, _: AuthenticatedUser = Depends(provider_manage), db: AsyncSession = Depends(get_db)): return await set_provider_credentials(db, provider_id, data.graph_client_id, data.graph_client_secret, request.state.request_id)

@router.post("/{provider_id}/test-connection", response_model=ProviderResponse)
async def test_connection(provider_id: UUID, request: Request, _: AuthenticatedUser = Depends(provider_manage), db: AsyncSession = Depends(get_db)): return await test_provider(db, provider_id, request.state.request_id)

@router.post("/{provider_id}/sync", response_model=SyncRunResponse)
async def sync_provider(provider_id: UUID, request: Request, _: AuthenticatedUser = Depends(provider_sync), db: AsyncSession = Depends(get_db)):
    provider = await get_provider(db, provider_id)
    return await run_sync(db, provider, request.state.request_id)

@router.get("/{provider_id}/sync-runs", response_model=list[SyncRunResponse])
async def provider_sync_runs(provider_id: UUID, _: AuthenticatedUser = Depends(sync_read), db: AsyncSession = Depends(get_db)):
    await get_provider(db, provider_id)
    return await list_sync_runs(db, provider_id)

@router.get("/{provider_id}/domains", response_model=list[DomainResponse])
async def provider_domains(provider_id: UUID, _: AuthenticatedUser = Depends(provider_read), db: AsyncSession = Depends(get_db)):
    return await list_domains(db, provider_id)
