from pathlib import Path
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AccessPilotError
from app.db.session import get_db
from app.schemas.directory import SyncRunResponse
from app.schemas.providers import AgentKeyResponse, DomainResponse, ProviderCreate, ProviderCredentialUpdate, ProviderResponse, ProviderUpdate
from app.security.auth import AuthenticatedUser, require_permission
from app.services import agent as agent_service
from app.services.directory_sync import run_sync
from app.services.provider_configuration import create_provider, delete_provider, get_provider, list_domains, list_providers, list_sync_runs, set_provider_credentials, test_provider, update_provider

AGENT_SCRIPT_PATH = Path(__file__).resolve().parents[4] / "agent" / "accesspilot_agent.py"

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

# ---- Active Directory connectivity agent (Phase 1 — see app.services.agent) ----

@router.post("/{provider_id}/agent/generate-key", response_model=AgentKeyResponse)
async def generate_agent_key(provider_id: UUID, request: Request, _: AuthenticatedUser = Depends(provider_manage), db: AsyncSession = Depends(get_db)):
    """Admin-only, same gate as every other provider-credential action. Returns the plaintext key once; only its
    hash is kept. Regenerating invalidates whatever key an already-running agent process is using."""
    api_key = await agent_service.generate_agent_key(db, provider_id, request.state.request_id)
    return AgentKeyResponse(api_key=api_key)


@router.post("/{provider_id}/agent/heartbeat", response_model=ProviderResponse)
async def agent_heartbeat(provider_id: UUID, db: AsyncSession = Depends(get_db), x_agent_key: Optional[str] = Header(default=None)):
    """Called by the on-prem agent process itself, not an interactive admin session — authenticated by its own
    per-provider API key (X-Agent-Key header), never a user bearer token."""
    if not x_agent_key:
        raise AccessPilotError("INVALID_AGENT_KEY", "Missing X-Agent-Key header.", 401)
    return await agent_service.record_heartbeat(db, provider_id, x_agent_key)


@router.get("/{provider_id}/agent/download")
async def download_agent(provider_id: UUID, _: AuthenticatedUser = Depends(provider_manage), db: AsyncSession = Depends(get_db)):
    """Same connectivity-agent script for every Active Directory provider — it takes the provider id and agent key
    as environment variables at run time (see agent/README.md), nothing provider-specific is baked into the file
    itself. Still gated behind PROVIDER_MANAGE, matching every other provider-configuration action."""
    await get_provider(db, provider_id)
    return FileResponse(AGENT_SCRIPT_PATH, media_type="text/x-python", filename="accesspilot_agent.py")
