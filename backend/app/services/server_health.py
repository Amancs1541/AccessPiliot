from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import engine
from app.models import AccessAssignment, AuditLog, IdentityProvider, OnboardingImport, SyncError, SyncRun
from app.schemas.server_health import DatabaseHealth, EndpointHealth, LiveEvent, RequestVolumeChart, ServerHealthResponse, ServiceStatusCard, WorkerStatus, WorkflowStatus
from app.services import server_health_state
from app.services.audit_read import list_system_generated_audit_logs

# V2: every field below is computed from a real, already-existing source (see the design discussion this was
# scoped from) — nothing here is fabricated. Two things are deliberately kept honest rather than faked, because
# nothing in this app tracks them at all: "last backup" (no backup system exists) and any literal browser
# "session count" (this is a stateless JWT/MSAL app with no server-side session store — "users with active
# access" is used instead, a real, differently-named metric).

_WORKER_NAMES = ["Entra sync worker", "Access expiry sweep", "Scheduled activation worker", "SoD exception expiry worker"]
_WORKER_POLL_SECONDS = 60  # true for all four registered workers today — see workers/*.py's own POLL_INTERVAL_SECONDS


def _format_ago(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s ago"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    return f"{int(seconds // 86400)}d ago"


def _format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m {int(seconds % 60)}s"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h {int((seconds % 3600) // 60)}m"
    return f"{int(seconds // 86400)}d {int((seconds % 86400) // 3600)}h"


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(len(ordered) * pct))
    return ordered[index]


def _api_card(now: datetime) -> ServiceStatusCard:
    recent = [r for r in server_health_state.REQUEST_LOG if r["at"] >= now - timedelta(minutes=15)]
    durations = [r["duration_ms"] for r in recent]
    errors = sum(1 for r in recent if r["status_code"] >= 500)
    error_rate = errors / len(recent) if recent else 0.0
    variant = "crit" if error_rate > 0.1 else "warn" if errors else "ok"
    status = "DEGRADED" if variant == "crit" else "ERRORS" if variant == "warn" else "HEALTHY"
    uptime = (now - server_health_state.PROCESS_STARTED_AT).total_seconds()
    p95 = _percentile(durations, 0.95)
    return ServiceStatusCard(name="API", tag="FastAPI · uvicorn", status=status, variant=variant, metric=_format_duration(uptime), metric_unit="", metric_label="Process uptime", foot_label="p95 latency (15m)", foot_value=f"{p95:.0f} ms" if durations else "no traffic yet")


def _pool_stats() -> tuple[int, int]:
    """(connections in use, max possible) — real when the engine uses a real connection pool (QueuePool /
    AsyncAdaptedQueuePool, what production's real Postgres connection genuinely uses). SQLite deployments (and
    this test suite) use NullPool, which doesn't pool at all and exposes none of these methods — (0, 0) there is
    itself the honest answer, not a fallback fake."""
    pool = engine.pool
    if not hasattr(pool, "checkedout"):
        return 0, 0
    return pool.checkedout(), pool.size() + getattr(pool, "_max_overflow", 10)


def _database_card() -> ServiceStatusCard:
    pool_used, pool_max = _pool_stats()
    durations = list(server_health_state.QUERY_DURATIONS)
    avg_query_ms = sum(durations) / len(durations) if durations else 0.0
    variant = "warn" if pool_max and pool_used / pool_max > 0.8 else "ok"
    metric_unit = f"/{pool_max}" if pool_max else " (unpooled)"
    return ServiceStatusCard(name="Database", tag="PostgreSQL", status="BUSY" if variant == "warn" else "HEALTHY", variant=variant, metric=str(pool_used), metric_unit=metric_unit, metric_label="Connections in use", foot_label="avg query", foot_value=f"{avg_query_ms:.1f} ms" if durations else "no queries yet")


async def _graph_card(session: AsyncSession, now: datetime) -> ServiceStatusCard:
    latest_run = (await session.execute(select(SyncRun).order_by(SyncRun.started_at.desc()).limit(1))).scalars().first()
    latest_success = (await session.execute(select(SyncRun).where(SyncRun.status == "COMPLETED").order_by(SyncRun.completed_at.desc()).limit(1))).scalars().first()
    since = now - timedelta(hours=1)
    throttle_count = (await session.execute(select(func.count()).select_from(SyncError).where(SyncError.error_code == "GRAPH_THROTTLED", SyncError.created_at >= since))).scalar_one()

    if latest_run is None:
        variant, status, last_success_text = "mock", "NO SYNC YET", "never"
    elif throttle_count > 0:
        variant, status = "warn", "THROTTLED"
    else:
        variant, status = "ok", "HEALTHY"
    if latest_run is not None:
        if latest_success is not None and latest_success.completed_at is not None:
            completed_at = latest_success.completed_at if latest_success.completed_at.tzinfo else latest_success.completed_at.replace(tzinfo=timezone.utc)
            last_success_text = _format_ago((now - completed_at).total_seconds())
        else:
            last_success_text = "never"
    return ServiceStatusCard(name="Graph connector", tag="Microsoft Entra ID", status=status, variant=variant, metric=str(throttle_count), metric_unit=" throttled (1h)", metric_label="429s from Graph API", foot_label="last success", foot_value=last_success_text)


def _workers_card() -> ServiceStatusCard:
    alive = sum(1 for task in server_health_state.WORKER_TASKS.values() if not task.done())
    total = len(server_health_state.WORKER_TASKS)
    if total == 0:
        variant, status = "mock", "NOT RUNNING"
    elif alive < total:
        variant, status = "crit", "DEGRADED"
    else:
        variant, status = "ok", "RUNNING"
    return ServiceStatusCard(name="Background workers", tag="asyncio · in-process", status=status, variant=variant, metric=str(alive), metric_unit=f"/{total}" if total else "", metric_label="Workers alive", foot_label="poll interval", foot_value=f"{_WORKER_POLL_SECONDS}s" if total else "n/a")


async def _auth_card(session: AsyncSession) -> ServiceStatusCard:
    active_users = (await session.execute(select(func.count(func.distinct(AccessAssignment.user_id))).where(AccessAssignment.status == "ACTIVE"))).scalar_one()
    connected_providers = (await session.execute(select(func.count()).select_from(IdentityProvider).where(IdentityProvider.status == "CONNECTED"))).scalar_one()
    variant = "ok" if connected_providers else "warn"
    return ServiceStatusCard(name="Auth / Portal", tag="MSAL · portal IDP", status="HEALTHY" if variant == "ok" else "NO PROVIDER", variant=variant, metric=str(active_users), metric_unit="", metric_label="Users with active access", foot_label="connected providers", foot_value=str(connected_providers))


async def _providers_card(session: AsyncSession) -> ServiceStatusCard:
    providers = list((await session.scalars(select(IdentityProvider))).all())
    types = sorted({provider.type for provider in providers})
    variant = "ok" if providers else "mock"
    return ServiceStatusCard(name="Identity providers", tag="Configured connectors", status="CONFIGURED" if providers else "NONE CONFIGURED", variant=variant, metric=str(len(providers)), metric_unit="", metric_label="Providers configured", foot_label="types", foot_value=", ".join(types) if types else "none")


def _request_chart(now: datetime) -> RequestVolumeChart:
    log = list(server_health_state.REQUEST_LOG)
    bars: list[int] = []
    latency_line: list[int] = []
    for offset in range(59, -1, -1):
        bucket_start = now - timedelta(minutes=offset + 1)
        bucket_end = now - timedelta(minutes=offset)
        entries = [r for r in log if bucket_start <= r["at"] < bucket_end]
        bars.append(len(entries))
        latency_line.append(round(sum(r["duration_ms"] for r in entries) / len(entries)) if entries else 0)
    avg_req_per_min = round(sum(bars) / len(bars)) if bars else 0
    return RequestVolumeChart(avg_req_per_min=avg_req_per_min, bars=bars, latency_line=latency_line)


def _workers_list() -> list[WorkerStatus]:
    workers = []
    for name in _WORKER_NAMES:
        last_run, ticks = server_health_state.worker_status(name)
        workers.append(WorkerStatus(name=name, cadence=f"every {_WORKER_POLL_SECONDS} sec", last_run=last_run or "not yet run", ticks=ticks))
    return workers


# Every real automated pipeline in the app, background loop or on-demand — description text is a plain, accurate
# summary of what the code actually does, not aspirational copy. "24h"/"7d" activity counts are real audit/table
# queries, never estimates.
_WORKFLOW_DESCRIPTIONS = {
    "Entra sync worker": "Polls each configured provider's own schedule and pulls users, groups, roles, and applications from Microsoft Graph.",
    "Access expiry sweep": "Ends real access whose time-bound expiration has passed, and expires ELIGIBLE rows never activated by their deadline.",
    "Scheduled activation worker": "Grants real access for future-dated bypass assignments once their start time arrives.",
    "SoD exception expiry worker": "Ends the specific access an SoD exception was covering once that exception's own expiry passes.",
}


def _background_workflow_variant(name: str, last_run: str | None, ticks: list[str]) -> tuple[str, str]:
    if name not in server_health_state.WORKER_TASKS:
        return "mock", "NOT RUNNING"
    task = server_health_state.WORKER_TASKS[name]
    if task.done():
        return "crit", "STOPPED"
    if ticks and ticks[-1] == "crit":
        return "warn", "LAST TICK FAILED"
    if last_run is None:
        return "warn", "AWAITING FIRST TICK"
    return "ok", "RUNNING"


async def _background_workflows(now: datetime, session: AsyncSession) -> list[WorkflowStatus]:
    since = now - timedelta(hours=24)
    action_by_worker = {
        "Entra sync worker": "SYNC_COMPLETED",
        "Access expiry sweep": "ASSIGNMENT_EXPIRED",
        "Scheduled activation worker": "ASSIGNMENT_ACTIVATED",
        "SoD exception expiry worker": "ASSIGNMENT_REVOKED",
    }
    workflows = []
    for name in _WORKER_NAMES:
        last_run, ticks = server_health_state.worker_status(name)
        variant, status = _background_workflow_variant(name, last_run, ticks)
        # actor_user_id.is_(None) is what makes this genuinely "this worker's own doing" — see
        # list_system_generated_audit_logs's docstring for why that's a real, not inferred, signal.
        count = (await session.execute(select(func.count()).select_from(AuditLog).where(AuditLog.action == action_by_worker[name], AuditLog.actor_user_id.is_(None), AuditLog.timestamp >= since))).scalar_one()
        workflows.append(WorkflowStatus(name=name, description=_WORKFLOW_DESCRIPTIONS[name], kind="background", cadence=f"every {_WORKER_POLL_SECONDS} sec", status=status, variant=variant, last_run=last_run or "not yet run", ticks=ticks, recent_activity=f"{count} in the last 24h"))
    return workflows


async def _onboarding_workflow(session: AsyncSession, now: datetime) -> WorkflowStatus:
    latest = (await session.execute(select(OnboardingImport).order_by(OnboardingImport.created_at.desc()).limit(1))).scalars().first()
    since = now - timedelta(days=7)
    committed_count = (await session.execute(select(func.count()).select_from(OnboardingImport).where(OnboardingImport.status == "COMMITTED", OnboardingImport.created_at >= since))).scalar_one()
    if latest is None:
        return WorkflowStatus(name="Onboarding CSV import", description="Validates and commits an HR/CSV roster upload — creates, updates, or disables local identities.", kind="on-demand", cadence="on demand", status="NEVER RUN", variant="mock", last_run="never", ticks=[], recent_activity="0 committed in the last 7 days")
    last_run = _format_ago((now - (latest.created_at if latest.created_at.tzinfo else latest.created_at.replace(tzinfo=timezone.utc))).total_seconds())
    variant, status = {"COMMITTED": ("ok", "HEALTHY"), "VALIDATED": ("warn", "AWAITING COMMIT"), "VALIDATING": ("warn", "IN PROGRESS"), "VALIDATION_FAILED": ("warn", "LAST IMPORT FAILED VALIDATION")}.get(latest.status, ("warn", latest.status))
    return WorkflowStatus(name="Onboarding CSV import", description="Validates and commits an HR/CSV roster upload — creates, updates, or disables local identities.", kind="on-demand", cadence="on demand", status=status, variant=variant, last_run=last_run, ticks=[], recent_activity=f"{committed_count} committed in the last 7 days")


async def _package_assignment_workflow(session: AsyncSession, now: datetime) -> WorkflowStatus:
    since = now - timedelta(hours=24)
    entries = list((await session.scalars(select(AuditLog).where(AuditLog.action == "PACKAGE_ASSIGNED", AuditLog.timestamp >= since))).all())
    total_created = sum((entry.metadata_json or {}).get("created", 0) for entry in entries)
    total_failed = sum((entry.metadata_json or {}).get("failed", 0) for entry in entries)
    if not entries:
        return WorkflowStatus(name="Access Package assignment", description="Fans a package's items out into individual assignments for a user or every member of a group.", kind="on-demand", cadence="on demand", status="NO ACTIVITY", variant="mock", last_run="none in the last 24h", ticks=[], recent_activity="0 in the last 24h")
    latest = max(entries, key=lambda entry: entry.timestamp)
    last_run = _format_ago((now - (latest.timestamp if latest.timestamp.tzinfo else latest.timestamp.replace(tzinfo=timezone.utc))).total_seconds())
    variant, status = ("warn", "SOME ITEMS FAILED") if total_failed else ("ok", "HEALTHY")
    return WorkflowStatus(name="Access Package assignment", description="Fans a package's items out into individual assignments for a user or every member of a group.", kind="on-demand", cadence="on demand", status=status, variant=variant, last_run=last_run, ticks=[], recent_activity=f"{total_created} succeeded, {total_failed} failed (24h)")


async def _workflows_list(session: AsyncSession, now: datetime) -> list[WorkflowStatus]:
    return [*(await _background_workflows(now, session)), await _onboarding_workflow(session, now), await _package_assignment_workflow(session, now)]


def _endpoints_list(now: datetime) -> list[EndpointHealth]:
    window_minutes = 15
    window_start = now - timedelta(minutes=window_minutes)
    grouped: dict[tuple[str, str], list[dict]] = {}
    for entry in server_health_state.REQUEST_LOG:
        if entry["at"] < window_start:
            continue
        grouped.setdefault((entry["method"], entry["path"]), []).append(entry)

    endpoints = []
    for (method, path), entries in grouped.items():
        durations = [entry["duration_ms"] for entry in entries]
        error_count = sum(1 for entry in entries if entry["status_code"] >= 400)
        error_rate = error_count / len(entries) * 100
        avg = sum(durations) / len(durations)
        p95 = _percentile(durations, 0.95)
        variant = "error" if error_rate > 10 else "warn" if avg > 250 else "info"
        latest_status = entries[-1]["status_code"]
        status_label = "SLOW" if variant == "warn" else str(latest_status) if latest_status >= 400 else "200 OK"
        endpoints.append(EndpointHealth(method=method, path=path, status=status_label, status_variant=variant, avg=f"{avg:.0f} ms", p95=f"{p95:.0f} ms", req_per_min=round(len(entries) / window_minutes, 1), error_rate=f"{error_rate:.2f}%"))
    endpoints.sort(key=lambda item: item.req_per_min, reverse=True)
    return endpoints[:25]


async def _database_section(session: AsyncSession) -> DatabaseHealth:
    pool_used, pool_max = _pool_stats()
    durations = list(server_health_state.QUERY_DURATIONS)
    audit_log_rows = (await session.execute(select(func.count()).select_from(AuditLog))).scalar_one()
    return DatabaseHealth(
        pool_used=pool_used, pool_max=pool_max,
        avg_query_ms=round(sum(durations) / len(durations), 1) if durations else 0.0,
        slowest_query_ms=round(max(durations), 1) if durations else 0.0,
        audit_log_rows=audit_log_rows,
        # Both real, honest facts about this deployment — never fabricated: a single-node Postgres instance has
        # no replica to lag, and no backup system has been built or scheduled anywhere in this app.
        replication_lag="n/a — single node", last_backup="Not configured",
    )


async def _events_list(session: AsyncSession) -> list[LiveEvent]:
    """The server's own activity — what it's actually doing, not the ordinary business audit trail (a plain
    assignment grant, an approval) which already has its own home on the regular Audit Logs page. Every
    record_audit() call the sync/expiration/activation/SoD-expiry workers make omits actor_user_id entirely
    (see list_system_generated_audit_logs), which is what "server-generated" really means here — not a guess."""
    entries = await list_system_generated_audit_logs(session, limit=15)
    events = []
    for entry, hydrated in entries:
        target = hydrated["target_user_display_name"]
        message = entry.action.replace("_", " ").title() + (f" — {target}" if target else "")
        events.append(LiveEvent(level="error" if entry.result == "FAILURE" else "info", time=entry.timestamp.strftime("%H:%M:%S"), message=message))
    return events


async def get_live_server_health(session: AsyncSession) -> ServerHealthResponse:
    now = datetime.now(timezone.utc)
    # _graph_card runs first (and is the first real DB query this request's session makes) so _database_card's
    # own connection-pool reading reflects this very request's connection actually being checked out, instead of
    # reading the pool before this session has run a single query — AsyncSession only acquires a physical
    # connection lazily, on its first execute(), so reading the pool any earlier always saw a guaranteed 0.
    graph_card = await _graph_card(session, now)
    services = [_api_card(now), _database_card(), graph_card, _workers_card(), await _auth_card(session), await _providers_card(session)]
    overall_status = "degraded" if any(card.variant == "crit" for card in services) else "operational"
    return ServerHealthResponse(
        is_mock=False,
        overall_status=overall_status,
        services=services,
        request_chart=_request_chart(now),
        workers=_workers_list(),
        workflows=await _workflows_list(session, now),
        endpoints=_endpoints_list(now),
        database=await _database_section(session),
        events=await _events_list(session),
    )
