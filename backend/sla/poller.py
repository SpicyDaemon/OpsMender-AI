"""Background SLA target poller."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from backend.db.repos import MaintenanceWindowRepo, SLATargetRepo, UptimeSampleRepo

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
            targets = await SLATargetRepo.list_all(db, active_only=True)

        for target in targets:
            if target.id in self._running_probes:
                continue
            self._running_probes.add(target.id)
            asyncio.create_task(self._probe_and_record(target))

    async def _probe_and_record(self, target: SLATarget) -> None:
        try:
            up, latency_ms = await self._probe_target(target)
            
            async with self._session_factory() as db:
                # Check for maintenance windows
                now = datetime.now(timezone.utc)
                windows = await MaintenanceWindowRepo.list_active_at(db, now)
                
                suppressed = False
                for w in windows:
                    target_ids = w.target_ids or []
                    if str(target.id) in target_ids or "*" in target_ids:
                        suppressed = True
                        break

                await UptimeSampleRepo.create(
                    db,
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
                    up = (resp.status_code == expected_status)
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
