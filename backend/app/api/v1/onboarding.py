from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import request_id as get_request_id
from app.db.session import get_db
from app.schemas.onboarding import OnboardingCsvUpload, OnboardingImportRecordResponse, OnboardingImportResponse
from app.security.auth import AuthenticatedUser, require_permission
from app.services import onboarding as onboarding_service

router = APIRouter(prefix="/onboarding", tags=["onboarding"])
onboarding_read = require_permission("ONBOARDING_READ")
onboarding_manage = require_permission("ONBOARDING_MANAGE")


@router.post("/csv", response_model=OnboardingImportResponse, status_code=201)
async def upload_csv(data: OnboardingCsvUpload, request: Request, actor: AuthenticatedUser = Depends(onboarding_manage), db: AsyncSession = Depends(get_db)):
    return await onboarding_service.parse_and_validate_csv(db, data.filename, data.content, actor.directory_object_id, get_request_id(request))


@router.get("/imports", response_model=list[OnboardingImportResponse])
async def list_imports(_: AuthenticatedUser = Depends(onboarding_read), db: AsyncSession = Depends(get_db)):
    return await onboarding_service.list_imports(db)


@router.get("/imports/{import_id}", response_model=OnboardingImportResponse)
async def get_import(import_id: UUID, _: AuthenticatedUser = Depends(onboarding_read), db: AsyncSession = Depends(get_db)):
    return await onboarding_service.get_import(db, import_id)


@router.get("/imports/{import_id}/preview", response_model=list[OnboardingImportRecordResponse])
async def preview_import(import_id: UUID, _: AuthenticatedUser = Depends(onboarding_read), db: AsyncSession = Depends(get_db)):
    return await onboarding_service.get_import_preview(db, import_id)


@router.post("/imports/{import_id}/commit", response_model=OnboardingImportResponse)
async def commit_import(import_id: UUID, request: Request, actor: AuthenticatedUser = Depends(onboarding_manage), db: AsyncSession = Depends(get_db)):
    return await onboarding_service.commit_import(db, import_id, actor.directory_object_id, get_request_id(request))
