"""Detector → Audit Schedule migration helper (Sprint 39 step 3).

The Detector concept is being retired in favor of the Sprint 32 Auditor.
This module reads the legacy ``detector_rules`` table and proposes
equivalent ``audit_schedules`` rows that the operator can review before
flipping the switch. The mapping is:

============================  =================================
Detector field                Audit schedule field
============================  =================================
``name``                      ``name``
``mcp_server_id`` → name      ``mcp_server_name``
``prompt_template``           ``description`` + ``focus_areas`` (truncated)
``interval_seconds``          ``max(15, interval_seconds // 60)`` minutes
``is_active``                 ``is_active``
``org_id``                    ``org_id``
============================  =================================

Analyzers always default to ``["environment-scan"]`` — the
:class:`EnvironmentScanAnalyzer` is the closest analog to the legacy
detector loop (read-only MCP + LLM verdict).

The CLI wraps this with a dry-run by default; ``--apply`` actually
writes rows.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import MetaData, Table, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db.models import AuditSchedule
from backend.db.repos import AuditScheduleRepo


logger = logging.getLogger(__name__)


async def _reflect_legacy_table(db: AsyncSession, table_name: str) -> Table | None:
    """Reflect a removed legacy table if it still exists in the database."""

    conn = await db.connection()

    def _reflect(sync_conn):
        if not inspect(sync_conn).has_table(table_name):
            return None
        metadata = MetaData()
        return Table(table_name, metadata, autoload_with=sync_conn)

    return await conn.run_sync(_reflect)


def _as_uuid(value) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


@dataclass
class MigrationPlan:
    """One proposed audit_schedules row built from a detector_rules row."""

    detector_rule_id: uuid.UUID
    org_id: uuid.UUID
    name: str
    description: str
    mcp_server_name: str | None
    focus_areas: list[str]
    interval_minutes: int
    is_active: bool
    # Skip reason — set when we can't migrate this row (e.g. unresolvable
    # MCP server). UI prints these so the operator knows which rules
    # need manual attention.
    skip_reason: str | None = None


def _focus_areas_from_prompt(prompt: str) -> list[str]:
    """Extract candidate focus-area strings from a detector prompt.

    Detector prompts tend to be free-form English; we take the first
    sentence and the first three bullet points (if any) and truncate
    each to 80 characters. The resulting list is a hint to the LLM —
    operators can edit it after migration.
    """

    if not prompt:
        return []
    chunks: list[str] = []
    first_line = prompt.strip().splitlines()[0].strip()
    if first_line:
        sentence = re.split(r"(?<=[.!?])\s+", first_line, maxsplit=1)[0]
        chunks.append(sentence[:80])

    bullets = re.findall(r"^[\s]*[-*]\s+(.+)$", prompt, flags=re.MULTILINE)
    for bullet in bullets[:3]:
        cleaned = bullet.strip()[:80]
        if cleaned and cleaned not in chunks:
            chunks.append(cleaned)
    return chunks


async def plan_migrations(db: AsyncSession) -> list[MigrationPlan]:
    """Build a migration plan for every detector rule on the server.

    The function never writes — only reads. Operators inspect the
    output, then run ``apply_migrations`` (or the CLI ``--apply`` flag)
    to actually persist the new audit schedules.
    """

    rules_table = await _reflect_legacy_table(db, "detector_rules")
    if rules_table is None:
        return []

    servers_table = await _reflect_legacy_table(db, "mcp_servers")
    if servers_table is None:
        return []

    rules = (
        await db.execute(select(rules_table).order_by(rules_table.c.name))
    ).mappings().all()
    if not rules:
        return []

    # Resolve MCP server ids → names in one round trip.
    server_ids = {_as_uuid(r["mcp_server_id"]) for r in rules if r["mcp_server_id"]}
    name_by_id: dict[uuid.UUID, str] = {}
    if server_ids:
        rows = (
            await db.execute(
                select(servers_table.c.id, servers_table.c.name).where(
                    servers_table.c.id.in_(server_ids)
                )
            )
        ).all()
        name_by_id = {_as_uuid(row[0]): row[1] for row in rows}

    plans: list[MigrationPlan] = []
    for rule in rules:
        rule_server_id = _as_uuid(rule["mcp_server_id"])
        server_name = name_by_id.get(rule_server_id)
        skip: str | None = None
        if not server_name:
            skip = "MCP server not found (rule references a deleted server)"

        interval_minutes = max(15, (rule["interval_seconds"] or 300) // 60)
        focus_areas = _focus_areas_from_prompt(rule["prompt_template"] or "")

        plans.append(
            MigrationPlan(
                detector_rule_id=_as_uuid(rule["id"]),
                org_id=_as_uuid(rule["org_id"]),
                name=rule["name"],
                description=(
                    (rule["prompt_template"] or "")[:1000]
                    or "Migrated from legacy detector rule"
                ),
                mcp_server_name=server_name,
                focus_areas=focus_areas,
                interval_minutes=interval_minutes,
                is_active=bool(rule["is_active"]),
                skip_reason=skip,
            )
        )
    return plans


async def apply_migrations(
    db: AsyncSession, plans: Iterable[MigrationPlan]
) -> tuple[int, int]:
    """Persist ``audit_schedules`` rows for every applicable plan.

    Returns ``(created, skipped)``. Skipped plans either had a
    ``skip_reason`` set by :func:`plan_migrations` or collided with an
    existing audit schedule by ``(org_id, name)``.
    """

    created = 0
    skipped = 0
    now = datetime.now(timezone.utc)
    for plan in plans:
        if plan.skip_reason is not None:
            skipped += 1
            continue

        # Collision check: if the operator has already created an audit
        # schedule with the same (org_id, name), don't double-up.
        existing_q = select(AuditSchedule).where(
            AuditSchedule.org_id == plan.org_id,
            AuditSchedule.name == plan.name,
        )
        existing = (await db.execute(existing_q)).scalar_one_or_none()
        if existing is not None:
            skipped += 1
            continue

        await AuditScheduleRepo.create(
            db,
            plan.org_id,
            name=plan.name,
            description=plan.description,
            analyzers=["environment-scan"],
            mcp_server_name=plan.mcp_server_name,
            focus_areas=plan.focus_areas,
            interval_minutes=plan.interval_minutes,
            is_active=plan.is_active,
            next_run_at=now,
        )
        created += 1
    return created, skipped
