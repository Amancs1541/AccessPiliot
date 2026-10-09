"""Attestation/compliance exports for a completed (or in-progress) Access Review campaign: a PDF report in the
shape an auditor actually asks for, and a CSV of the same line items for anyone who wants to process it rather
than file it. Pure presentation layer — takes already-hydrated response objects (the same ones the campaign
detail/items endpoints already return) rather than touching the database itself, so it can never drift from what
the UI shows. Reuses the PDF styling helpers already proven by the JML report (see app.services.jml_report) rather
than inventing a second report-building convention."""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from app.schemas.access_reviews import AccessReviewCampaignResponse, AccessReviewItemResponse
from app.services.jml_report import MUTED, _fmt, _styles, _table


def build_campaign_report(campaign: AccessReviewCampaignResponse, items: list[AccessReviewItemResponse], tz_name: str) -> bytes:
    tz = ZoneInfo(tz_name)
    styles = _styles()
    now = datetime.now(timezone.utc)

    story: list = [
        Paragraph(f"Access Review report — {campaign.name}", styles["title"]),
        Paragraph(f"Generated {_fmt(now, tz)} ({tz_name}).", styles["muted"]), Spacer(1, 6),
    ]
    if campaign.description:
        story.append(Paragraph(campaign.description, styles["body"]))

    story.append(Paragraph("Campaign", styles["h1"]))
    scope = "Everyone" if campaign.scope_type == "ALL" else campaign.scope_type.replace("_", " ").title()
    story.append(_table(styles, ["Field", "Value"], [
        ["Status", campaign.status], ["Scope", scope],
        ["Reviewer", campaign.reviewer_display_name or (campaign.workflow_definition_name and f"Workflow: {campaign.workflow_definition_name}") or "—"],
        ["Fallback reviewer", campaign.fallback_reviewer_display_name or "—"],
        ["Due", _fmt(campaign.due_at, tz)], ["Created", _fmt(campaign.created_at, tz)], ["Completed", _fmt(campaign.completed_at, tz) if campaign.completed_at else "—"],
    ], [50 * mm, 120 * mm]))

    story.append(Paragraph("Summary", styles["h1"]))
    story.append(_table(styles, ["Measure", "Count"], [
        ["Total items", campaign.item_count], ["Decided", campaign.decided_count], ["Pending", campaign.item_count - campaign.decided_count],
        ["Approved", campaign.approved_count], ["Revoked (reviewer decision)", campaign.revoked_count], ["Auto-revoked (no response)", campaign.auto_revoked_count],
    ], [110 * mm, 40 * mm]))

    story.append(Paragraph("Items", styles["h1"]))
    rows = [[
        item.user_display_name or str(item.user_id), f"{item.resource_type.title()}: {item.resource_display_name or item.resource_id}",
        item.granted_via or "—", item.decision, item.decided_by_display_name or "—", _fmt(item.decided_at, tz) if item.decided_at else "—", item.justification or "—",
    ] for item in items]
    story.append(_table(styles, ["User", "Resource", "Granted via", "Decision", "Decided by", "Decided at", "Justification"], rows or [["No items in this campaign."] + [""] * 6], [28 * mm, 50 * mm, 32 * mm, 20 * mm, 24 * mm, 24 * mm, 64 * mm]))

    buffer = io.BytesIO()

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(12 * mm, 8 * mm, f"AccessPilot - Access Review report - {campaign.name}")
        canvas.drawRightString(landscape(A4)[0] - 12 * mm, 8 * mm, f"Page {doc.page}")
        canvas.restoreState()

    SimpleDocTemplate(buffer, pagesize=landscape(A4), leftMargin=12 * mm, rightMargin=12 * mm, topMargin=12 * mm, bottomMargin=14 * mm, title=f"AccessPilot Access Review report — {campaign.name}", author="AccessPilot").build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()


def build_campaign_csv(campaign: AccessReviewCampaignResponse, items: list[AccessReviewItemResponse]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["User", "Email", "Resource Type", "Resource", "Granted Via", "Decision", "Decided By", "Decided At", "Justification"])
    for item in items:
        writer.writerow([
            item.user_display_name or str(item.user_id), item.user_email or "", item.resource_type, item.resource_display_name or str(item.resource_id),
            item.granted_via or "", item.decision, item.decided_by_display_name or "", item.decided_at.isoformat() if item.decided_at else "", item.justification or "",
        ])
    return buffer.getvalue()
