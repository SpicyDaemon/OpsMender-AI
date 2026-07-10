"""Read-only analytics reports for alert noise and response time."""

from __future__ import annotations

import csv
import io
import statistics
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import case, extract, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import Incident, IngestLog, IngestToken, Service

NOISE_METRIC_DEFINITIONS = {
    "inbound_alerts": "Inbound alerts are ingest log rows created in the selected time range.",
    "incidents_created": "Incidents created is the count of ingest log rows whose dedup action is created.",
    "noise_reduction_ratio": "Noise reduction ratio is 1 minus incidents created divided by inbound alerts.",
    "grouped_alert_savings": "Grouped alert savings is the sum of correlated alerts recorded on incidents created in the selected range.",
    "flapping_incident_count": "Flapping incident count is the number of incidents created in the selected range that were marked flapping.",
}

RESPONSE_METRIC_DEFINITIONS = {
    "mtta_seconds": "MTTA seconds is the median created-to-acknowledged duration for incidents with acknowledged_at set.",
    "mttr_seconds": "MTTR seconds is the median created-to-resolved duration for incidents whose status is resolved, using updated_at as the resolved timestamp.",
}


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _median(values: Iterable[float]) -> float | None:
    values = list(values)
    return round(statistics.median(values), 2) if values else None


def _noise_ratio(inbound: int, created: int) -> float:
    if inbound <= 0:
        return 0.0
    return round(1 - (created / inbound), 4)


def _log_base(org_id: uuid.UUID, from_at: datetime, to_at: datetime):
    return select(IngestLog).where(
        IngestLog.org_id == org_id,
        IngestLog.created_at >= from_at,
        IngestLog.created_at < to_at,
    )


def _with_service_filter(stmt, service_id: uuid.UUID | None):
    if service_id is None:
        return stmt
    return stmt.join(
        IngestToken,
        IngestToken.id == IngestLog.ingest_token_id,
    ).where(IngestToken.service_id == service_id)


async def build_noise_report(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    from_at: datetime,
    to_at: datetime,
    service_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Build alert-noise metrics from ingest logs and incident noise fields."""

    from_at = _utc(from_at)
    to_at = _utc(to_at)

    inbound_stmt = _with_service_filter(
        select(func.count(IngestLog.id))
        .select_from(IngestLog)
        .where(
            IngestLog.org_id == org_id,
            IngestLog.created_at >= from_at,
            IngestLog.created_at < to_at,
        ),
        service_id,
    )
    inbound_alerts = int((await db.execute(inbound_stmt)).scalar_one() or 0)

    breakdown_stmt = _with_service_filter(
        select(IngestLog.dedup_action, func.count(IngestLog.id))
        .select_from(IngestLog)
        .where(
            IngestLog.org_id == org_id,
            IngestLog.created_at >= from_at,
            IngestLog.created_at < to_at,
        )
        .group_by(IngestLog.dedup_action),
        service_id,
    )
    dedup_breakdown = {"created": 0, "updated": 0, "skipped": 0}
    for action, count in (await db.execute(breakdown_stmt)).all():
        if action in dedup_breakdown:
            dedup_breakdown[str(action)] = int(count or 0)
    incidents_created = dedup_breakdown["created"]

    incident_filters = [
        Incident.org_id == org_id,
        Incident.created_at >= from_at,
        Incident.created_at < to_at,
    ]
    if service_id is not None:
        incident_filters.append(Incident.service_id == service_id)
    incident_metrics = (
        await db.execute(
            select(
                func.coalesce(func.sum(Incident.correlated_count), 0),
                func.sum(case((Incident.flapping.is_(True), 1), else_=0)),
            ).where(*incident_filters)
        )
    ).one()
    grouped_alert_savings = int(incident_metrics[0] or 0)
    flapping_incident_count = int(incident_metrics[1] or 0)

    service_stmt = (
        select(
            IngestToken.service_id,
            Service.name,
            func.count(IngestLog.id).label("alert_count"),
            func.sum(case((IngestLog.dedup_action == "created", 1), else_=0)).label(
                "created_count"
            ),
        )
        .select_from(IngestLog)
        .join(IngestToken, IngestToken.id == IngestLog.ingest_token_id)
        .outerjoin(Service, Service.id == IngestToken.service_id)
        .where(
            IngestLog.org_id == org_id,
            IngestLog.created_at >= from_at,
            IngestLog.created_at < to_at,
            IngestToken.service_id.is_not(None),
        )
        .group_by(IngestToken.service_id, Service.name)
    )
    if service_id is not None:
        service_stmt = service_stmt.where(IngestToken.service_id == service_id)
    top_services = []
    for sid, name, alert_count, created_count in (await db.execute(service_stmt)).all():
        alerts = int(alert_count or 0)
        created = int(created_count or 0)
        top_services.append(
            {
                "service_id": str(sid) if sid else None,
                "service_name": name or "Unassigned",
                "inbound_alerts": alerts,
                "incidents_created": created,
                "alerts_per_created_incident": round(alerts / max(created, 1), 2),
            }
        )
    top_services.sort(
        key=lambda row: (
            row["alerts_per_created_incident"],
            row["inbound_alerts"],
            row["service_name"],
        ),
        reverse=True,
    )

    hour_expr = extract("hour", IngestLog.created_at)
    hour_stmt = _with_service_filter(
        select(hour_expr.label("hour"), func.count(IngestLog.id))
        .select_from(IngestLog)
        .where(
            IngestLog.org_id == org_id,
            IngestLog.created_at >= from_at,
            IngestLog.created_at < to_at,
        )
        .group_by(hour_expr),
        service_id,
    )
    hour_counts = {hour: 0 for hour in range(24)}
    for hour, count in (await db.execute(hour_stmt)).all():
        if hour is not None:
            hour_counts[int(hour)] = int(count or 0)

    return {
        "from_at": from_at.isoformat(),
        "to_at": to_at.isoformat(),
        "service_id": str(service_id) if service_id else None,
        "definitions": NOISE_METRIC_DEFINITIONS,
        "inbound_alerts": inbound_alerts,
        "incidents_created": incidents_created,
        "dedup_breakdown": dedup_breakdown,
        "noise_reduction_ratio": _noise_ratio(inbound_alerts, incidents_created),
        "grouped_alert_savings": grouped_alert_savings,
        "flapping_incident_count": flapping_incident_count,
        "top_noisy_services": top_services[:10],
        "alerts_by_hour_utc": [
            {"hour": hour, "alerts": hour_counts[hour]} for hour in range(24)
        ],
        "hour_display_caveat": "Hour buckets are grouped by UTC hour of day.",
    }


def _duration_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    mtta = [
        max(0.0, (row["acknowledged_at"] - row["created_at"]).total_seconds())
        for row in rows
        if row["acknowledged_at"] is not None
    ]
    mttr = [
        max(0.0, (row["updated_at"] - row["created_at"]).total_seconds())
        for row in rows
        if row["status"] == "resolved"
    ]
    return {
        "incident_count": len(rows),
        "acknowledged_count": len(mtta),
        "resolved_count": len(mttr),
        "mtta_seconds": _median(mtta),
        "mttr_seconds": _median(mttr),
    }


def _week_start(value: datetime) -> datetime:
    day = value.astimezone(timezone.utc).date()
    return datetime.combine(
        day - timedelta(days=day.weekday()),
        datetime.min.time(),
        tzinfo=timezone.utc,
    )


async def build_response_report(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    from_at: datetime,
    to_at: datetime,
    service_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """Build MTTA and MTTR metrics from incident lifecycle timestamps."""

    from_at = _utc(from_at)
    to_at = _utc(to_at)
    stmt = (
        select(
            Incident.id,
            Incident.service_id,
            Service.name,
            Incident.priority,
            Incident.status,
            Incident.created_at,
            Incident.acknowledged_at,
            Incident.updated_at,
        )
        .outerjoin(Service, Service.id == Incident.service_id)
        .where(
            Incident.org_id == org_id,
            Incident.created_at >= from_at,
            Incident.created_at < to_at,
        )
    )
    if service_id is not None:
        stmt = stmt.where(Incident.service_id == service_id)
    rows = []
    for row in (await db.execute(stmt)).all():
        rows.append(
            {
                "id": row.id,
                "service_id": row.service_id,
                "service_name": row.name or "Unassigned",
                "priority": row.priority or "unknown",
                "status": row.status,
                "created_at": _utc(row.created_at),
                "acknowledged_at": _utc(row.acknowledged_at)
                if row.acknowledged_at
                else None,
                "updated_at": _utc(row.updated_at),
            }
        )

    by_service: dict[uuid.UUID | None, list[dict[str, Any]]] = defaultdict(list)
    by_priority: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_week: dict[str, list[dict[str, Any]]] = defaultdict(list)
    service_names: dict[uuid.UUID | None, str] = {}
    for row in rows:
        by_service[row["service_id"]].append(row)
        by_priority[row["priority"]].append(row)
        by_week[_week_start(row["created_at"]).date().isoformat()].append(row)
        service_names[row["service_id"]] = row["service_name"]

    per_service = [
        {
            "service_id": str(sid) if sid else None,
            "service_name": service_names.get(sid, "Unassigned"),
            **_duration_metrics(items),
        }
        for sid, items in by_service.items()
    ]
    per_service.sort(key=lambda row: row["service_name"])

    priorities = ["P0", "P1", "P2", "P3"]
    per_priority = [
        {"priority": priority, **_duration_metrics(by_priority.get(priority, []))}
        for priority in priorities
    ]
    for priority in sorted(k for k in by_priority if k not in priorities):
        per_priority.append(
            {"priority": priority, **_duration_metrics(by_priority[priority])}
        )

    weekly_trend = [
        {"week_start": week, **_duration_metrics(items)}
        for week, items in sorted(by_week.items())
    ]

    return {
        "from_at": from_at.isoformat(),
        "to_at": to_at.isoformat(),
        "service_id": str(service_id) if service_id else None,
        "definitions": RESPONSE_METRIC_DEFINITIONS,
        "overall": _duration_metrics(rows),
        "per_service": per_service,
        "per_priority": per_priority,
        "weekly_trend": weekly_trend,
    }


def render_noise_csv(report: dict[str, Any]) -> bytes:
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow(["OpsMender noise analytics"])
    writer.writerow(["from", report["from_at"]])
    writer.writerow(["to", report["to_at"]])
    writer.writerow(["service_id", report["service_id"] or ""])
    writer.writerow([])
    writer.writerow(["metric", "value"])
    for key in (
        "inbound_alerts",
        "incidents_created",
        "noise_reduction_ratio",
        "grouped_alert_savings",
        "flapping_incident_count",
    ):
        writer.writerow([key, report[key]])
    for action, count in report["dedup_breakdown"].items():
        writer.writerow([f"dedup_breakdown.{action}", count])
    writer.writerow([])
    writer.writerow(
        [
            "service_id",
            "service_name",
            "inbound_alerts",
            "incidents_created",
            "alerts_per_created_incident",
        ]
    )
    for row in report["top_noisy_services"]:
        writer.writerow(
            [
                row["service_id"] or "",
                row["service_name"],
                row["inbound_alerts"],
                row["incidents_created"],
                row["alerts_per_created_incident"],
            ]
        )
    writer.writerow([])
    writer.writerow(["hour_utc", "alerts"])
    for row in report["alerts_by_hour_utc"]:
        writer.writerow([row["hour"], row["alerts"]])
    return out.getvalue().encode("utf-8")


def render_response_csv(report: dict[str, Any]) -> bytes:
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow(["OpsMender response analytics"])
    writer.writerow(["from", report["from_at"]])
    writer.writerow(["to", report["to_at"]])
    writer.writerow(["service_id", report["service_id"] or ""])
    writer.writerow([])
    writer.writerow(["metric", "value"])
    for key, value in report["overall"].items():
        writer.writerow([f"overall.{key}", value if value is not None else ""])
    writer.writerow([])
    writer.writerow(
        [
            "service_id",
            "service_name",
            "incident_count",
            "acknowledged_count",
            "resolved_count",
            "mtta_seconds",
            "mttr_seconds",
        ]
    )
    for row in report["per_service"]:
        writer.writerow(
            [
                row["service_id"] or "",
                row["service_name"],
                row["incident_count"],
                row["acknowledged_count"],
                row["resolved_count"],
                row["mtta_seconds"] if row["mtta_seconds"] is not None else "",
                row["mttr_seconds"] if row["mttr_seconds"] is not None else "",
            ]
        )
    writer.writerow([])
    writer.writerow(
        [
            "priority",
            "incident_count",
            "acknowledged_count",
            "resolved_count",
            "mtta_seconds",
            "mttr_seconds",
        ]
    )
    for row in report["per_priority"]:
        writer.writerow(
            [
                row["priority"],
                row["incident_count"],
                row["acknowledged_count"],
                row["resolved_count"],
                row["mtta_seconds"] if row["mtta_seconds"] is not None else "",
                row["mttr_seconds"] if row["mttr_seconds"] is not None else "",
            ]
        )
    writer.writerow([])
    writer.writerow(
        [
            "week_start",
            "incident_count",
            "acknowledged_count",
            "resolved_count",
            "mtta_seconds",
            "mttr_seconds",
        ]
    )
    for row in report["weekly_trend"]:
        writer.writerow(
            [
                row["week_start"],
                row["incident_count"],
                row["acknowledged_count"],
                row["resolved_count"],
                row["mtta_seconds"] if row["mtta_seconds"] is not None else "",
                row["mttr_seconds"] if row["mttr_seconds"] is not None else "",
            ]
        )
    return out.getvalue().encode("utf-8")
