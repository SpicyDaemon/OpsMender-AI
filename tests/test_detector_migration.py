"""Tests for the Detector → Audit Schedule migration (Sprint 39 step 3)."""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.auditor.detector_migration import (
    _focus_areas_from_prompt,
    apply_migrations,
    plan_migrations,
)
from backend.db.models import (
    Base,
    MCPServer,
    Organization,
)
from backend.db.repos import AuditScheduleRepo


TEST_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000777")


@pytest.fixture
async def db_session(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path}/migrate.db", echo=False
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_create_legacy_detector_tables)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(Organization(id=TEST_ORG_ID, name="Org", slug="org"))
        await session.commit()
    async with factory() as session:
        yield session
    await engine.dispose()


def _create_legacy_detector_tables(conn):
    metadata = sa.MetaData()
    sa.Table(
        "detector_rules",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("mcp_server_id", sa.Uuid(), nullable=False),
        sa.Column("prompt_template", sa.Text(), nullable=False),
        sa.Column("model_config_id", sa.Uuid(), nullable=True),
        sa.Column("interval_seconds", sa.Integer(), nullable=False),
        sa.Column("severity_default", sa.String(20), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
    )
    metadata.create_all(conn)


async def _seed_mcp_server(db, name="prod-k8s") -> MCPServer:
    server = MCPServer(
        org_id=TEST_ORG_ID,
        name=name,
        transport="stdio",
        command="kubectl",
        args=[],
        env_vars={},
    )
    db.add(server)
    await db.flush()
    return server


async def _seed_detector_rule(
    db,
    *,
    mcp_server_id,
    name="check-pods",
    prompt="Check for crashlooping pods.\n- pods\n- deployments",
    interval_seconds=300,
    is_active=True,
):
    rule_id = uuid.uuid4()
    await db.execute(
        sa.text(
            """
            INSERT INTO detector_rules (
                id, org_id, name, mcp_server_id, prompt_template,
                interval_seconds, severity_default, is_active
            )
            VALUES (
                :id, :org_id, :name, :mcp_server_id, :prompt_template,
                :interval_seconds, :severity_default, :is_active
            )
            """
        ),
        {
            "id": str(rule_id),
            "org_id": str(TEST_ORG_ID),
            "name": name,
            "mcp_server_id": str(mcp_server_id),
            "prompt_template": prompt,
            "interval_seconds": interval_seconds,
            "severity_default": "medium",
            "is_active": is_active,
        },
    )
    await db.flush()
    return rule_id


class TestFocusAreasFromPrompt:
    def test_extracts_first_sentence(self):
        result = _focus_areas_from_prompt(
            "Check for crashlooping pods. Then look at restarts."
        )
        assert result[0].startswith("Check for crashlooping pods")

    def test_extracts_bullets(self):
        result = _focus_areas_from_prompt(
            "Audit the cluster\n- pods\n- deployments\n- services\n- extra"
        )
        # First sentence + up to 3 bullets.
        assert "pods" in result
        assert "deployments" in result
        assert "services" in result
        assert "extra" not in result

    def test_empty_prompt_returns_empty(self):
        assert _focus_areas_from_prompt("") == []

    def test_truncates_long_text(self):
        long = "x" * 200
        result = _focus_areas_from_prompt(long)
        assert all(len(item) <= 80 for item in result)


class TestPlanMigrations:
    async def test_no_rules_returns_empty(self, db_session):
        assert await plan_migrations(db_session) == []

    async def test_happy_path_resolves_mcp_server_name(self, db_session):
        server = await _seed_mcp_server(db_session)
        await _seed_detector_rule(db_session, mcp_server_id=server.id)
        await db_session.commit()

        plans = await plan_migrations(db_session)
        assert len(plans) == 1
        plan = plans[0]
        assert plan.name == "check-pods"
        assert plan.mcp_server_name == "prod-k8s"
        assert plan.skip_reason is None
        # Detector default is 300s = 5 min, which clamps up to the
        # audit-schedule 15-minute floor.
        assert plan.interval_minutes == 15
        assert plan.is_active is True
        assert "pods" in plan.focus_areas

    async def test_enforces_15_minute_floor(self, db_session):
        server = await _seed_mcp_server(db_session)
        await _seed_detector_rule(
            db_session,
            mcp_server_id=server.id,
            name="fast",
            interval_seconds=60,  # 1 min — should clamp up to 15
        )
        await db_session.commit()
        plans = await plan_migrations(db_session)
        assert plans[0].interval_minutes == 15

    async def test_missing_mcp_server_blocks_migration(self, db_session):
        # Reference an MCP server id that doesn't exist.
        ghost = uuid.uuid4()
        # Create the server first so the FK accepts the rule, then delete it.
        server = await _seed_mcp_server(db_session, name="will-be-gone")
        await _seed_detector_rule(db_session, mcp_server_id=server.id)
        await db_session.commit()
        # Hard-delete the MCP server, leaving the rule orphaned (the
        # detector rule has cascade-on-delete in production; we patch
        # that here by clearing the FK manually for the test).
        await db_session.execute(
            sa.text("UPDATE detector_rules SET mcp_server_id = :gid"),
            {"gid": str(ghost)},
        )
        await db_session.commit()

        plans = await plan_migrations(db_session)
        assert len(plans) == 1
        assert plans[0].skip_reason is not None
        assert "MCP server" in plans[0].skip_reason


class TestApplyMigrations:
    async def test_creates_audit_schedules(self, db_session):
        server = await _seed_mcp_server(db_session)
        await _seed_detector_rule(db_session, mcp_server_id=server.id)
        await db_session.commit()

        plans = await plan_migrations(db_session)
        created, skipped = await apply_migrations(db_session, plans)
        await db_session.commit()

        assert created == 1
        assert skipped == 0

        schedules = await AuditScheduleRepo.list_for_org(db_session, TEST_ORG_ID)
        assert len(schedules) == 1
        assert schedules[0].name == "check-pods"
        assert schedules[0].analyzers == ["environment-scan"]
        assert schedules[0].mcp_server_name == "prod-k8s"

    async def test_skips_blocked_plans(self, db_session):
        server = await _seed_mcp_server(db_session)
        await _seed_detector_rule(db_session, mcp_server_id=server.id)
        await db_session.commit()
        await db_session.execute(
            sa.text("UPDATE detector_rules SET mcp_server_id = :gid"),
            {"gid": str(uuid.uuid4())},
        )
        await db_session.commit()

        plans = await plan_migrations(db_session)
        created, skipped = await apply_migrations(db_session, plans)
        assert created == 0
        assert skipped == 1

    async def test_skips_collisions_by_name(self, db_session):
        server = await _seed_mcp_server(db_session)
        await _seed_detector_rule(db_session, mcp_server_id=server.id)
        await db_session.commit()

        # Pre-create an audit schedule that collides on (org_id, name).
        from datetime import datetime, timezone

        await AuditScheduleRepo.create(
            db_session,
            TEST_ORG_ID,
            name="check-pods",
            analyzers=["environment-scan"],
            interval_minutes=60,
            next_run_at=datetime.now(timezone.utc),
        )
        await db_session.commit()

        plans = await plan_migrations(db_session)
        created, skipped = await apply_migrations(db_session, plans)
        await db_session.commit()
        assert created == 0
        assert skipped == 1
        # Still only one schedule with that name.
        schedules = await AuditScheduleRepo.list_for_org(db_session, TEST_ORG_ID)
        assert len([s for s in schedules if s.name == "check-pods"]) == 1
