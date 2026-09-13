from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class Incident(BaseModel):
    title: str
    severity: str  # "crit" | "warn"
    started_label: str  # e.g. "22m ago" when a real anchor point exists, else "currently observed"
    affects: list[str]


class RootCause(BaseModel):
    rank: int
    title: str
    detail: str
    confidence: str  # "high" | "medium" | "low"


class ChecklistItem(BaseModel):
    label: str
    detail: str
    done: bool  # true only when genuinely verified from real data — never a guess


class ErrorTrendPoint(BaseModel):
    label: str
    count: int


class DependencyNode(BaseModel):
    name: str
    status: str
    variant: str  # "ok" | "warn" | "crit"


class ProviderDetail(BaseModel):
    name: str
    type: str
    tag: str  # "primary" | "secondary"
    status: str
    variant: str  # "ok" | "warn" | "crit"
    detail: str


class TroubleshootLog(BaseModel):
    level: str
    time: str
    message: str
    service: str  # which dependency-chain node this log line is attributed to, for the filter pills


class TroubleshootingResponse(BaseModel):
    incident: Optional[Incident] = None
    root_causes: list[RootCause]
    checklist: list[ChecklistItem]
    error_trend: list[ErrorTrendPoint]
    dependency_chain: list[DependencyNode]
    providers: list[ProviderDetail]
    logs: list[TroubleshootLog]
    # The real, already-existing provider a "Retry connection" / "Run manual sync" quick action should target —
    # null when there's genuinely no provider configured at all yet.
    primary_provider_id: Optional[str] = None
