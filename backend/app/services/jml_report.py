"""The Joiner / Mover / Leaver PDF report: every JML process AccessPilot has recorded, with the items each one touched
(accounts created / enabled / disabled / deleted, access revoked, groups removed, reviews started, people notified).
Read-only; built from the same tables the Movers, Joiners and Leaver screens use. Temporary passwords are never stored,
so they can never appear here."""
from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Optional
from uuid import UUID

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AccessAssignment, AuditLog, JoinerRequest, LeaverPolicy, LeaverRequest, LifecycleEvent, PendingMove, ReenableRequest, User
from app.services.access_reviews import _app_timezone
from app.services.assignments import _resolve_target
from app.services.lifecycle import get_lifecycle_settings, leaver_due_at, list_events, resolve_leaver_policy

MAX_ROWS = 500
INK, MUTED, LINE, HEAD = colors.HexColor("#1c2330"), colors.HexColor("#5d6675"), colors.HexColor("#d5d9e0"), colors.HexColor("#0f766e")


def _styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=base["Title"], fontSize=22, textColor=INK, alignment=0, spaceAfter=4),
        "h1": ParagraphStyle("h1", parent=base["Heading1"], fontSize=15, textColor=HEAD, spaceBefore=14, spaceAfter=6),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontSize=11, textColor=INK, spaceBefore=8, spaceAfter=3),
        "body": ParagraphStyle("b", parent=base["BodyText"], fontSize=9, leading=12, textColor=INK),
        "muted": ParagraphStyle("m", parent=base["BodyText"], fontSize=8, leading=10, textColor=MUTED),
        "cell": ParagraphStyle("c", parent=base["BodyText"], fontSize=7.5, leading=9.5, textColor=INK),
        "head": ParagraphStyle("hd", parent=base["BodyText"], fontSize=7.5, leading=9.5, textColor=colors.white, fontName="Helvetica-Bold"),
    }


def _fmt(value: Optional[datetime], tz) -> str:
    if value is None:
        return "-"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(tz).strftime("%Y-%m-%d %H:%M")


def _table(styles, header: list[str], rows: list[list[str]], widths: list[float]) -> Table:
    data = [[Paragraph(escape(h), styles["head"]) for h in header]] + [[Paragraph(escape(str(cell).replace(chr(0x2192), "->")) if cell is not None else "-", styles["cell"]) for cell in row] for row in rows]
    table = Table(data, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), HEAD), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, LINE), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f7f9")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return table


def _changes_text(changes: dict) -> str:
    parts = []
    for key, value in (changes or {}).items():
        if isinstance(value, dict) and "from" in value:
            parts.append(f"{key.replace('_', ' ')}: {value.get('from') or '-'} -> {value.get('to') or '-'}")
    return "; ".join(parts) or "-"


async def _revoked_items(session: AsyncSession, user_id: UUID, when: datetime) -> list[str]:
    """Access revoked in the same run: assignments of this person whose revocation falls within minutes of the event."""
    start = (when if when.tzinfo else when.replace(tzinfo=timezone.utc)) - timedelta(minutes=2)
    rows = (await session.scalars(select(AccessAssignment).where(AccessAssignment.user_id == user_id, AccessAssignment.status == "REVOKED", AccessAssignment.revoked_at.is_not(None), AccessAssignment.revoked_at >= start, AccessAssignment.revoked_at <= start + timedelta(minutes=15)))).all()
    names = []
    for assignment in rows:
        try:
            _, name, _ = await _resolve_target(session, assignment.resource_type, assignment.resource_id)
        except Exception:  # a resource deleted since; still worth listing
            name = str(assignment.resource_id)
        names.append(f"{assignment.resource_type.title()}: {name}")
    return sorted(names)


async def build_report(session: AsyncSession) -> bytes:
    tz_name = await _app_timezone(session)
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(tz_name)
    styles = _styles()
    now = datetime.now(timezone.utc)
    events = await list_events(session, None, MAX_ROWS)
    joiners = list((await session.scalars(select(JoinerRequest).order_by(JoinerRequest.start_at.desc()).limit(MAX_ROWS))).all())
    moves = list((await session.scalars(select(PendingMove).order_by(PendingMove.effective_at.desc()).limit(MAX_ROWS))).all())
    policies = list((await session.scalars(select(LeaverPolicy).order_by(LeaverPolicy.is_default, LeaverPolicy.priority))).all())
    requests = list((await session.scalars(select(ReenableRequest).order_by(ReenableRequest.created_at.desc()).limit(MAX_ROWS))).all())
    scheduled = list((await session.scalars(select(User).where(User.leaver_date.is_not(None), User.leaver_processed_at.is_(None), User.account_type == "NORMAL").order_by(User.leaver_date))).all())
    deleted_users = list((await session.scalars(select(User).where(User.accounts_deleted_at.is_not(None)).order_by(User.accounts_deleted_at.desc()))).all())
    awaiting_delete = list((await session.scalars(select(User).where(User.accounts_delete_at.is_not(None), User.accounts_deleted_at.is_(None)).order_by(User.accounts_delete_at))).all())
    settings = await get_lifecycle_settings(session)
    by_type = {t: [e for e in events if e.event_type == t] for t in ("JOINER", "MOVER", "LEAVER")}

    story: list = []
    story += [Paragraph("Joiner / Mover / Leaver report", styles["title"]),
              Paragraph(f"Generated {_fmt(now, tz)} ({tz_name}). Covers every joiner, mover and leaver process AccessPilot has recorded, with the items each one touched. Temporary passwords are never stored and are not part of this report.", styles["muted"]), Spacer(1, 6)]

    story.append(Paragraph("1. Summary", styles["h1"]))
    story.append(_table(styles, ["Measure", "Count"], [
        ["Joiners recorded (requests)", len(joiners)], ["Joiner events", len(by_type["JOINER"])], ["Mover events", len(by_type["MOVER"])], ["Leaver events", len(by_type["LEAVER"])],
        ["Scheduled moves (pending)", sum(1 for m in moves if m.status == "SCHEDULED")], ["Scheduled leavers (waiting for their date)", len(scheduled)],
        ["Access items revoked by mover/leaver events", sum(e.revoked_count for e in events if e.event_type in ("MOVER", "LEAVER"))],
        ["Re-enable requests (pending / total)", f"{sum(1 for r in requests if r.status == 'PENDING')} / {len(requests)}"],
        ["People with accounts deleted (audit)", len(deleted_users)], ["People waiting for account deletion", len(awaiting_delete)],
    ], [110 * mm, 40 * mm]))

    story.append(Paragraph("2. Configuration in force", styles["h1"]))
    story.append(Paragraph(f"Mover review: {'on' if settings.mover_review_enabled else 'off'}, due in {settings.review_due_days} days. Leaver revokes access when a directory reports the person disabled: {'yes' if settings.revoke_on_directory_disable else 'no'}.", styles["body"]))
    story.append(Paragraph("Leaver policies (first matching active policy by priority wins)", styles["h2"]))
    story.append(_table(styles, ["Policy", "Priority", "Applies to", "Runs at", "Reminders (days before)", "Actions", "Delete accounts after"], [
        [p.name + (" (default)" if p.is_default else ""), p.priority, "Everyone" if p.scope_type == "ALL" else f"{p.scope_type.replace('_', ' ').title()}: {p.scope_value}", p.effective_time, ", ".join(str(d) for d in (p.notify_days_before or [])) or "-",
         ", ".join(x for x, on in (("revoke access", p.revoke_access), ("disable accounts", p.disable_accounts), ("disable PU/TU", p.disable_privileged_accounts), ("remove group memberships", p.remove_group_memberships)) if on) or "none",
         f"{p.delete_after_days} days" if p.delete_after_days else "never"] for p in policies], [42 * mm, 15 * mm, 38 * mm, 15 * mm, 30 * mm, 55 * mm, 30 * mm]))

    story.append(Paragraph("3. Joiners", styles["h1"]))
    rows = []
    for joiner in joiners:
        manager = await session.get(User, joiner.manager_id) if joiner.manager_id else None
        accounts = "; ".join(f"{t.get('provider_name')}: {t.get('username')} [{t.get('status')}{', manager set' if t.get('manager_pushed') else ''}]" + (f" ERROR {t.get('error')}" if t.get("error") else "") for t in (joiner.targets or [])) or "-"
        rows.append([f"{joiner.first_name} {joiner.last_name}", joiner.employee_id or "-", joiner.department or "-", joiner.job_title or "-", manager.display_name if manager else "-", _fmt(joiner.start_at, tz), joiner.leaver_date.isoformat() if joiner.leaver_date else "-", joiner.status, accounts])
    story.append(_table(styles, ["Person", "Employee ID", "Department", "Job title", "Manager", "Start", "Leaver date", "Status", "Accounts created (per directory)"], rows or [["No joiners recorded."] + [""] * 8], [26 * mm, 18 * mm, 22 * mm, 22 * mm, 24 * mm, 24 * mm, 18 * mm, 16 * mm, 78 * mm]))
    story.append(Paragraph("Joiner events (birthright access granted on start)", styles["h2"]))
    story.append(_table(styles, ["When", "Person", "Source", "Access made eligible", "People notified"], [[_fmt(e.created_at, tz), e.user_display_name, e.source, e.granted_count, ", ".join(e.notified) or "-"] for e in by_type["JOINER"]] or [["None recorded", "", "", "", ""]], [30 * mm, 45 * mm, 25 * mm, 35 * mm, 100 * mm]))

    story.append(Paragraph("4. Movers", styles["h1"]))
    rows = []
    for e in by_type["MOVER"]:
        items = await _revoked_items(session, e.user_id, e.created_at)
        review = f"{e.review_campaign_name} ({e.review_status}; {e.review_decided_count}/{e.review_item_count} decided; reviewer {e.review_reviewer_name})" if e.review_campaign_name else (e.review_note or "no review")
        rows.append([_fmt(e.created_at, tz), e.user_display_name, e.source, _changes_text(e.changes), f"{e.revoked_count}" + (": " + "; ".join(items) if items else ""), e.granted_count, review, ", ".join(e.notified) or "-"])
    story.append(_table(styles, ["When", "Person", "Source", "Change", "Access revoked (items)", "Newly eligible", "Leftover-access review", "Notified"], rows or [["No movers recorded."] + [""] * 7], [26 * mm, 26 * mm, 22 * mm, 40 * mm, 50 * mm, 14 * mm, 48 * mm, 32 * mm]))
    story.append(Paragraph("Scheduled (effective-dated) moves", styles["h2"]))
    mrows = []
    for m in moves:
        person = await session.get(User, m.user_id)
        mrows.append([person.display_name if person else "-", ", ".join(x for x in (f"department -> {m.new_department}" if m.new_department else "", f"job title -> {m.new_job_title}" if m.new_job_title else "") if x), _fmt(m.effective_at, tz), m.source, m.status, m.failure_reason or "-"])
    story.append(_table(styles, ["Person", "Change", "Effective", "Source", "Status", "Failure"], mrows or [["None", "", "", "", "", ""]], [45 * mm, 70 * mm, 30 * mm, 20 * mm, 22 * mm, 60 * mm]))

    story.append(Paragraph("5. Leavers", styles["h1"]))
    rows = []
    for e in by_type["LEAVER"]:
        items = await _revoked_items(session, e.user_id, e.created_at)
        c = e.changes or {}
        accounts = "; ".join(f"{a.get('provider')}: {'disabled' if a.get('ok') else 'FAILED ' + str(a.get('error') or '')}" for a in c.get("accounts", [])) or "-"
        rows.append([_fmt(e.created_at, tz), e.user_display_name, e.source, c.get("policy") or "-", f"{e.revoked_count}" + (": " + "; ".join(items) if items else ""), accounts, f"{c.get('linked_accounts_disabled', 0)} PU/TU; {c.get('groups_removed', 0)} groups", f"{c['delete_after_days']} days" if c.get("delete_after_days") else "never", ", ".join(e.notified) or "-"])
    story.append(_table(styles, ["When", "Person", "Source", "Policy", "Access revoked (items)", "Directory accounts", "Also", "Deletion", "Notified"], rows or [["No leavers recorded."] + [""] * 8], [24 * mm, 25 * mm, 14 * mm, 26 * mm, 50 * mm, 40 * mm, 24 * mm, 16 * mm, 32 * mm]))
    story.append(Paragraph("Scheduled leavers (waiting for their leaver date)", styles["h2"]))
    srows = []
    for u in scheduled:
        policy = await resolve_leaver_policy(session, u)
        srows.append([u.display_name, u.department or "-", u.employment_type or "-", u.leaver_date.isoformat(), policy.name, _fmt(leaver_due_at(u.leaver_date, policy.effective_time, tz_name), tz), f"{policy.delete_after_days} days after" if policy.delete_after_days else "never"])
    story.append(_table(styles, ["Person", "Department", "Employment type", "Leaver date", "Policy", "Runs at", "Account deletion"], srows or [["None", "", "", "", "", "", ""]], [42 * mm, 34 * mm, 28 * mm, 24 * mm, 45 * mm, 30 * mm, 30 * mm]))

    story.append(Paragraph("Manual leaver requests (justification, approval, outcome)", styles["h2"]))
    lrows = []
    for r in list((await session.scalars(select(LeaverRequest).order_by(LeaverRequest.created_at.desc()).limit(MAX_ROWS))).all()):
        person, requester, decider = await session.get(User, r.user_id), (await session.get(User, r.requested_by) if r.requested_by else None), (await session.get(User, r.decided_by) if r.decided_by else None)
        approvers = ", ".join(u.display_name for u in [await session.get(User, UUID(str(a))) for a in (r.approver_ids or [])] if u is not None) or "-"
        lrows.append([_fmt(r.created_at, tz), person.display_name if person else "-", requester.display_name if requester else "-", r.justification, approvers, r.status, (decider.display_name if decider else "-") + (f": {r.decision_note}" if r.decision_note else ""), r.outcome or "-"])
    story.append(_table(styles, ["Requested", "Person", "Started by", "Justification", "Approvers", "Status", "Decided by / note", "Outcome"], lrows or [["None"] + [""] * 7], [24 * mm, 28 * mm, 26 * mm, 50 * mm, 28 * mm, 18 * mm, 38 * mm, 48 * mm]))

    story.append(Paragraph("6. Account deletion (retention) - kept as 'Deleted' for audit", styles["h1"]))
    drows = [[u.display_name, u.email or "-", u.employee_id or "-", u.department or "-", _fmt(u.leaver_processed_at, tz), _fmt(u.accounts_deleted_at, tz)] for u in deleted_users]
    story.append(_table(styles, ["Person", "Email", "Employee ID", "Department", "Left", "Accounts deleted"], drows or [["No accounts deleted yet.", "", "", "", "", ""]], [45 * mm, 65 * mm, 25 * mm, 35 * mm, 30 * mm, 30 * mm]))
    story.append(Paragraph("Waiting for deletion", styles["h2"]))
    story.append(_table(styles, ["Person", "Left", "Accounts will be deleted"], [[u.display_name, _fmt(u.leaver_processed_at, tz), _fmt(u.accounts_delete_at, tz)] for u in awaiting_delete] or [["None", "", ""]], [70 * mm, 45 * mm, 60 * mm]))
    audit_rows = list((await session.scalars(select(AuditLog).where(AuditLog.action == "LEAVER_ACCOUNTS_DELETED").order_by(AuditLog.created_at.desc()).limit(MAX_ROWS))).all())
    if audit_rows:
        story.append(Paragraph("Audit trail of deletions (the record kept after the accounts are gone)", styles["h2"]))
        story.append(_table(styles, ["When", "Person (as recorded)", "Employee ID", "Department", "Directories"], [[_fmt(a.created_at, tz), ((a.metadata_json or {}).get("person") or {}).get("display_name", "-"), ((a.metadata_json or {}).get("person") or {}).get("employee_id") or "-", ((a.metadata_json or {}).get("person") or {}).get("department") or "-", ", ".join(x.get("provider", "?") for x in (a.metadata_json or {}).get("accounts", []))] for a in audit_rows], [30 * mm, 55 * mm, 30 * mm, 40 * mm, 60 * mm]))

    story.append(Paragraph("7. Re-enable requests after leaving", styles["h1"]))
    rrows = []
    for r in requests:
        person, requester, decider = await session.get(User, r.user_id), (await session.get(User, r.requested_by) if r.requested_by else None), (await session.get(User, r.decided_by) if r.decided_by else None)
        approvers = ", ".join(u.display_name for u in [await session.get(User, UUID(str(a))) for a in (r.approver_ids or [])] if u is not None) or "-"
        rrows.append([_fmt(r.created_at, tz), person.display_name if person else "-", requester.display_name if requester else "-", r.reason, approvers, r.status, (decider.display_name if decider else "-") + (f": {r.decision_note}" if r.decision_note else ""), _fmt(r.decided_at, tz)])
    story.append(_table(styles, ["Requested", "Person", "Requested by", "Reason", "Approvers", "Status", "Decided by / note", "Decided"], rrows or [["None"] + [""] * 7], [24 * mm, 28 * mm, 26 * mm, 50 * mm, 32 * mm, 18 * mm, 42 * mm, 24 * mm]))

    buffer = io.BytesIO()

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(12 * mm, 8 * mm, "AccessPilot - Joiner / Mover / Leaver report")
        canvas.drawRightString(landscape(A4)[0] - 12 * mm, 8 * mm, f"Page {doc.page}")
        canvas.restoreState()

    SimpleDocTemplate(buffer, pagesize=landscape(A4), leftMargin=12 * mm, rightMargin=12 * mm, topMargin=12 * mm, bottomMargin=14 * mm, title="AccessPilot JML report", author="AccessPilot").build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
