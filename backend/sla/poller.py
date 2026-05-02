"""Background SLA target poller."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.models import Incident
from backend.db.repos import (
    IncidentRepo,
    MaintenanceWindowRepo,
    SessionRepo,
    SLATargetRepo,
    SLORepo,
    UptimeSampleRepo,
    OrganizationRepo,
)
from backend.ingest.autostart import (
    has_active_session_for_incident,
    load_auto_start_policy,
    should_auto_start_session,
)
from backend.webhooks import schedule_generic_event

if TYPE_CHECKING:
    from backend.config_loader import AppConfig
    from backend.db.models import SLATarget

logger = logging.getLogger(__name__)


class SLAPoller:
    """Background task to probe SLA targets and record UptimeSamples."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        config: AppConfig,
    ) -> None:
        self._session_factory = session_factory
        self._config = config
        self._task: asyncio.Task | None = None
        # Track ongoing probes so we don't spawn duplicates if polling interval < latency
        self._running_probes: set[uuid.UUID] = set()
        self._active_mw_ids: set[uuid.UUID] = set()
        self._violated_slo_ids: set[uuid.UUID] = set()

    async def start(self) -> None:
        if not self._config.sla.poller_enabled:
            logger.info("SLA poller disabled by config")
            return
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._loop(), name="aim-sla-poller")
        logger.info("SLA poller started")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        logger.info("SLA poller stopped")

    async def _loop(self) -> None:
        interval = self._config.sla.poll_interval_default
        while True:
            try:
                await self._tick()
            except Exception as exc:  # noqa: BLE001
                logger.error("SLA poller tick failed: %s", exc)
            await asyncio.sleep(interval)

    async def _tick(self) -> None:
        async with self._session_factory() as db:
            organizations = await OrganizationRepo.list_all(db)

        for org in organizations:
            org_id = org.id
            async with self._session_factory() as db:
                targets = await SLATargetRepo.list_all(db, org_id, active_only=True)
                windows = await MaintenanceWindowRepo.list_active_at(
                    db, org_id, datetime.now(timezone.utc)
                )

                current_mw_ids = {w.id for w in windows}
                started_mws = current_mw_ids - self._active_mw_ids
                ended_mws = self._active_mw_ids - current_mw_ids

                for w in windows:
                    if w.id in started_mws:
                        schedule_generic_event(
                            self._session_factory,
                            task_registry=None,
                            event_type="maintenance_window.started",
                            payload_data={
                                "message": f"Maintenance window started: {w.name}",
                                "maintenance_window": {
                                    "id": str(w.id),
                                    "name": w.name,
                                    "reason": w.reason,
                                },
                            },
                        )

                if ended_mws:
                    for wid in ended_mws:
                        w = await MaintenanceWindowRepo.get_by_id(db, org_id, wid)
                        if w:
                            schedule_generic_event(
                                self._session_factory,
                                task_registry=None,
                                event_type="maintenance_window.ended",
                                payload_data={
                                    "message": f"Maintenance window ended: {w.name}",
                                    "maintenance_window": {
                                        "id": str(w.id),
                                        "name": w.name,
                                        "reason": w.reason,
                                    },
                                },
                            )

                self._active_mw_ids = current_mw_ids

                for target in targets:
                    if target.id in self._running_probes:
                        continue
                    self._running_probes.add(target.id)
                    asyncio.create_task(self._probe_and_record(org_id, target))

                await self._check_slos(org_id)

    async def _check_slos(self, org_id: uuid.UUID) -> None:
        async with self._session_factory() as db:
            slos = await SLORepo.list_all(db, org_id, active_only=True)

            for slo in slos:
                if slo.burn_alert_threshold is None:
                    continue

                now = datetime.now(timezone.utc)
                since = now - timedelta(seconds=slo.window_seconds)

                stats = await UptimeSampleRepo.compute_uptime(
                    db, org_id, slo.target_id, since=since, until=now
                )
                actual_pct = stats["uptime_pct"]
                objective = slo.objective_pct

                error_budget_total = 100.0 - objective
                error_used = 100.0 - actual_pct
                if error_budget_total > 0:
                    burn_rate = error_used / error_budget_total
                else:
                    burn_rate = float("inf") if error_used > 0 else 0.0

                if burn_rate > float(slo.burn_alert_threshold):
                    if slo.id not in self._violated_slo_ids:
                        self._violated_slo_ids.add(slo.id)
                        schedule_generic_event(
                            self._session_factory,
                            task_registry=None,
                            event_type="slo.burn_rate_violated",
                            payload_data={
                                "message": f"SLO burn rate exceeded for {slo.name}",
                                "slo": {
                                    "id": str(slo.id),
                                    "name": slo.name,
                                    "objective_pct": float(slo.objective_pct),
                                    "burn_alert_threshold": float(
                                        slo.burn_alert_threshold
                                    ),
                                    "burn_rate": float(burn_rate),
                                    "actual_pct": float(actual_pct),
                                },
                            },
                        )

                    external_source = f"slo:{slo.id}"
                    external_id = "burn_rate_violation"

                    existing = await IncidentRepo.get_by_external_fingerprint(
                        db,
                        org_id,
                        external_source=external_source,
                        external_id=external_id,
                    )

                    if existing and existing.status not in ("resolved", "closed"):
                        continue

                    dedup_action = "created"
                    if existing:
                        await IncidentRepo.update_status(
                            db, org_id, existing.id, "open"
                        )
                        incident = existing
                        dedup_action = "updated"
                        logger.warning(
                            "Re-opened incident %s for SLO violation: %s",
                            incident.id,
                            slo.name,
                        )
                    else:
                        incident = Incident(
                            org_id=org_id,
                            title=f"SLO Violation: {slo.name}",
                            description=f"SLO {slo.name} has exceeded its burn rate alert threshold.\n\nObjective: {slo.objective_pct}%\nActual: {actual_pct:.2f}%\nBurn Rate: {burn_rate:.2f}x\nThreshold: {slo.burn_alert_threshold}x",
                            severity="high",
                            status="open",
                            external_id=external_id,
                            external_source=external_source,
                            target_id=slo.target_id,
                        )
                        db.add(incident)
                        await db.flush()
                        logger.warning(
                            "Created incident %s for SLO violation: %s",
                            incident.id,
                            slo.name,
                        )

                    policy = await load_auto_start_policy(db, self._config)
                    if should_auto_start_session(
                        incident, dedup_action=dedup_action, policy=policy
                    ):
                        if not await has_active_session_for_incident(db, org_id, incident.id):
                            await SessionRepo.create(
                                db,
                                org_id,
                                tier=policy.session_tier,
                                incident_id=incident.id,
                            )
                else:
                    self._violated_slo_ids.discard(slo.id)
            await db.commit()

    async def _probe_and_record(self, org_id: uuid.UUID, target: SLATarget) -> None:
        try:
            up, latency_ms = await self._probe_target(target)

            async with self._session_factory() as db:
                # Check for maintenance windows
                now = datetime.now(timezone.utc)
                windows = await MaintenanceWindowRepo.list_active_at(db, org_id, now)

                suppressed = False
                for w in windows:
                    target_ids = w.target_ids or []
                    if str(target.id) in target_ids or "*" in target_ids:
                        suppressed = True
                        break

                await UptimeSampleRepo.create(
                    db,
                    org_id,
                    target_id=target.id,
                    up=up,
                    latency_ms=latency_ms,
                    source="poller",
                    suppressed=suppressed,
                )
                await db.commit()
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to probe target %s: %s", target.id, exc)
        finally:
            self._running_probes.discard(target.id)

    async def _probe_target(self, target: SLATarget) -> tuple[bool, int | None]:
        """Return (is_up, latency_ms)."""
        kind = target.kind.lower()
        config = target.config or {}

        start = time.perf_counter()
        up = False

        try:
            if kind == "http":
                url = config.get("url")
                if not url:
                    return False, None
                method = config.get("method", "GET")
                expected_status = config.get("expected_status", 200)
                timeout = config.get("timeout", 10.0)

                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.request(method, url)
                    up = resp.status_code == expected_status
            elif kind == "tcp":
                host = config.get("host")
                port = config.get("port")
                if not host or not port:
                    return False, None
                timeout = config.get("timeout", 10.0)

                async with asyncio.timeout(timeout):
                    reader, writer = await asyncio.open_connection(host, port)
                    writer.close()
                    await writer.wait_closed()
                    up = True
            else:
                # "external" or unknown types aren't actively polled by us
                return False, None
        except Exception as e:  # noqa: BLE001
            logger.debug("Probe failed for %s: %s", target.id, e)
            up = False

        latency_ms = int((time.perf_counter() - start) * 1000)
        return up, latency_ms
