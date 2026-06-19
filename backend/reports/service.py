"""Incident report query, metrics, CSV, and PDF rendering."""

from __future__ import annotations

import csv
import io
import statistics
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import Incident, MaintenanceWindow, Service, Team


@dataclass
class IncidentReport:
    generated_at: datetime
    from_at: datetime
    to_at: datetime
    incidents: list[dict[str, Any]]
    metrics: dict[str, Any]


def _iso(value: datetime | None) -> str:
    return value.isoformat() if value else ""


async def build_incident_report(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    from_at: datetime,
    to_at: datetime,
    filters: dict[str, Any] | None = None,
) -> IncidentReport:
    filters = filters or {}
    stmt = (
        select(Incident, Service.name, Team.name)
        .outerjoin(Service, Incident.service_id == Service.id)
        .outerjoin(Team, Service.team_id == Team.id)
        .where(
            Incident.org_id == org_id,
            Incident.created_at >= from_at,
            Incident.created_at < to_at,
        )
        .order_by(Incident.created_at)
    )
    if filters.get("status"):
        stmt = stmt.where(Incident.status == filters["status"])
    if filters.get("severity"):
        stmt = stmt.where(Incident.severity == filters["severity"])
    if filters.get("priority"):
        stmt = stmt.where(Incident.priority == filters["priority"])
    if filters.get("service_id"):
        stmt = stmt.where(Incident.service_id == uuid.UUID(str(filters["service_id"])))
    if filters.get("team_id"):
        stmt = stmt.where(Team.id == uuid.UUID(str(filters["team_id"])))

    rows = (await db.execute(stmt)).all()
    incidents: list[dict[str, Any]] = []
    mtta: list[float] = []
    mttr: list[float] = []
    for incident, service_name, team_name in rows:
        if incident.acknowledged_at:
            mtta.append(
                max(0.0, (incident.acknowledged_at - incident.created_at).total_seconds())
            )
        if incident.status == "resolved":
            mttr.append(max(0.0, (incident.updated_at - incident.created_at).total_seconds()))
        incidents.append(
            {
                "id": str(incident.id),
                "title": incident.title,
                "status": incident.status,
                "severity": incident.severity or "",
                "priority": incident.priority or "",
                "service": service_name or incident.external_source or "",
                "team": team_name or "",
                "source": incident.external_source or "",
                "created_at": _iso(incident.created_at),
                "acknowledged_at": _iso(incident.acknowledged_at),
                "updated_at": _iso(incident.updated_at),
                "mtta_seconds": (
                    round((incident.acknowledged_at - incident.created_at).total_seconds(), 2)
                    if incident.acknowledged_at
                    else None
                ),
                "mttr_seconds": (
                    round((incident.updated_at - incident.created_at).total_seconds(), 2)
                    if incident.status == "resolved"
                    else None
                ),
            }
        )

    maintenance_ids = (
        (
            await db.execute(
                select(MaintenanceWindow.id).where(
                    MaintenanceWindow.org_id == org_id,
                    MaintenanceWindow.starts_at < to_at,
                    MaintenanceWindow.ends_at >= from_at,
                )
            )
        )
        .scalars()
        .unique()
        .all()
    )
    statuses = Counter(row["status"] for row in incidents)
    severities = Counter(row["severity"] or "unknown" for row in incidents)
    priorities = Counter(row["priority"] or "unknown" for row in incidents)
    services = Counter(row["service"] or "unassigned" for row in incidents)
    teams = Counter(row["team"] or "unassigned" for row in incidents)
    metrics = {
        "total_incidents": len(incidents),
        "by_status": dict(sorted(statuses.items())),
        "by_severity": dict(sorted(severities.items())),
        "by_priority": dict(sorted(priorities.items())),
        "by_service": dict(sorted(services.items())),
        "by_team": dict(sorted(teams.items())),
        "top_services": [
            {"service": name, "count": count}
            for name, count in services.most_common(10)
        ],
        "mtta_seconds": round(statistics.median(mtta), 2) if mtta else None,
        "mttr_seconds": round(statistics.median(mttr), 2) if mttr else None,
        "slo_breach_incidents": sum(
            1 for row in incidents if row["source"].startswith("slo:")
        ),
        "maintenance_windows": len(maintenance_ids),
    }
    return IncidentReport(
        generated_at=datetime.now(timezone.utc),
        from_at=from_at,
        to_at=to_at,
        incidents=incidents,
        metrics=metrics,
    )


def render_csv(report: IncidentReport) -> bytes:
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow(["OpsMender incident report"])
    writer.writerow(["from", report.from_at.isoformat()])
    writer.writerow(["to", report.to_at.isoformat()])
    writer.writerow(["generated_at", report.generated_at.isoformat()])
    writer.writerow([])
    writer.writerow(["metric", "value"])
    writer.writerow(["total_incidents", report.metrics["total_incidents"]])
    writer.writerow(["mtta_seconds", report.metrics["mtta_seconds"] or ""])
    writer.writerow(["mttr_seconds", report.metrics["mttr_seconds"] or ""])
    writer.writerow(["slo_breach_incidents", report.metrics["slo_breach_incidents"]])
    writer.writerow(["maintenance_windows", report.metrics["maintenance_windows"]])
    for group in ("by_status", "by_severity", "by_priority", "by_service", "by_team"):
        for key, value in report.metrics[group].items():
            writer.writerow([f"{group}.{key}", value])
    writer.writerow([])
    columns = [
        "id",
        "title",
        "status",
        "severity",
        "priority",
        "service",
        "team",
        "source",
        "created_at",
        "acknowledged_at",
        "updated_at",
        "mtta_seconds",
        "mttr_seconds",
    ]
    writer.writerow(columns)
    for row in report.incidents:
        writer.writerow([row.get(column, "") for column in columns])
    return out.getvalue().encode("utf-8")


def render_pdf(report: IncidentReport) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=0.5 * inch,
        leftMargin=0.5 * inch,
        topMargin=0.5 * inch,
        bottomMargin=0.5 * inch,
    )
    styles = getSampleStyleSheet()
    story = [
        Paragraph("OpsMender Incident Report", styles["Title"]),
        Paragraph(
            f"{report.from_at.isoformat()} through {report.to_at.isoformat()}",
            styles["Normal"],
        ),
        Spacer(1, 12),
    ]
    summary = [
        ["Metric", "Value"],
        ["Incidents", report.metrics["total_incidents"]],
        ["Median MTTA (seconds)", report.metrics["mtta_seconds"] or "—"],
        ["Median MTTR (seconds)", report.metrics["mttr_seconds"] or "—"],
        ["SLO-breach incidents", report.metrics["slo_breach_incidents"]],
        ["Maintenance windows", report.metrics["maintenance_windows"]],
    ]
    table = Table(summary, colWidths=[2.5 * inch, 2 * inch])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#334155")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("PADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.extend([table, Spacer(1, 16), Paragraph("Incidents", styles["Heading2"])])
    incident_rows = [["Created", "Priority", "Status", "Service", "Title"]]
    for row in report.incidents:
        incident_rows.append(
            [
                row["created_at"][:10],
                row["priority"] or "—",
                row["status"],
                row["service"] or "—",
                Paragraph(row["title"], styles["BodyText"]),
            ]
        )
    incidents_table = Table(
        incident_rows or [["No incidents"]],
        repeatRows=1,
        colWidths=[0.8 * inch, 0.55 * inch, 0.8 * inch, 1.25 * inch, 3.1 * inch],
    )
    incidents_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e2e8f0")),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("PADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(incidents_table)
    doc.build(story)
    return buffer.getvalue()


def render_report(report: IncidentReport, format: str) -> tuple[bytes, str]:
    if format == "csv":
        return render_csv(report), "text/csv; charset=utf-8"
    return render_pdf(report), "application/pdf"
