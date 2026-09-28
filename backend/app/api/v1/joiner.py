from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.joiner import JoinerCreate, JoinerResponse, JoinerTargetResponse, JoinerTargetUpdate
from app.security.auth import AuthenticatedUser, require_permission
from app.services import joiner as joiner_service

router = APIRouter(prefix="/lifecycle", tags=["joiner"])
joiner_read = require_permission("JOINER_READ")
joiner_manage = require_permission("JOINER_MANAGE")


@router.get("/joiner-targets", response_model=list[JoinerTargetResponse])
async def joiner_targets(_: AuthenticatedUser = Depends(joiner_read), db: AsyncSession = Depends(get_db)):
    return await joiner_service.list_targets(db)


@router.put("/joiner-targets/{provider_id}", response_model=list[JoinerTargetResponse])
async def set_joiner_target(provider_id: UUID, data: JoinerTargetUpdate, request: Request, _: AuthenticatedUser = Depends(joiner_manage), db: AsyncSession = Depends(get_db)):
    return await joiner_service.set_target_enabled(db, provider_id, data.enabled, request.state.request_id)


@router.get("/joiners", response_model=list[JoinerResponse])
async def list_joiners(_: AuthenticatedUser = Depends(joiner_read), db: AsyncSession = Depends(get_db)):
    return await joiner_service.list_joiners(db)


@router.post("/joiners", response_model=JoinerResponse, status_code=201)
async def create_joiner(data: JoinerCreate, request: Request, actor: AuthenticatedUser = Depends(joiner_manage), db: AsyncSession = Depends(get_db)):
    """The response carries each account's one-time temporary password — it is never stored and cannot be shown again."""
    response, _ = await joiner_service.create_joiner(db, data, actor.directory_object_id, request.state.request_id)
    return response


@router.post("/joiners/{joiner_id}/retry", response_model=JoinerResponse)
async def retry_joiner(joiner_id: UUID, request: Request, _: AuthenticatedUser = Depends(joiner_manage), db: AsyncSession = Depends(get_db)):
    response, _passwords = await joiner_service.retry_joiner(db, joiner_id, request.state.request_id)
    return response


@router.delete("/joiners/{joiner_id}", response_model=JoinerResponse)
async def cancel_joiner(joiner_id: UUID, request: Request, delete_accounts: bool = False, actor: AuthenticatedUser = Depends(joiner_manage), db: AsyncSession = Depends(get_db)):
    return await joiner_service.cancel_joiner(db, joiner_id, actor.directory_object_id, request.state.request_id, delete_accounts=delete_accounts)
