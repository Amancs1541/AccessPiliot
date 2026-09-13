from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AccessPilotError
from app.db.session import get_db
from app.schemas.soc import SocFieldsResponse, SocLayoutResponse, SocLayoutUpdateRequest, SocWidgetDataRequest, SocWidgetDataResponse, SocWidgetDrilldownRequest, SocWidgetDrilldownResponse
from app.security.auth import AuthenticatedUser, require_permission
from app.services import soc as soc_service
from app.services.assignments import _resolve_internal_user_id

router = APIRouter(prefix="/soc", tags=["soc"])
soc_read = require_permission("SOC_READ")


@router.get("/fields", response_model=SocFieldsResponse)
async def get_fields(_: AuthenticatedUser = Depends(soc_read)):
    """Static, no DB access needed — the exact source/field list the "Add widget" builder offers, and the
    built-in panels available to re-add. See services/soc.py's FIELD_DEFS for what backs this."""
    return soc_service.get_available_fields()


@router.get("/layout", response_model=SocLayoutResponse)
async def get_layout(actor: AuthenticatedUser = Depends(soc_read), db: AsyncSession = Depends(get_db)):
    """Per-viewer, not shared — a Break-Glass-style session (subject never matches a real directory user) simply
    always sees the hardcoded default layout, never an error, since there's nothing wrong with viewing the
    dashboard un-customized."""
    actor_id = await _resolve_internal_user_id(db, actor.directory_object_id)
    if actor_id is None:
        return SocLayoutResponse(widgets=soc_service.default_widgets())
    return await soc_service.get_soc_layout(db, actor_id)


@router.put("/layout", response_model=SocLayoutResponse)
async def put_layout(data: SocLayoutUpdateRequest, actor: AuthenticatedUser = Depends(soc_read), db: AsyncSession = Depends(get_db)):
    actor_id = await _resolve_internal_user_id(db, actor.directory_object_id)
    if actor_id is None:
        raise AccessPilotError("USER_NOT_FOUND", "Your directory record could not be found, so your dashboard layout can't be saved.", 404)
    return await soc_service.save_soc_layout(db, actor_id, data.widgets)


@router.post("/widget-data", response_model=SocWidgetDataResponse)
async def post_widget_data(data: SocWidgetDataRequest, _: AuthenticatedUser = Depends(soc_read), db: AsyncSession = Depends(get_db)):
    """Computes every widget in the given list in one call — the dashboard sends its current (possibly not-yet-
    saved) layout here to render live data, including while a SoCAdmin is still previewing a graph they're
    building before saving it to their layout at all."""
    results = {widget.id: await soc_service.compute_widget_data(db, widget) for widget in data.widgets}
    return SocWidgetDataResponse(results=results)


@router.post("/widget-drilldown", response_model=SocWidgetDrilldownResponse)
async def post_widget_drilldown(data: SocWidgetDrilldownRequest, _: AuthenticatedUser = Depends(soc_read), db: AsyncSession = Depends(get_db)):
    """The rows behind one clicked point on a timeseries chart — see services/soc.py's compute_widget_drilldown
    for exactly what's returned per source, and why only timeseries widgets support this at all."""
    return SocWidgetDrilldownResponse(rows=await soc_service.compute_widget_drilldown(db, data.widget, data.date, data.group_value))
