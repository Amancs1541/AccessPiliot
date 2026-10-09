from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.entitlement_catalog import EntitlementCatalogEntryResponse, EntitlementCatalogEntryUpdate
from app.security.auth import AuthenticatedUser, require_permission
from app.services import entitlement_catalog as entitlement_catalog_service

router = APIRouter(prefix="/entitlement-catalog", tags=["entitlement-catalog"])
catalog_read = require_permission("ENTITLEMENT_CATALOG_READ")
catalog_manage = require_permission("ENTITLEMENT_CATALOG_MANAGE")


@router.get("", response_model=list[EntitlementCatalogEntryResponse])
async def list_entitlement_catalog(_: AuthenticatedUser = Depends(catalog_read), db: AsyncSession = Depends(get_db)):
    return await entitlement_catalog_service.list_catalog(db)


@router.patch("/{entry_id}", response_model=EntitlementCatalogEntryResponse)
async def update_entitlement_catalog_entry(entry_id: UUID, data: EntitlementCatalogEntryUpdate, request: Request, _: AuthenticatedUser = Depends(catalog_manage), db: AsyncSession = Depends(get_db)):
    return await entitlement_catalog_service.update_catalog_entry(db, entry_id, data, request.state.request_id)
