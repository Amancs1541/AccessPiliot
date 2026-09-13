from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import IdentityProvider, SyncError
from app.schemas.server_health import ServerHealthResponse
from app.schemas.troubleshooting import ChecklistItem, DependencyNode, ErrorTrendPoint, Incident, ProviderDetail, RootCause, TroubleshootingResponse, TroubleshootLog
from app.services.audit_read import list_system_generated_audit_logs
from app.services.server_health import _format_ago, get_live_server_health

# Diagnoses one real thing at a time — whichever service card is currently worst. Every number quoted below is
# read directly off the same live signals System Health's own cards already compute (or a fresh, real query
# alongside them); nothing here is a statistical/ML root-cause engine, the same honest scope boundary the SOC
# dashboard's "high-signal events" already drew for anomaly detection — this is curated, explainable, rule-based
# diagnosis, not a guess dressed up as one.
_SEVERITY_RANK = {"crit": 0, "warn": 1, "mock": 2, "ok": 3}


def _card(health: ServerHealthResponse, name: str):
    return next(card for card in health.services if card.name == name)


async def _graph_diagnosis(session: AsyncSession, health: ServerHealthResponse, now: datetime) -> tuple[Incident, list[RootCause], list[ChecklistItem]]:
    graph = _card(health, "Graph connector")
    window = now - timedelta(hours=1)
    recent_errors = list((await session.scalars(select(SyncError).where(SyncError.created_at >= window).order_by(SyncError.created_at.asc()))).all())
    throttle_errors = [error for error in recent_errors if error.error_code == "GRAPH_THROTTLED"]
    started_label = "currently observed"
    if throttle_errors:
        first = throttle_errors[0].created_at if throttle_errors[0].created_at.tzinfo else throttle_errors[0].created_at.replace(tzinfo=timezone.utc)
        started_label = f"{_format_ago((now - first).total_seconds())} (first throttled call)"
    incident = Incident(title=f"Graph connector {graph.status.lower()} — role & app-role sync affected", severity=graph.variant, started_label=started_label, affects=["SoD detective scan (stale group/role data)", "Role & application-role sync", "Onboarding role-based assignment"])

    root_causes = []
    if throttle_errors:
        root_causes.append(RootCause(rank=1, title="Graph API throttling (429s)", detail=f"{len(throttle_errors)} throttled call(s) to Microsoft Graph in the last hour, most recently {recent_errors[-1].error_code} on {recent_errors[-1].resource_type}.", confidence="high"))
    else:
        root_causes.append(RootCause(rank=1, title="No throttling detected", detail="No GRAPH_THROTTLED errors in the last hour — if sync is still failing, check credentials or connectivity next.", confidence="low"))
    other_errors = [error for error in recent_errors if error.error_code != "GRAPH_THROTTLED"]
    if other_errors:
        codes = sorted({error.error_code for error in other_errors})
        root_causes.append(RootCause(rank=2, title="Other Graph errors present", detail=f"{len(other_errors)} non-throttling error(s) in the last hour: {', '.join(codes)}.", confidence="medium"))
    root_causes.append(RootCause(rank=len(root_causes) + 1, title="Expired or near-expiry client secret", detail="Would produce 401s specifically, not 429s or the codes above — a quick check to rule out regardless.", confidence="low"))

    checklist = [
        ChecklistItem(label="Confirm which calls are failing", detail=(f"{len(recent_errors)} Graph error(s) in the last hour across: {', '.join(sorted({e.resource_type for e in recent_errors}))}." if recent_errors else "No failing Graph calls detected in the last hour."), done=bool(recent_errors)),
        ChecklistItem(label="Check when Graph last succeeded", detail=f"Last successful sync: {graph.foot_value}.", done=True),
        ChecklistItem(label="Inspect Graph request rate vs. the app registration's limit", detail="Not tracked in this app yet — check the Entra portal's app registration usage & quota page directly.", done=False),
        ChecklistItem(label="Reduce sync frequency or batch size", detail="Admin → Sync → schedule configuration.", done=False),
        ChecklistItem(label="Re-run a manual sync and confirm it succeeds", detail="Admin → Sync → \"Sync now\", or use Retry connection below.", done=False),
    ]
    return incident, root_causes, checklist


async def _database_diagnosis(health: ServerHealthResponse) -> tuple[Incident, list[RootCause], list[ChecklistItem]]:
    database = health.database
    incident = Incident(title="Database connection pool under pressure", severity=_card(health, "Database").variant, started_label="currently observed", affects=["All database-backed reads and writes"])
    root_causes = [
        RootCause(rank=1, title="Connection pool near capacity", detail=f"{database.pool_used}/{database.pool_max} connections currently checked out.", confidence="high"),
        RootCause(rank=2, title="Slow queries holding connections longer than usual", detail=f"Slowest recorded query: {database.slowest_query_ms} ms (avg {database.avg_query_ms} ms).", confidence="medium" if database.slowest_query_ms > 200 else "low"),
    ]
    checklist = [
        ChecklistItem(label="Check current pool usage", detail=f"{database.pool_used}/{database.pool_max} in use.", done=True),
        ChecklistItem(label="Check for a slow or stuck query", detail=f"Slowest recently recorded: {database.slowest_query_ms} ms.", done=True),
        ChecklistItem(label="Look for a connection leak (a session never closed)", detail="Review any recent code touching database sessions directly rather than via the get_db()/AsyncSessionLocal dependency.", done=False),
        ChecklistItem(label="Consider raising pool_size/max_overflow", detail="backend/app/db/session.py — create_async_engine(...).", done=False),
    ]
    return incident, root_causes, checklist


def _workers_diagnosis(health: ServerHealthResponse) -> tuple[Incident, list[RootCause], list[ChecklistItem]]:
    workers_card = _card(health, "Background workers")
    never_ticked = [worker.name for worker in health.workers if "ago" not in worker.last_run]
    stopped = [worker.name for worker in health.workers if worker.ticks and worker.ticks[-1] == "crit"]
    affected_names = stopped or never_ticked or [worker.name for worker in health.workers] or ["all background processing"]
    incident = Incident(title="One or more background workers are not running", severity=workers_card.variant, started_label="currently observed", affects=[f"{name} — its own responsibilities are paused" for name in affected_names])
    root_causes = [RootCause(rank=1, title="Worker task stopped or was never started", detail=f"{workers_card.metric}{workers_card.metric_unit} workers currently alive.", confidence="high")]
    if stopped:
        root_causes.append(RootCause(rank=2, title="Last tick for this worker raised an exception", detail=f"{', '.join(stopped)} recorded a failed tick most recently.", confidence="high"))
    checklist = [
        ChecklistItem(label="Identify which worker(s) stopped", detail=", ".join(stopped or never_ticked) or "None currently registered in this process.", done=bool(stopped or never_ticked)),
        ChecklistItem(label="Check the backend log for that worker's last exception", detail="Search the backend log for the worker's own logger name (e.g. accesspilot.scheduler, accesspilot.expiration).", done=False),
        ChecklistItem(label="Restart the backend process", detail="Workers restart automatically with the process — see the dev environment notes for the restart command.", done=False),
    ]
    return incident, root_causes, checklist


def _api_diagnosis(health: ServerHealthResponse) -> tuple[Incident, list[RootCause], list[ChecklistItem]]:
    api_card = _card(health, "API")
    incident = Incident(title="Elevated API error rate", severity=api_card.variant, started_label="currently observed (last 15 min)", affects=["End users hitting errors on recent requests"])
    error_endpoints = [endpoint for endpoint in health.endpoints if endpoint.status_variant == "error"]
    root_causes = [RootCause(rank=1, title="One or more endpoints returning 5xx", detail=(", ".join(f"{e.method} {e.path} ({e.error_rate})" for e in error_endpoints) if error_endpoints else "No specific endpoint identified yet — check the endpoint table below."), confidence="high" if error_endpoints else "low")]
    checklist = [
        ChecklistItem(label="Identify which endpoint(s) are erroring", detail=(", ".join(e.path for e in error_endpoints) if error_endpoints else "None identified yet."), done=bool(error_endpoints)),
        ChecklistItem(label="Check the backend log around this time for a traceback", detail="The backend log records a full traceback for every unhandled exception.", done=False),
    ]
    return incident, root_causes, checklist


def _error_trend(errors: list[SyncError], now: datetime, minutes: int = 30, buckets: int = 12) -> list[ErrorTrendPoint]:
    bucket_minutes = minutes / buckets
    points = []
    for index in range(buckets):
        offset_start = minutes - (index + 1) * bucket_minutes
        offset_end = minutes - index * bucket_minutes
        start = now - timedelta(minutes=offset_end)
        end = now - timedelta(minutes=offset_start)
        count = sum(1 for error in errors if start <= (error.created_at if error.created_at.tzinfo else error.created_at.replace(tzinfo=timezone.utc)) < end)
        points.append(ErrorTrendPoint(label=f"-{int(offset_start)}m", count=count))
    return points


def _dependency_chain(health: ServerHealthResponse) -> list[DependencyNode]:
    api = _card(health, "API")
    graph = _card(health, "Graph connector")
    sync_worker = next((w for w in health.workers if w.name == "Entra sync worker"), None)
    if sync_worker is None or not sync_worker.ticks:
        sync_status, sync_variant = "not yet run", "warn"
    elif sync_worker.ticks[-1] == "crit":
        sync_status, sync_variant = "retrying", "warn"
    else:
        sync_status, sync_variant = "healthy", "ok"
    sod_status, sod_variant = ("stale data", "warn") if graph.variant != "ok" else ("healthy", "ok")
    return [
        DependencyNode(name="API", status="healthy" if api.variant == "ok" else api.status.lower(), variant=api.variant),
        DependencyNode(name="Graph connector", status="healthy" if graph.variant == "ok" else graph.status.lower(), variant=graph.variant),
        DependencyNode(name="Sync worker", status=sync_status, variant=sync_variant),
        # Inferred, not independently measured: the SoD detective scan reads synced group/role data, so a
        # degraded sync connector means that data is stale — a real architectural dependency, stated as such.
        DependencyNode(name="SoD detective scan", status=sod_status, variant=sod_variant),
    ]


async def _providers_list(session: AsyncSession, health: ServerHealthResponse) -> tuple[list[ProviderDetail], str | None]:
    providers = list((await session.scalars(select(IdentityProvider))).all())
    auth_card = _card(health, "Auth / Portal")
    details = []
    primary_id = None
    for provider in providers:
        is_login_idp = provider.type == "ENTRA"  # the only provider type actually used for interactive login today
        if is_login_idp and primary_id is None:
            primary_id = str(provider.id)
        # Real provider status values (services/provider_configuration.py): CONNECTED (a test-connection or sync
        # call has actually succeeded), CONFIGURED (credentials set but not yet verified, or changed since the
        # last successful test — not broken, just unverified), ERROR (the last attempt failed). Mapped honestly
        # rather than lumping "not CONNECTED" together as if it meant "not real" — a provider mid-setup is a
        # different, less urgent state than one that's actually failing.
        variant = "ok" if provider.status == "CONNECTED" else "crit" if provider.status == "ERROR" else "warn"
        detail = f"{auth_card.metric} users with active access" if is_login_idp else "data-source only — not used for interactive login"
        details.append(ProviderDetail(name=provider.name, type=provider.type, tag="primary" if is_login_idp else "secondary", status=provider.status, variant=variant, detail=detail))
    return details, primary_id


def _log_service(action: str) -> str:
    if "SYNC" in action or action.endswith("_SYNCED"):
        return "Sync worker"
    if action.startswith("PROVIDER"):
        return "Graph connector"
    if action.startswith("SOD"):
        return "SoD engine"
    return "Other"


async def build_troubleshooting_report(session: AsyncSession) -> TroubleshootingResponse:
    now = datetime.now(timezone.utc)
    health = await get_live_server_health(session)

    worst = min(health.services, key=lambda card: _SEVERITY_RANK[card.variant])
    incident: Incident | None = None
    root_causes: list[RootCause] = []
    checklist: list[ChecklistItem] = []
    if _SEVERITY_RANK[worst.variant] <= _SEVERITY_RANK["warn"]:
        if worst.name == "Graph connector":
            incident, root_causes, checklist = await _graph_diagnosis(session, health, now)
        elif worst.name == "Database":
            incident, root_causes, checklist = await _database_diagnosis(health)
        elif worst.name == "Background workers":
            incident, root_causes, checklist = _workers_diagnosis(health)
        elif worst.name == "API":
            incident, root_causes, checklist = _api_diagnosis(health)

    since = now - timedelta(minutes=30)
    recent_errors = list((await session.scalars(select(SyncError).where(SyncError.created_at >= since))).all())
    error_trend = _error_trend(recent_errors, now)
    dependency_chain = _dependency_chain(health)
    providers, primary_provider_id = await _providers_list(session, health)

    log_entries = await list_system_generated_audit_logs(session, limit=30)
    logs = [TroubleshootLog(level="error" if entry.result == "FAILURE" else "info", time=entry.timestamp.strftime("%H:%M:%S"), message=entry.action.replace("_", " ").title() + (f" — {hydrated['target_user_display_name']}" if hydrated["target_user_display_name"] else ""), service=_log_service(entry.action)) for entry, hydrated in log_entries]

    return TroubleshootingResponse(incident=incident, root_causes=root_causes, checklist=checklist, error_trend=error_trend, dependency_chain=dependency_chain, providers=providers, logs=logs, primary_provider_id=primary_provider_id)
