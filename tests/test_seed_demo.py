from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import pytest

from backend.config_loader import set_env_path
from backend.db.models import ApprovalRequest, AuditEntry, Incident, Service
from backend.db.models import Session as SessionModel
from scripts import seed_demo


@pytest.mark.asyncio
async def test_seed_demo_services_have_mcp_allowlists(tmp_path, monkeypatch):
    db_file = tmp_path / "demo.db"
    env_file = tmp_path / "empty.env"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.setenv(
        "OPSMENDER_DATABASE_URL",
        f"sqlite+aiosqlite:///{db_file.as_posix()}",
    )
    monkeypatch.setenv("OPSMENDER_BOOTSTRAP_ADMIN_EMAIL", "admin@example.test")
    monkeypatch.setenv("OPSMENDER_BOOTSTRAP_ADMIN_PASSWORD", "DemoSeed123!")

    set_env_path(env_file)
    try:
        await seed_demo.main()
    finally:
        set_env_path(None)

    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_file.as_posix()}",
        echo=False,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            service_rows = (
                await db.execute(
                    select(Service.slug, Service.mcp_server_ids).order_by(Service.slug)
                )
            ).all()
            scenario_rows = (
                await db.execute(
                    select(Incident, SessionModel)
                    .join(SessionModel, SessionModel.incident_id == Incident.id)
                    .where(
                        Incident.external_id.in_(
                            ["demo-tier-0", "demo-tier-1", "demo-tier-2"]
                        )
                    )
                )
            ).all()
            audit_rows = (
                (
                    await db.execute(
                        select(AuditEntry).where(
                            AuditEntry.session_id.in_(
                                [session.id for _, session in scenario_rows]
                            )
                        )
                    )
                )
                .scalars()
                .all()
            )
            approvals = (
                (
                    await db.execute(
                        select(ApprovalRequest).where(
                            ApprovalRequest.session_id.in_(
                                [session.id for _, session in scenario_rows]
                            )
                        )
                    )
                )
                .scalars()
                .all()
            )
    finally:
        await engine.dispose()

    expected_slugs = {
        "api-gateway",
        "auth-service",
        "checkout-api",
        "payments-db",
        "ingest-pipeline",
    }
    allowlists = {
        slug: mcp_ids for slug, mcp_ids in service_rows if slug in expected_slugs
    }

    assert set(allowlists) == expected_slugs
    assert all(allowlists[slug] for slug in expected_slugs)
    assert len(allowlists["checkout-api"]) >= 2

    scenarios = {
        session.tier: (incident, session) for incident, session in scenario_rows
    }
    assert set(scenarios) == {0, 1, 2}

    events_by_tier = {
        tier: [row for row in audit_rows if row.session_id == session.id]
        for tier, (_, session) in scenarios.items()
    }
    assert all(len(events) >= 8 for events in events_by_tier.values())
    assert all(
        any(event.entry_type == "node_transition" for event in events)
        for events in events_by_tier.values()
    )

    tier0_incident, tier0_session = scenarios[0]
    assert tier0_incident.status == "resolved"
    assert tier0_session.status == "completed"
    assert tier0_session.ended_at is not None
    assert "Autonomously" in (tier0_session.summary or "")
    tier0_writes = [
        event
        for event in events_by_tier[0]
        if event.tool_name == "kubectl_rollout_restart"
    ]
    assert len(tier0_writes) == 1
    assert tier0_writes[0].entry_type == "tool_call_end"
    assert tier0_writes[0].permitted is True
    assert tier0_writes[0].result["decision"] == "autonomous"
    assert tier0_writes[0].result["compensating_inverse"] == "kubectl_rollout_undo"

    tier1_incident, tier1_session = scenarios[1]
    assert tier1_incident.status == "in_progress"
    assert tier1_session.status == "awaiting_approval"
    tier1_write_events = [
        event
        for event in events_by_tier[1]
        if event.tool_name == "kubectl_rollout_restart"
    ]
    assert len(tier1_write_events) == 1
    assert tier1_write_events[0].entry_type == "tool_call_blocked"
    assert tier1_write_events[0].permitted is False
    assert tier1_write_events[0].result["requires_approval"] is True
    assert not any(
        event.entry_type == "tool_call_end"
        and event.tool_name == "kubectl_rollout_restart"
        for event in events_by_tier[1]
    )
    assert len(approvals) == 1
    assert approvals[0].session_id == tier1_session.id
    assert approvals[0].status == "pending"

    tier2_incident, tier2_session = scenarios[2]
    assert tier2_incident.status == "in_progress"
    assert tier2_session.status == "completed"
    assert "Advised only" in (tier2_session.summary or "")
    tier2_tools = [event for event in events_by_tier[2] if event.tool_name]
    assert {event.tool_name for event in tier2_tools} == {
        "postgres_select_query",
        "postgres_explain_query",
    }
    assert all(event.entry_type == "tool_call_end" for event in tier2_tools)
    assert all(event.permitted is True for event in tier2_tools)
