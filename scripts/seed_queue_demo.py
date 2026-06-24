"""Seed orchestration-queue test data so an admin can exercise the queue UI.

Populates the AI Agent -> Orchestration page with running + queued sessions and
(optionally) a model concurrency cap so the capacity bars and the priority queue
have something to show. Everything it creates is tagged with a recognizable
title prefix so a follow-up ``--clean`` run removes exactly this demo data and
nothing else.

This talks to whatever database your server is configured to use
(``OPSMENDER_DATABASE_URL`` / config), so run it on the same host/env as the
backend.

Usage (PowerShell):
    $env:OPSMENDER_DATABASE_URL = "<your db url>"   # if not already set
    uv run python scripts/seed_queue_demo.py            # seed
    uv run python scripts/seed_queue_demo.py --clean    # remove the demo data

Notes:
- It does NOT start real AI work; the "active" rows are placeholders so the
  capacity bars render. Use them to test Purge / Cancel / Move / Force-start.
- It sets the first active model's ``max_concurrent_sessions`` to 1 only if it is
  currently unlimited, and ``--clean`` restores it to unlimited.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("OPSMENDER_DEPLOYMENT_MODE", "development")

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config_loader import AppConfig
from backend.db.engine import resolve_database_url
from backend.db.models import (
    Incident,
    ModelConfig,
    Organization,
    Service,
    Session as SessionModel,
)

TITLE_PREFIX = "[queue-demo]"
# (priority, status, queue_reason). status "active" fills capacity; "queued"
# populates the priority queue. Reasons mirror the real admission paths.
QUEUE_PLAN = [
    ("P0", "active", None),
    ("P1", "active", None),
    ("P0", "queued", "model_at_capacity"),
    ("P1", "queued", "model_at_capacity"),
    ("P2", "queued", "model_at_capacity"),
    ("P3", "queued", "awaiting_capacity"),
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _first(session, stmt):
    return (await session.execute(stmt)).scalars().first()


async def seed(factory) -> None:
    async with factory() as db:
        org = await _first(db, select(Organization).order_by(Organization.created_at))
        if org is None:
            print("No organization found — create one through the app first.")
            return
        service = await _first(
            db, select(Service).where(Service.org_id == org.id)
        )
        model = await _first(
            db,
            select(ModelConfig).where(
                ModelConfig.org_id == org.id, ModelConfig.is_active.is_(True)
            ),
        )
        if model is not None and not model.max_concurrent_sessions:
            model.max_concurrent_sessions = 1
            print(
                f"Set model '{model.name}' max_concurrent_sessions = 1 "
                "(so sessions queue)."
            )

        now = _utcnow()
        created = 0
        for idx, (priority, status, reason) in enumerate(QUEUE_PLAN):
            incident = Incident(
                org_id=org.id,
                title=f"{TITLE_PREFIX} {priority} sample #{idx + 1}",
                description="Synthetic incident for orchestration-queue testing.",
                status="open",
                severity="high" if priority in {"P0", "P1"} else "medium",
                priority=priority,
                service_id=service.id if service is not None else None,
            )
            db.add(incident)
            await db.flush()

            session = SessionModel(
                org_id=org.id,
                incident_id=incident.id,
                model_config_id=model.id if model is not None else None,
                tier=0,
                status=status,
            )
            if status == "queued":
                session.queued_at = now - timedelta(minutes=idx)
                session.queue_expires_at = now + timedelta(minutes=30)
                session.queue_reason = reason
            db.add(session)
            created += 1

        await db.commit()
        print(
            f"Seeded {created} sessions "
            f"({sum(1 for _, s, _ in QUEUE_PLAN if s == 'queued')} queued, "
            f"{sum(1 for _, s, _ in QUEUE_PLAN if s == 'active')} active) "
            f"for org '{org.name}'."
        )
        print("Open AI Agent -> Orchestration to test Purge / Cancel / Move / Force-start.")


async def clean(factory) -> None:
    async with factory() as db:
        incidents = (
            await db.execute(
                select(Incident).where(Incident.title.like(f"{TITLE_PREFIX}%"))
            )
        ).scalars().all()
        removed = 0
        for incident in incidents:
            sessions = (
                await db.execute(
                    select(SessionModel).where(
                        SessionModel.incident_id == incident.id
                    )
                )
            ).scalars().all()
            for session in sessions:
                await db.delete(session)
                removed += 1
            await db.delete(incident)
        # Restore any model cap we may have set.
        models = (
            await db.execute(
                select(ModelConfig).where(ModelConfig.max_concurrent_sessions == 1)
            )
        ).scalars().all()
        for model in models:
            model.max_concurrent_sessions = None
        await db.commit()
        print(
            f"Removed {len(incidents)} demo incidents and {removed} sessions; "
            "restored model caps to unlimited."
        )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--clean", action="store_true", help="Remove previously seeded demo data."
    )
    args = parser.parse_args()

    config = AppConfig.load()
    engine = create_async_engine(resolve_database_url(config.db), echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        if args.clean:
            await clean(factory)
        else:
            await seed(factory)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
