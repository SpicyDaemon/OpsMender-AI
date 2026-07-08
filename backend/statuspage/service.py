"""Status Page status derivation and presentation helpers."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Sequence
import uuid

from backend.db.models import Incident, MaintenanceWindow, UptimeSample1h


STATUS_LADDER = [
    "operational",
    "maintenance",
    "degraded",
    "partial_outage",
    "major_outage",
]
STATUS_RANK = {name: idx for idx, name in enumerate(STATUS_LADDER)}
ACTIVE_UPDATE_STATES = {"investigating", "identified", "monitoring"}
PRIORITY_STATUS = {
    "P0": "major_outage",
    "P1": "partial_outage",
    "P2": "degraded",
    "P3": "degraded",
}


@dataclass(frozen=True)
class PublishedIncidentState:
    incident_id: uuid.UUID
    latest_state: str


@dataclass(frozen=True)
class UptimeDay:
    date: str
    pct: float


def _worst(left: str, right: str) -> str:
    return left if STATUS_RANK[left] >= STATUS_RANK[right] else right


def overall_status(component_statuses: Iterable[str]) -> str:
    status = "operational"
    for component_status in component_statuses:
        status = _worst(status, component_status)
    return status


def maintenance_covers_service(
    window: MaintenanceWindow,
    service_id: uuid.UUID,
) -> bool:
    if window.scope_type == "global":
        return True
    if window.scope_type != "service":
        return False
    return service_id in window.scope_ids


def component_status(
    *,
    service_id: uuid.UUID,
    incidents: Sequence[Incident],
    published_states: dict[uuid.UUID, PublishedIncidentState],
    active_windows: Sequence[MaintenanceWindow],
) -> str:
    status = "operational"
    if any(maintenance_covers_service(window, service_id) for window in active_windows):
        status = _worst(status, "maintenance")

    for incident in incidents:
        if incident.service_id != service_id:
            continue
        if incident.status not in {"open", "in_progress"}:
            continue
        state = published_states.get(incident.id)
        if state is None or state.latest_state not in ACTIVE_UPDATE_STATES:
            continue
        status = _worst(
            status,
            PRIORITY_STATUS.get((incident.priority or "").upper(), "degraded"),
        )
    return status


def latest_published_states(updates) -> dict[uuid.UUID, PublishedIncidentState]:
    latest: dict[uuid.UUID, PublishedIncidentState] = {}
    for update in updates:
        current = latest.get(update.incident_id)
        if current is None:
            latest[update.incident_id] = PublishedIncidentState(
                incident_id=update.incident_id,
                latest_state=update.state,
            )
    return latest


def aggregate_90d_uptime(
    *,
    target_to_service: dict[uuid.UUID, uuid.UUID],
    samples_by_target: dict[uuid.UUID, Sequence[UptimeSample1h]],
    now: datetime | None = None,
) -> dict[uuid.UUID, list[UptimeDay]]:
    """Return daily 90-day uptime bars keyed by service id.

    Bars appear only for services whose active SLO target has at least one
    hourly uptime sample. Multiple linked SLO targets are averaged per day.
    """

    now = now or datetime.now(timezone.utc)
    today = now.date()
    start = today - timedelta(days=89)
    daily: dict[uuid.UUID, dict[date, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for target_id, samples in samples_by_target.items():
        service_id = target_to_service.get(target_id)
        if service_id is None:
            continue
        for sample in samples:
            day = sample.bucket_start.date()
            if start <= day <= today:
                daily[service_id][day].append(float(sample.up_pct) * 100.0)

    result: dict[uuid.UUID, list[UptimeDay]] = {}
    for service_id, by_day in daily.items():
        if not by_day:
            continue
        days: list[UptimeDay] = []
        for offset in range(90):
            day = start + timedelta(days=offset)
            values = by_day.get(day)
            if not values:
                continue
            pct = round(sum(values) / len(values), 3)
            days.append(UptimeDay(date=day.isoformat(), pct=pct))
        if days:
            result[service_id] = days
    return result
