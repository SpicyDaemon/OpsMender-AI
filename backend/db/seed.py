"""Dev seed script — populates a fresh database with sample data.

Usage::

    AIM_DATABASE_URL=postgresql+asyncpg://aim:aim@localhost/aim \
        uv run python -m backend.db.seed

Requires: running Postgres with migrations applied.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

from passlib.hash import bcrypt

from backend.db.engine import get_engine, get_session_factory
from backend.db.models import Base
from backend.db.repos import (
    AuditEntryRepo,
    IncidentRepo,
    ModelConfigRepo,
    SessionRepo,
    UserRepo,
)


async def seed(database_url: str) -> None:
    engine = get_engine(database_url)
    factory = get_session_factory(engine)

    async with factory() as db:
        # -- Users -----------------------------------------------------------
        admin = await UserRepo.create(
            db,
            username="admin",
            email="admin@aim.local",
            password_hash=bcrypt.hash("admin123"),
            role="admin",
        )
        operator = await UserRepo.create(
            db,
            username="operator",
            email="operator@aim.local",
            password_hash=bcrypt.hash("operator123"),
            role="operator",
        )
        viewer = await UserRepo.create(
            db,
            username="viewer",
            email="viewer@aim.local",
            password_hash=bcrypt.hash("viewer123"),
            role="viewer",
        )
        print(f"  Created users: admin, operator, viewer")

        # -- Model configs ---------------------------------------------------
        anthropic_cfg = await ModelConfigRepo.create(
            db,
            name="claude-sonnet",
            provider="anthropic",
            model_id="claude-sonnet-4-20250514",
            api_key_env_var="ANTHROPIC_API_KEY",
            is_default=True,
        )
        openai_cfg = await ModelConfigRepo.create(
            db,
            name="gpt-4o",
            provider="openai",
            model_id="gpt-4o",
            api_key_env_var="OPENAI_API_KEY",
        )
        ollama_cfg = await ModelConfigRepo.create(
            db,
            name="llama3-local",
            provider="ollama",
            model_id="llama3.2",
            base_url="http://localhost:11434",
        )
        print(f"  Created model configs: claude-sonnet (default), gpt-4o, llama3-local")

        # -- Incidents -------------------------------------------------------
        inc1 = await IncidentRepo.create(
            db,
            title="API latency spike in production",
            description="p95 latency jumped from 200ms to 2s on /api/v1/orders endpoint. Started at 14:30 UTC.",
            severity="high",
        )
        inc2 = await IncidentRepo.create(
            db,
            title="OOMKilled pods in staging",
            description="Multiple pods in staging namespace getting OOMKilled. Memory limits may need adjustment.",
            severity="medium",
        )
        print(f"  Created incidents: {inc1.title[:40]}..., {inc2.title[:40]}...")

        # -- Sessions --------------------------------------------------------
        sess1 = await SessionRepo.create(
            db,
            tier=2,
            incident_id=inc1.id,
            model_provider="anthropic",
            model_id="claude-sonnet-4-20250514",
        )
        print(f"  Created session for incident 1 (tier 2)")

        # -- Audit entries ---------------------------------------------------
        now = datetime.now(timezone.utc)
        await AuditEntryRepo.create(
            db,
            session_id=sess1.id,
            tier=2,
            entry_type="session_start",
        )
        await AuditEntryRepo.create(
            db,
            session_id=sess1.id,
            tier=2,
            entry_type="tool_call_start",
            tool_name="get_pod_logs",
            tool_parameters={"namespace": "default", "pod": "api-server-abc"},
        )
        await AuditEntryRepo.create(
            db,
            session_id=sess1.id,
            tier=2,
            entry_type="tool_call_end",
            tool_name="get_pod_logs",
            result={"lines": 120},
            duration_ms=312,
        )
        await AuditEntryRepo.create(
            db,
            session_id=sess1.id,
            tier=2,
            entry_type="tool_call_blocked",
            tool_name="delete_pod",
            tool_parameters={"namespace": "default", "pod": "api-server-abc"},
            permitted=False,
            block_reason="Tier 2 denies destructive operations",
        )
        await AuditEntryRepo.create(
            db,
            session_id=sess1.id,
            tier=2,
            entry_type="session_end",
        )
        print(f"  Created 5 audit entries for session")

        await db.commit()

    await engine.dispose()
    print("\nSeed complete.")


def main() -> None:
    url = os.environ.get(
        "AIM_DATABASE_URL",
        "postgresql+asyncpg://aim:aim@localhost:5432/aim",
    )
    print(f"Seeding database: {url.split('@')[-1]}")
    asyncio.run(seed(url))


if __name__ == "__main__":
    main()
