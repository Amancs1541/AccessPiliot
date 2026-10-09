from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AccessPilotError
from app.models import AccessAssignment, AccessReviewCampaign, Application, AuditLog, SocDashboardLayout, User
from app.schemas.soc import SocFieldInfo, SocFieldsResponse, SocLayoutResponse, SocSourceInfo, SocWidgetDataResult, SocWidgetDefinition
from app.services.assignments import _app_role_name, _resolve_target
from app.services.audit_read import _hydrate_entry, list_audit_logs_by_action
from app.services.sod import get_sod_violations

# Curated, not "the N most recent entries overall" (which would mostly be routine sync/assignment noise) — every
# action here is something a security-operations viewer would specifically want to know happened: emergency
# access usage, blocked or forcibly-ended access, a change to SoD governance itself, or a real sync/connector
# failure. A fixed, in-code list for the one built-in "High-signal events" panel — a SoCAdmin building their OWN
# custom list/bar widget against audit_logs (below) can filter to any action they want instead.
SIGNAL_ACTIONS = [
    "BREAKGLASS_LOGIN", "BREAKGLASS_ELEVATED", "BREAKGLASS_PASSWORD_ROTATED",
    "ASSIGNMENT_REVOKED", "ASSIGNMENT_CREATE_BLOCKED",
    "SOD_EXCEPTION_GRANTED", "SOD_EXCEPTION_REVOKED", "SOD_POLICY_DISABLED", "SOD_POLICY_DELETED",
    "PROVIDER_DELETED", "SYNC_FAILED",
    "PORTAL_AUTH_CONFIG_ACTIVATED", "PORTAL_AUTH_CONFIG_UPDATED_VIA_BREAKGLASS",
]

# Every data source a SoCAdmin can build a custom graph against, and the exact columns they're allowed to group
# or filter by — a deliberate whitelist, never raw/arbitrary column access. "day" (bucketing by the source's own
# timestamp column) is always additionally available for a timeseries graph, handled separately below rather
# than listed here, since it needs Python-side bucketing (see _daily_count_series) instead of a plain GROUP BY.
FIELD_DEFS: dict[str, dict] = {
    "audit_logs": {
        "label": "Audit Log",
        "model": AuditLog,
        "time_field": AuditLog.timestamp,
        "fields": {
            "action": ("Action", AuditLog.action),
            "result": ("Result", AuditLog.result),
            "target_type": ("Target type", AuditLog.target_type),
        },
    },
    "assignments": {
        "label": "Access Assignments",
        "model": AccessAssignment,
        "time_field": AccessAssignment.created_at,
        "fields": {
            "status": ("Status", AccessAssignment.status),
            "resource_type": ("Resource type", AccessAssignment.resource_type),
            "assignment_type": ("Assignment type", AccessAssignment.assignment_type),
        },
    },
}

# The fixed set of built-in panels every SoCAdmin starts with — each backed by a hand-written computation in
# _compute_builtin below (some, like open_sod_violations, reuse a live scan that can't be expressed as a plain
# grouped query against one table, which is exactly why they're "built-in" rather than something a custom graph
# could reproduce). A layout only ever references one of these by builtin_id; it never redefines what one means.
_BUILTIN_TITLES = {
    "active_sessions": "Active sessions",
    "pending_approvals": "Pending approvals",
    "open_sod_violations": "SoD violations",
    "breakglass_events_24h": "Break-glass events (24h)",
    "activity_timeline": "Platform activity (30 days)",
    "high_signal_events": "High-signal events",
    "dormant_access": "Dormant access (90+ days)",
    "access_outliers": "Access outliers",
    "identity_risk_posture": "Identity Risk Posture",
    "unclassified_entitlements": "Unclassified entitlements",
}
_BUILTIN_KINDS = {
    "active_sessions": "card", "pending_approvals": "card", "open_sod_violations": "card", "breakglass_events_24h": "card",
    "activity_timeline": "timeseries", "high_signal_events": "list", "dormant_access": "card", "access_outliers": "card",
    "identity_risk_posture": "card", "unclassified_entitlements": "card",
}

# "Light role mining" — no sign-in/usage telemetry exists to measure real usage (same honest limitation already
# documented for INACTIVE_USERS review scope and the dashboard's own stat cards), so DORMANT_ACCESS_DAYS is the
# best available proxy: real, granted access (status ACTIVE) that was activated this long ago and never touched
# since. A fixed window baked into the builtin's own identity, matching breakglass_events_24h's convention,
# rather than a configurable setting that would need its own UI.
DORMANT_ACCESS_DAYS = 90


def _dormant_access_conditions(session_now: Optional[datetime] = None) -> list:
    cutoff = (session_now or datetime.now(timezone.utc)) - timedelta(days=DORMANT_ACCESS_DAYS)
    return [AccessAssignment.status == "ACTIVE", AccessAssignment.activated_at.is_not(None), AccessAssignment.activated_at <= cutoff]


# "Light" peer-group outlier detection — no ML, no sign-in telemetry, just "does almost nobody else in this
# person's own department hold the same thing." A holder is flagged when their department's prevalence for that
# exact entitlement is at or below OUTLIER_PREVALENCE_THRESHOLD; OUTLIER_MIN_DEPARTMENT_SIZE skips departments too
# small for a percentage to mean anything. Both fixed, in-code constants — same "baked into the builtin's own
# identity, not a configurable setting" convention DORMANT_ACCESS_DAYS above already established.
OUTLIER_PREVALENCE_THRESHOLD = 0.10
OUTLIER_MIN_DEPARTMENT_SIZE = 5


async def compute_access_outliers(session: AsyncSession) -> list[dict]:
    """Live detective scan, never stored/materialized — same convention as get_sod_violations(). Scope: real
    ACTIVE assignments held by a real ACTIVE, NORMAL-account person with a known department (PU/TU accounts and
    anyone with no department on file have no peer group to compare against, so they're excluded entirely, never
    flagged). Groups every (resource, app role, department) combination and flags every holder in a department
    whose prevalence for that specific entitlement falls at or below OUTLIER_PREVALENCE_THRESHOLD, as long as the
    department has at least OUTLIER_MIN_DEPARTMENT_SIZE real people to make that percentage meaningful."""
    dept_rows = (await session.execute(
        select(User.department, func.count()).where(User.status == "ACTIVE", User.account_type == "NORMAL", User.department.is_not(None)).group_by(User.department)
    )).all()
    dept_size = {department: count for department, count in dept_rows}

    stmt = (
        select(AccessAssignment, User.department, User.display_name)
        .join(User, User.id == AccessAssignment.user_id)
        .where(AccessAssignment.status == "ACTIVE", User.status == "ACTIVE", User.account_type == "NORMAL", User.department.is_not(None))
    )
    rows = (await session.execute(stmt)).all()

    groups: dict[tuple, list] = {}
    for assignment, department, display_name in rows:
        key = (assignment.resource_type, assignment.resource_id, assignment.app_role_external_id, department)
        groups.setdefault(key, []).append((assignment, display_name))

    outliers: list[dict] = []
    for (resource_type, resource_id, app_role_external_id, department), holders in groups.items():
        size = dept_size.get(department, 0)
        if size < OUTLIER_MIN_DEPARTMENT_SIZE:
            continue
        prevalence = len(holders) / size
        if prevalence > OUTLIER_PREVALENCE_THRESHOLD:
            continue
        try:
            _, resource_name, _ = await _resolve_target(session, resource_type, resource_id)
        except AccessPilotError:
            resource_name = resource_type
        if resource_type == "APPLICATION" and app_role_external_id:
            application = await session.get(Application, resource_id)
            role_name = _app_role_name(application, app_role_external_id)
            if role_name:
                resource_name = f"{resource_name} — {role_name}"
        for assignment, display_name in holders:
            outliers.append({
                "assignment_id": str(assignment.id), "user_id": str(assignment.user_id), "user_display_name": display_name,
                "department": department, "resource_type": resource_type, "resource_display_name": resource_name,
                "department_prevalence_pct": round(prevalence * 100, 1), "department_size": size, "holder_count": len(holders),
            })
    return outliers


async def compute_identity_risk_posture(session: AsyncSession) -> dict:
    """A single rolled-up count of every OPEN risk signal this app already tracks elsewhere as its own widget —
    deliberately a plain sum of real counts, never a fabricated 0-100 "score": this app has no sign-in-risk model
    to back a weighted score honestly (see the same reasoning behind AccessReviewDashboard's missing "high risk
    users" figure), so a transparent total of real, already-computed signals is the honest version of this idea.
    "Overdue reviews" here means a still-ACTIVE campaign past its due_at — normally the access-review worker
    closes these within ~60s of going overdue (see sweep_overdue_campaigns), so a nonzero count here usually means
    either the worker hasn't run yet or momentarily caught one mid-sweep, not a long-standing backlog."""
    sod_count = len(await get_sod_violations(session))
    dormant_count = (await session.execute(select(func.count()).select_from(AccessAssignment).where(*_dormant_access_conditions()))).scalar_one()
    outlier_count = len(await compute_access_outliers(session))
    overdue_count = (await session.execute(select(func.count()).select_from(AccessReviewCampaign).where(AccessReviewCampaign.status == "ACTIVE", AccessReviewCampaign.due_at <= datetime.now(timezone.utc)))).scalar_one()
    breakdown = [
        {"category": "Open SoD violations", "count": sod_count},
        {"category": "Dormant access (90+ days)", "count": dormant_count},
        {"category": "Access outliers", "count": outlier_count},
        {"category": "Overdue Access Reviews", "count": overdue_count},
    ]
    return {"total": sod_count + dormant_count + outlier_count + overdue_count, "breakdown": breakdown}


def default_widgets() -> list[SocWidgetDefinition]:
    return [
        SocWidgetDefinition(id=builtin_id, title=title, kind=_BUILTIN_KINDS[builtin_id], source="builtin", builtin_id=builtin_id, visible=True, order=index)
        for index, (builtin_id, title) in enumerate(_BUILTIN_TITLES.items())
    ]


def get_available_fields() -> SocFieldsResponse:
    sources = [
        SocSourceInfo(source=key, label=config["label"], fields=[SocFieldInfo(field=field_key, label=label) for field_key, (label, _) in config["fields"].items()])
        for key, config in FIELD_DEFS.items()
    ]
    builtin_widgets = [SocFieldInfo(field=builtin_id, label=title) for builtin_id, title in _BUILTIN_TITLES.items()]
    return SocFieldsResponse(sources=sources, builtin_widgets=builtin_widgets)


async def _daily_count_series(session: AsyncSession, time_field, conditions: list, days: int = 30) -> list[dict]:
    """Bucketed in Python, not SQL date_trunc/GROUP BY — the same portability convention already used by
    get_privileged_role_activation_timeline (services/dashboard.py) and the original SoC overview timeline, so
    this behaves identically on SQLite (tests) and Postgres (prod)."""
    since = datetime.now(timezone.utc) - timedelta(days=days - 1)
    stmt = select(time_field).where(time_field >= since, *conditions)
    timestamps = (await session.scalars(stmt)).all()
    counts_by_day: dict = {}
    for timestamp in timestamps:
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        day = timestamp.astimezone(timezone.utc).date()
        counts_by_day[day] = counts_by_day.get(day, 0) + 1
    today = datetime.now(timezone.utc).date()
    return [{"date": (today - timedelta(days=offset)).isoformat(), "count": counts_by_day.get(today - timedelta(days=offset), 0)} for offset in range(days - 1, -1, -1)]


def _audit_row(entry: AuditLog, hydrated: dict) -> dict:
    return {
        "id": str(entry.id), "timestamp": entry.timestamp.isoformat(), "action": entry.action, "result": entry.result,
        "actor_display_name": hydrated["actor_display_name"], "target_user_display_name": hydrated["target_user_display_name"],
    }


async def _compute_builtin(session: AsyncSession, builtin_id: Optional[str]) -> SocWidgetDataResult:
    if builtin_id == "active_sessions":
        value = (await session.execute(select(func.count()).select_from(AccessAssignment).where(AccessAssignment.status == "ACTIVE"))).scalar_one()
        return SocWidgetDataResult(value=value)
    if builtin_id == "pending_approvals":
        value = (await session.execute(select(func.count()).select_from(AccessAssignment).where(AccessAssignment.status == "PENDING_APPROVAL"))).scalar_one()
        return SocWidgetDataResult(value=value)
    if builtin_id == "open_sod_violations":
        # Counts every current conflict, exempted or not — matching the real SoD admin page's own convention
        # (its violations table lists every live conflict with a "Risk status" column, never hiding an exempted
        # one from the count). An earlier version of this card excluded exempted violations entirely, which
        # meant a real, known conflict could show as "0" here with no indication anything existed at all.
        violations = await get_sod_violations(session)
        return SocWidgetDataResult(value=len(violations))
    if builtin_id == "breakglass_events_24h":
        since = datetime.now(timezone.utc) - timedelta(hours=24)
        value = (await session.execute(select(func.count()).select_from(AuditLog).where(AuditLog.action.in_(("BREAKGLASS_LOGIN", "BREAKGLASS_ELEVATED")), AuditLog.timestamp >= since))).scalar_one()
        return SocWidgetDataResult(value=value)
    if builtin_id == "activity_timeline":
        return SocWidgetDataResult(series=await _daily_count_series(session, AuditLog.timestamp, []))
    if builtin_id == "high_signal_events":
        rows = [_audit_row(entry, hydrated) for entry, hydrated in await list_audit_logs_by_action(session, SIGNAL_ACTIONS, limit=25)]
        return SocWidgetDataResult(rows=rows)
    if builtin_id == "dormant_access":
        value = (await session.execute(select(func.count()).select_from(AccessAssignment).where(*_dormant_access_conditions()))).scalar_one()
        return SocWidgetDataResult(value=value)
    if builtin_id == "access_outliers":
        return SocWidgetDataResult(value=len(await compute_access_outliers(session)))
    if builtin_id == "identity_risk_posture":
        return SocWidgetDataResult(value=(await compute_identity_risk_posture(session))["total"])
    if builtin_id == "unclassified_entitlements":
        from app.services.entitlement_catalog import compute_unclassified_entitlements
        return SocWidgetDataResult(value=len(await compute_unclassified_entitlements(session)))
    raise AccessPilotError("UNKNOWN_WIDGET", f"'{builtin_id}' is not a known built-in widget.", 422)


async def _compute_custom(session: AsyncSession, widget: SocWidgetDefinition) -> SocWidgetDataResult:
    config = FIELD_DEFS.get(widget.source)
    if config is None:
        raise AccessPilotError("INVALID_SOURCE", f"'{widget.source}' is not a valid data source.", 422)
    fields = config["fields"]
    conditions = []
    for item in widget.filters:
        if item.field not in fields:
            raise AccessPilotError("INVALID_FIELD", f"'{item.field}' cannot be filtered on {widget.source}.", 422)
        conditions.append(fields[item.field][1] == item.value)

    if widget.kind == "card":
        stmt = select(func.count()).select_from(config["model"])
        if conditions:
            stmt = stmt.where(*conditions)
        return SocWidgetDataResult(value=(await session.execute(stmt)).scalar_one())

    if widget.kind == "timeseries" or widget.group_by == "day":
        return SocWidgetDataResult(series=await _daily_count_series(session, config["time_field"], conditions))

    if widget.kind in ("bar", "list"):
        if not widget.group_by:
            raise AccessPilotError("GROUP_BY_REQUIRED", "This graph needs a field to group by.", 422)
        if widget.group_by not in fields:
            raise AccessPilotError("INVALID_FIELD", f"'{widget.group_by}' is not a valid field on {widget.source}.", 422)
        column = fields[widget.group_by][1]
        stmt = select(column, func.count()).select_from(config["model"]).group_by(column).order_by(func.count().desc())
        if conditions:
            stmt = stmt.where(*conditions)
        rows = (await session.execute(stmt)).all()
        return SocWidgetDataResult(series=[{"label": str(label) if label is not None else "—", "value": count} for label, count in rows])

    raise AccessPilotError("UNKNOWN_KIND", f"'{widget.kind}' is not a known widget kind.", 422)


async def compute_widget_data(session: AsyncSession, widget: SocWidgetDefinition) -> SocWidgetDataResult:
    if widget.source == "builtin":
        return await _compute_builtin(session, widget.builtin_id)
    return await _compute_custom(session, widget)


def _violation_row(violation) -> dict:
    return {
        "policy_name": violation.policy_name,
        "severity": violation.severity,
        "user_display_name": violation.user_display_name,
        "side_a": [holding.resource_display_name for holding in violation.side_a_holdings],
        "side_b": [holding.resource_display_name for holding in violation.side_b_holdings],
        "exception_active": violation.exception_active,
        "exception_expires_at": violation.exception_expires_at.isoformat() if violation.exception_expires_at else None,
    }


def _builtin_drilldown_source(builtin_id: Optional[str]):
    """The model/time-column/base-conditions behind a builtin card or chart's own count — the exact same
    condition _compute_builtin uses for that builtin_id, just returning rows instead of a count. None for a
    builtin with nothing further to drill into (open_sod_violations and high_signal_events are handled as their
    own special cases before this is ever consulted)."""
    if builtin_id == "active_sessions":
        return AccessAssignment, AccessAssignment.created_at, [AccessAssignment.status == "ACTIVE"]
    if builtin_id == "pending_approvals":
        return AccessAssignment, AccessAssignment.created_at, [AccessAssignment.status == "PENDING_APPROVAL"]
    if builtin_id == "breakglass_events_24h":
        since = datetime.now(timezone.utc) - timedelta(hours=24)
        return AuditLog, AuditLog.timestamp, [AuditLog.action.in_(("BREAKGLASS_LOGIN", "BREAKGLASS_ELEVATED")), AuditLog.timestamp >= since]
    if builtin_id == "activity_timeline":
        return AuditLog, AuditLog.timestamp, []
    if builtin_id == "dormant_access":
        return AccessAssignment, AccessAssignment.activated_at, _dormant_access_conditions()
    return None


async def _format_drilldown_rows(session: AsyncSession, model, rows: list) -> list[dict]:
    if model is AuditLog:
        return [_audit_row(entry, await _hydrate_entry(session, entry)) for entry in rows]
    # AccessAssignment — a lighter hydration than the audit log path (just the target user's name, no resource
    # name resolution), since a drilldown list is meant as a quick "who/what" glance, not a full detail view.
    result = []
    for row in rows:
        user = await session.get(User, row.user_id)
        result.append({
            "id": str(row.id), "created_at": row.created_at.isoformat(), "status": row.status,
            "resource_type": row.resource_type, "assignment_type": row.assignment_type,
            "user_display_name": user.display_name if user else None,
            "activated_at": row.activated_at.isoformat() if row.activated_at else None,
        })
    return result


async def compute_widget_drilldown(session: AsyncSession, widget: SocWidgetDefinition, date_str: Optional[str] = None, group_value: Optional[str] = None) -> list[dict]:
    """The actual rows behind whatever was clicked — a card's total, one point on a timeseries chart, or one
    entry in a grouped bar/list — so a SoCAdmin never has to take a number's word for it. Two widgets have
    nothing further to show (already handled before anything else here): the "Open SoD violations" card returns
    every current violation from the same live scan (get_sod_violations) the count itself uses, including ones
    already covered by an exception the count deliberately excludes; "High-signal events" is already a raw list
    of individual events, so there's nothing more granular underneath it to drill into."""
    if widget.source == "builtin" and widget.builtin_id == "open_sod_violations":
        return [_violation_row(violation) for violation in await get_sod_violations(session)]
    if widget.source == "builtin" and widget.builtin_id == "high_signal_events":
        raise AccessPilotError("NOT_DRILLABLE", "This panel already lists individual events.", 422)
    if widget.source == "builtin" and widget.builtin_id == "access_outliers":
        return await compute_access_outliers(session)
    if widget.source == "builtin" and widget.builtin_id == "identity_risk_posture":
        return (await compute_identity_risk_posture(session))["breakdown"]
    if widget.source == "builtin" and widget.builtin_id == "unclassified_entitlements":
        from app.services.entitlement_catalog import compute_unclassified_entitlements
        return [{"unclassified_entitlement": True, "resource_type": e.resource_type, "resource_display_name": e.resource_display_name} for e in await compute_unclassified_entitlements(session)]

    fields: Optional[dict] = None
    if widget.source == "builtin":
        source = _builtin_drilldown_source(widget.builtin_id)
        if source is None:
            raise AccessPilotError("NOT_DRILLABLE", "This panel has no underlying rows to show.", 422)
        model, time_field, conditions = source
    else:
        config = FIELD_DEFS.get(widget.source)
        if config is None:
            raise AccessPilotError("INVALID_SOURCE", f"'{widget.source}' is not a valid data source.", 422)
        model, time_field, fields = config["model"], config["time_field"], config["fields"]
        conditions = []
        for item in widget.filters:
            if item.field not in fields:
                raise AccessPilotError("INVALID_FIELD", f"'{item.field}' cannot be filtered on {widget.source}.", 422)
            conditions.append(fields[item.field][1] == item.value)

    if widget.kind == "timeseries":
        if date_str is None:
            raise AccessPilotError("DATE_REQUIRED", "A date is required to drill into a chart point.", 422)
        day = datetime.strptime(date_str, "%Y-%m-%d").date()
        start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        end = start + timedelta(days=1)
        conditions = [*conditions, time_field >= start, time_field < end]
    elif widget.kind in ("bar", "list"):
        if fields is None or not widget.group_by or widget.group_by not in fields:
            raise AccessPilotError("NOT_DRILLABLE", "This panel has no field to drill into.", 422)
        if group_value is None:
            raise AccessPilotError("GROUP_VALUE_REQUIRED", "Which bar or list entry was clicked is required.", 422)
        conditions = [*conditions, fields[widget.group_by][1] == group_value]
    elif widget.kind != "card":
        raise AccessPilotError("UNKNOWN_KIND", f"'{widget.kind}' is not a known widget kind.", 422)

    stmt = select(model).where(*conditions).order_by(time_field.desc()).limit(200)
    rows = list((await session.scalars(stmt)).all())
    return await _format_drilldown_rows(session, model, rows)


async def get_soc_layout(session: AsyncSession, user_id: UUID) -> SocLayoutResponse:
    layout = (await session.execute(select(SocDashboardLayout).where(SocDashboardLayout.user_id == user_id))).scalars().first()
    if layout is None:
        return SocLayoutResponse(widgets=default_widgets())
    try:
        return SocLayoutResponse(widgets=[SocWidgetDefinition(**widget) for widget in layout.widgets])
    except ValidationError:
        # A row saved under an earlier, incompatible widget shape (e.g. from before this feature had a real
        # widget-builder and just stored {id, visible, order}) — confirmed live: exactly this happened for the
        # first real SoCAdmin to use the dashboard, 500ing on every load afterward. Fail open to the current
        # default rather than breaking the whole page over a stale personal preference; saving a new layout from
        # the UI overwrites the bad row with the current shape automatically.
        return SocLayoutResponse(widgets=default_widgets())


async def save_soc_layout(session: AsyncSession, user_id: UUID, widgets: list[SocWidgetDefinition]) -> SocLayoutResponse:
    layout = (await session.execute(select(SocDashboardLayout).where(SocDashboardLayout.user_id == user_id))).scalars().first()
    serialized = [widget.model_dump() for widget in widgets]
    if layout is None:
        layout = SocDashboardLayout(user_id=user_id, widgets=serialized)
        session.add(layout)
    else:
        layout.widgets = serialized
    await session.commit()
    return SocLayoutResponse(widgets=widgets)
