"""Seed orchestration-queue test data so an admin can exercise the queue UI.

Populates the AI Agent -> Orchestration page with running + queued sessions and
(optionally) a model concurrency cap so the capacity bars and the priority queue
have something to show. Everything it creates is tagged with a recognizable
title prefix so a follow-up ``--clean`` run removes exactly this demo data and
nothing else.

This talks to whatever database your server is configured to use
(``OPSMENDER_DATABASE_URL`` / config). IMPORTANT: it must reach the SAME
database the running server uses. If the app runs in Docker, the configured DB
host is usually a compose service name that does not resolve from your host
shell — run the script INSIDE the container, or point it at the published port.

Usage (PowerShell):
    # A) Run inside the backend container (DB host resolves there):
    docker compose exec <backend-service> uv run python scripts/seed_queue_demo.py

    # B) Or target the host-published Postgres port directly:
    uv run python scripts/seed_queue_demo.py --database-url `
        "postgresql+asyncpg://<user>:<pass>@localhost:5432/<db>"

    # Add --clean to either form to remove the demo data and restore caps.

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
from urllib.parse import urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("OPSMENDER_DEPLOYMENT_MODE", "development")

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config_loader import AppConfig
from backend.db.engine import resolve_database_url
from backend.db.models import (
    Base,
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
        incident_ids = list(
            (
                await db.execute(
                    select(Incident.id).where(
                        Incident.title.like(f"{TITLE_PREFIX}%")
                    )
                )
            ).scalars()
        )
        session_ids = list(
            (
                await db.execute(
                    select(SessionModel.id).where(
                        SessionModel.incident_id.in_(incident_ids)
                    )
                )
            ).scalars()
        ) if incident_ids else []

        # Bulk-delete child-first via table metadata (dialect-agnostic, and it
        # avoids the ORM relationship cascade — a force-started demo session may
        # have written audit_entries whose session_id is NOT NULL, which a
        # cascade would try to null and fail on). Tables that reference a session
        # go first, then tables that reference an incident, then the incidents.
        if session_ids:
            for table in reversed(Base.metadata.sorted_tables):
                if "session_id" in table.c:
                    await db.execute(
                        table.delete().where(table.c.session_id.in_(session_ids))
                    )
        if incident_ids:
            for table in reversed(Base.metadata.sorted_tables):
                if table.name == "incidents":
                    continue
                if "incident_id" in table.c:
                    await db.execute(
                        table.delete().where(
                            table.c.incident_id.in_(incident_ids)
                        )
                    )
            incidents_table = Incident.__table__
            await db.execute(
                incidents_table.delete().where(
                    incidents_table.c.id.in_(incident_ids)
                )
            )

        # Restore any model cap we may have set.
        for model in (
            await db.execute(
                select(ModelConfig).where(ModelConfig.max_concurrent_sessions == 1)
            )
        ).scalars().all():
            model.max_concurrent_sessions = None
        await db.commit()
        print(
            f"Removed {len(incident_ids)} demo incidents and "
            f"{len(session_ids)} sessions; restored model caps to unlimited."
        )


def _mask(url: str) -> str:
    """Show scheme + host:port + db, hiding any password."""
    try:
        parsed = urlparse(url)
        host = parsed.hostname or "?"
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme}://***@{host}{port}{parsed.path}"
    except Exception:  # noqa: BLE001
        return url


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--clean", action="store_true", help="Remove previously seeded demo data."
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="Override the DB URL (e.g. when the configured host isn't reachable "
        "from this shell). Otherwise OPSMENDER_DATABASE_URL / config is used.",
    )
    args = parser.parse_args()

    url = args.database_url or resolve_database_url(AppConfig.load().db)
    print(f"Target database: {_mask(url)}")
    engine = create_async_engine(url, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        if args.clean:
            await clean(factory)
        else:
            await seed(factory)
    except OSError as exc:
        print(
            f"\nCould not reach the database ({exc}).\n"
            "The configured DB host doesn't resolve/connect from this shell. "
            "If the app runs in Docker, either run this INSIDE the backend "
            "container (`docker compose exec <service> uv run python "
            "scripts/seed_queue_demo.py`) or pass --database-url pointing at the "
            "host-published port (e.g. postgresql+asyncpg://user:pass@localhost:5432/db)."
        )
        raise SystemExit(1) from exc
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
