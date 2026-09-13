from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class SocFieldInfo(BaseModel):
    field: str
    label: str


class SocSourceInfo(BaseModel):
    source: str
    label: str
    fields: list[SocFieldInfo]


class SocFieldsResponse(BaseModel):
    """Powers the "Add widget" builder — every source/field a SoCAdmin can build a custom graph from, plus the
    fixed set of built-in cards/panels available to re-add if removed. A single source of truth so the frontend
    never hardcodes a field list that could drift from what the backend actually knows how to compute."""
    sources: list[SocSourceInfo]
    builtin_widgets: list[SocFieldInfo]


class SocWidgetFilter(BaseModel):
    field: str
    value: str


class SocWidgetDefinition(BaseModel):
    """A single dashboard widget — either a fixed built-in panel (source="builtin", builtin_id set) or a
    self-service custom graph the SoCAdmin built themselves (source is a real data source name, group_by/filters
    describe how it's computed — see services/soc.py's FIELD_DEFS for what's actually valid)."""
    id: str
    title: str
    kind: str = Field(pattern="^(card|timeseries|bar|list)$")
    source: str
    builtin_id: Optional[str] = None
    group_by: Optional[str] = None
    filters: list[SocWidgetFilter] = []
    visible: bool = True
    order: int


class SocLayoutResponse(BaseModel):
    widgets: list[SocWidgetDefinition]


class SocLayoutUpdateRequest(BaseModel):
    widgets: list[SocWidgetDefinition]


class SocWidgetDataResult(BaseModel):
    value: Optional[int] = None
    series: Optional[list[dict]] = None
    rows: Optional[list[dict]] = None


class SocWidgetDataRequest(BaseModel):
    widgets: list[SocWidgetDefinition]


class SocWidgetDataResponse(BaseModel):
    results: dict[str, SocWidgetDataResult]


class SocWidgetDrilldownRequest(BaseModel):
    widget: SocWidgetDefinition
    # Optional: a timeseries chart point drilldown needs the clicked day; a card or the "Open SoD violations"
    # card needs neither. Only present for a timeseries point click.
    date: Optional[str] = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    # Optional: which bar/list entry was clicked (its group label) — only present for a bar/list widget.
    group_value: Optional[str] = None


class SocWidgetDrilldownResponse(BaseModel):
    rows: list[dict]
