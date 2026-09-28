from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.accounts import AccountEnabledRequest, IdentityAccountResponse, PersonAccountsActionResponse
from app.security.auth import AuthenticatedUser, require_permission
from app.services import accounts as accounts_service
from app.services import leaver_followup
from app.services.accounts import _get_user

router = APIRouter(prefix="/users", tags=["accounts"])
accounts_read = require_permission("IDENTITY_ACCOUNT_READ")
accounts_manage = require_permission("IDENTITY_ACCOUNT_MANAGE")


@router.get("/{user_id}/accounts", response_model=list[IdentityAccountResponse])
async def list_accounts(user_id: UUID, _: AuthenticatedUser = Depends(accounts_read), db: AsyncSession = Depends(get_db)):
    return await accounts_service.list_accounts(db, user_id)


@router.post("/{user_id}/accounts/disable-all", response_model=PersonAccountsActionResponse)
async def disable_all(user_id: UUID, request: Request, actor: AuthenticatedUser = Depends(accounts_manage), db: AsyncSession = Depends(get_db)):
    return await accounts_service.set_person_enabled(db, user_id, False, actor.directory_object_id, request.state.request_id)


@router.post("/{user_id}/accounts/enable-all", response_model=PersonAccountsActionResponse)
async def enable_all(user_id: UUID, request: Request, actor: AuthenticatedUser = Depends(accounts_manage), db: AsyncSession = Depends(get_db)):
    await leaver_followup.guard_enable(db, user_id)
    return await accounts_service.set_person_enabled(db, user_id, True, actor.directory_object_id, request.state.request_id)


@router.post("/{user_id}/accounts/{account_id}/enabled", response_model=IdentityAccountResponse)
async def set_one(user_id: UUID, account_id: UUID, data: AccountEnabledRequest, request: Request, actor: AuthenticatedUser = Depends(accounts_manage), db: AsyncSession = Depends(get_db)):
    if data.enabled:
        await leaver_followup.guard_enable(db, user_id)
    account = await accounts_service.set_account_enabled(db, account_id, data.enabled, actor.directory_object_id, request.state.request_id)
    user = await _get_user(db, account.user_id)
    return next(r for r in await accounts_service._account_responses(db, user) if r.id == account.id)
