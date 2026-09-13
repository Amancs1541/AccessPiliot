from __future__ import annotations

from pydantic import BaseModel


class ServiceStatusCard(BaseModel):
    name: str
    tag: str
    status: str
    variant: str  # "ok" | "warn" | "crit" | "mock"
    metric: str
    metric_unit: str
    metric_label: str
    foot_label: str
    foot_value: str


class RequestVolumeChart(BaseModel):
    avg_req_per_min: int
    bars: list[int]
    latency_line: list[int]


class WorkerStatus(BaseModel):
    name: str
    cadence: str
    last_run: str
    ticks: list[str]  # "ok" | "warn" | "crit" | "idle"


class WorkflowStatus(BaseModel):
    # Broader than WorkerStatus (kept as-is, still used internally by the Troubleshooting drill-down): every
    # real automated pipeline in the app, not just the 4 background asyncio loops — on-demand ones (Onboarding
    # import, Package assignment) have no ticks (they don't poll), just a real last-run outcome.
    name: str
    description: str
    kind: str  # "background" | "on-demand"
    cadence: str
    status: str
    variant: str  # "ok" | "warn" | "crit" | "mock"
    last_run: str
    ticks: list[str]
    recent_activity: str


class EndpointHealth(BaseModel):
    method: str
    path: str
    status: str
    status_variant: str  # "info" | "warn" | "error" | "mock"
    avg: str
    p95: str
    req_per_min: float
    error_rate: str


class DatabaseHealth(BaseModel):
    pool_used: int
    pool_max: int
    avg_query_ms: float
    slowest_query_ms: float
    audit_log_rows: int
    replication_lag: str
    last_backup: str


class LiveEvent(BaseModel):
    level: str  # "info" | "warn" | "error" | "mock"
    time: str
    message: str


class ServerHealthResponse(BaseModel):
    # is_mock flags every field below as placeholder data — see services/server_health.py for exactly which
    # pieces are real-today-and-buildable-for-free vs. which need new instrumentation, per the design discussion
    # this was scoped from. The frontend renders this identically either way; only the source of truth changes.
    is_mock: bool
    overall_status: str
    services: list[ServiceStatusCard]
    request_chart: RequestVolumeChart
    workers: list[WorkerStatus]
    workflows: list[WorkflowStatus]
    endpoints: list[EndpointHealth]
    database: DatabaseHealth
    events: list[LiveEvent]
