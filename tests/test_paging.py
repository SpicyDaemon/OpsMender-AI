"""Tests for the Paging foundation (Sprint 33).

Coverage: pure algorithm tests (on_call_at + assign_priority + rule_matches),
repository smoke tests, and full API tests through the FastAPI app.
"""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.app import create_app
from backend.api.deps import get_db, set_mcp_pool, set_session_factory
from backend.config_loader import set_env_path
from backend.db.models import Base, Organization
from backend.db.repos import (
    IncidentAssignmentRepo,
    IncidentRepo,
    OrganizationRepo,
    PriorityRuleRepo,
    RosterOverrideRepo,
    RosterRepo,
    ServiceRepo,
    TeamRepo,
)
from backend.paging.on_call import (
    OnCallContext,
    OnCallMember,
    OnCallOverride,
    on_call_at,
)
from backend.paging.priority import (
    DEFAULT_MODE_FOR,
    PriorityRuleLike,
    assign_priority,
    rule_matches,
)


TEST_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


@pytest.fixture
async def app(tmp_path):
    db_path = tmp_path / "paging-test.db"
    database_url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(
            Organization(id=TEST_ORG_ID, name="Test Org", slug="test-org")
        )
        await session.commit()
    set_session_factory(factory)

    tmp_env = tmp_path / ".env"
    tmp_env.write_text(
        "OPSMENDER_TIER=2\n"
        "OPSMENDER_LOG_LEVEL=INFO\n"
        "OPSMENDER_AUDIT_LOG=./logs/audit.jsonl\n"
        "OPSMENDER_JWT_SECRET=test-secret\n"
        f"OPSMENDER_DATABASE_URL={database_url}\n"
        f"OPSMENDER_MCP_SERVERS_JSON={json.dumps([])}\n"
    )
    set_env_path(tmp_env)

    application = create_app()
    application.state.session_factory = factory

    class _FakePool:
        async def get_server(self, org_id, name):
            return object()

        @asynccontextmanager
        async def connect(self, org_id, name):
            class _S:
                pass

            yield _S()

    set_mcp_pool(_FakePool())

    async def _get_db():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    application.dependency_overrides[get_db] = _get_db
    yield application

    set_env_path(None)
    await engine.dispose()


@pytest.fixture
async def client(app):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest.fixture
async def auth_headers(client: AsyncClient) -> dict[str, str]:
    await client.post(
        "/auth/register",
        json={
            "username": "paging-admin",
            "email": "paging-admin@test.com",
            "password": "securepass123",
        },
    )
    resp = await client.post(
        "/auth/login",
        json={"username": "paging-admin", "password": "securepass123"},
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


# ---------------------------------------------------------------------------
# Pure on_call_at
# ---------------------------------------------------------------------------


class TestOnCallAt:
    def _ctx(self, users, **kwargs):
        return OnCallContext(
            members=[
                OnCallMember(user_id=u, position_index=i)
                for i, u in enumerate(users)
            ],
            anchor_date=kwargs.get("anchor_date", date(2026, 5, 4)),
            pattern=kwargs.get("pattern", "weekly"),
            pattern_length=kwargs.get("pattern_length", 7),
            handoff_time=kwargs.get("handoff_time", "09:00"),
            time_zone=kwargs.get("time_zone", "UTC"),
        )

    def test_weekly_rotation(self):
        u1, u2, u3 = (uuid.uuid4() for _ in range(3))
        ctx = self._ctx([u1, u2, u3])
        assert on_call_at(
            ctx, datetime(2026, 5, 5, 12, tzinfo=timezone.utc)
        ) == u1
        assert on_call_at(
            ctx, datetime(2026, 5, 12, 12, tzinfo=timezone.utc)
        ) == u2
        assert on_call_at(
            ctx, datetime(2026, 5, 19, 12, tzinfo=timezone.utc)
        ) == u3
        # Wraps after three weeks.
        assert on_call_at(
            ctx, datetime(2026, 5, 26, 12, tzinfo=timezone.utc)
        ) == u1

    def test_daily_rotation(self):
        u1, u2 = uuid.uuid4(), uuid.uuid4()
        ctx = self._ctx([u1, u2], pattern="daily", pattern_length=1)
        assert on_call_at(
            ctx, datetime(2026, 5, 4, 12, tzinfo=timezone.utc)
        ) == u1
        assert on_call_at(
            ctx, datetime(2026, 5, 5, 12, tzinfo=timezone.utc)
        ) == u2

    def test_custom_n_days_rotation(self):
        u1, u2, u3 = (uuid.uuid4() for _ in range(3))
        ctx = self._ctx(
            [u1, u2, u3], pattern="custom_n_days", pattern_length=3
        )
        # Day 0–2 → u1, day 3–5 → u2, day 6–8 → u3
        assert on_call_at(
            ctx, datetime(2026, 5, 5, 12, tzinfo=timezone.utc)
        ) == u1
        assert on_call_at(
            ctx, datetime(2026, 5, 7, 12, tzinfo=timezone.utc)
        ) == u2
        assert on_call_at(
            ctx, datetime(2026, 5, 10, 12, tzinfo=timezone.utc)
        ) == u3

    def test_handoff_boundary(self):
        u1, u2 = uuid.uuid4(), uuid.uuid4()
        ctx = self._ctx([u1, u2], pattern="daily", pattern_length=1)
        # 08:00 on day 1 is still the previous shift (anchor day = u1, day1 = u2 normally
        # but 08:00 < 09:00 handoff so we stay on u1).
        assert on_call_at(
            ctx, datetime(2026, 5, 5, 8, 0, tzinfo=timezone.utc)
        ) == u1
        # 10:00 same day flips.
        assert on_call_at(
            ctx, datetime(2026, 5, 5, 10, 0, tzinfo=timezone.utc)
        ) == u2

    def test_override_wins(self):
        u1, u2, u3 = (uuid.uuid4() for _ in range(3))
        ctx = self._ctx([u1, u2])
        ov_ctx = OnCallContext(
            members=ctx.members,
            overrides=[
                OnCallOverride(
                    covering_user_id=u3,
                    starts_at=datetime(2026, 5, 5, 8, tzinfo=timezone.utc),
                    ends_at=datetime(2026, 5, 5, 12, tzinfo=timezone.utc),
                )
            ],
            anchor_date=ctx.anchor_date,
            pattern=ctx.pattern,
            pattern_length=ctx.pattern_length,
            handoff_time=ctx.handoff_time,
            time_zone=ctx.time_zone,
        )
        assert on_call_at(
            ov_ctx, datetime(2026, 5, 5, 10, tzinfo=timezone.utc)
        ) == u3

    def test_empty_roster_returns_none(self):
        ctx = OnCallContext(members=[], anchor_date=date(2026, 5, 4))
        assert (
            on_call_at(ctx, datetime(2026, 5, 5, 10, tzinfo=timezone.utc))
            is None
        )

    def test_timezone_handling(self):
        u1, u2 = uuid.uuid4(), uuid.uuid4()
        ctx = self._ctx(
            [u1, u2],
            pattern="daily",
            pattern_length=1,
            time_zone="America/Chicago",
        )
        # 14:00 UTC on May 5 = 09:00 Chicago — right at handoff. Should be u2.
        t_utc = datetime(2026, 5, 5, 14, 0, tzinfo=timezone.utc)
        assert on_call_at(ctx, t_utc) == u2


# ---------------------------------------------------------------------------
# Pure rule_matches / assign_priority
# ---------------------------------------------------------------------------


class TestPriority:
    def test_rule_matches_list_or(self):
        assert rule_matches({"severity": ["critical", "high"]}, {"severity": "high"})
        assert not rule_matches(
            {"severity": ["critical"]}, {"severity": "warning"}
        )

    def test_rule_matches_case_insensitive(self):
        assert rule_matches(
            {"severity": ["Critical"]}, {"severity": "critical"}
        )

    def test_rule_missing_key_does_not_match(self):
        assert not rule_matches({"missing": ["x"]}, {"other": "x"})

    async def test_first_match_wins(self):
        rules = [
            PriorityRuleLike(
                id="r1",
                name="r1",
                rule_index=0,
                condition={"severity": ["critical"]},
                priority="P0",
                response_mode=None,
                is_active=True,
            ),
            PriorityRuleLike(
                id="r2",
                name="r2",
                rule_index=1,
                condition={"severity": ["critical"]},
                priority="P1",
                response_mode=None,
                is_active=True,
            ),
        ]
        result = await assign_priority({"severity": "critical"}, rules)
        assert result.priority == "P0"
        assert result.matched_rule_id == "r1"
        assert result.response_mode == DEFAULT_MODE_FOR["P0"]

    async def test_fallback_when_no_match(self):
        result = await assign_priority({"severity": "info"}, [])
        assert result.priority == "P3"
        assert result.response_mode == "auto_resolve"

    async def test_inactive_rules_skipped(self):
        rules = [
            PriorityRuleLike(
                id="r1",
                name="r1",
                rule_index=0,
                condition={"severity": ["critical"]},
                priority="P0",
                response_mode=None,
                is_active=False,
            )
        ]
        result = await assign_priority({"severity": "critical"}, rules)
        assert result.priority == "P3"
        assert result.matched_rule_id is None

    async def test_llm_can_escalate_only(self):
        rules = [
            PriorityRuleLike(
                id="r1",
                name="r1",
                rule_index=0,
                condition={"severity": ["warning"]},
                priority="P2",
                response_mode=None,
                is_active=True,
            )
        ]

        async def llm_up(payload, current):
            return "P0", "looks bad"

        async def llm_down(payload, current):
            return "P3", "false alarm"

        up = await assign_priority(
            {"severity": "warning"},
            rules,
            llm_escalation_enabled=True,
            llm_callback=llm_up,
        )
        assert up.priority == "P0"
        assert up.llm_escalated is True
        assert up.llm_reason == "looks bad"

        down = await assign_priority(
            {"severity": "warning"},
            rules,
            llm_escalation_enabled=True,
            llm_callback=llm_down,
        )
        # LLM tried to go from P2 → P3 which is a downgrade — ignored.
        assert down.priority == "P2"
        assert down.llm_escalated is False

    async def test_response_mode_override_on_rule(self):
        rules = [
            PriorityRuleLike(
                id="r1",
                name="r1",
                rule_index=0,
                condition={"severity": ["info"]},
                priority="P3",
                response_mode="notify",
                is_active=True,
            )
        ]
        result = await assign_priority({"severity": "info"}, rules)
        assert result.priority == "P3"
        assert result.response_mode == "notify"


# ---------------------------------------------------------------------------
# Repository smoke tests
# ---------------------------------------------------------------------------


class TestRepos:
    async def test_team_crud_and_members(self, app):
        async with app.state.session_factory() as db:
            team = await TeamRepo.create(
                db, TEST_ORG_ID, name="Platform", slug="platform"
            )
            await db.commit()
            assert team.slug == "platform"
            fetched = await TeamRepo.list_all(db, TEST_ORG_ID)
            assert len(fetched) == 1

    async def test_service_create_under_team(self, app):
        async with app.state.session_factory() as db:
            team = await TeamRepo.create(
                db, TEST_ORG_ID, name="Payments", slug="payments"
            )
            svc = await ServiceRepo.create(
                db,
                TEST_ORG_ID,
                team_id=team.id,
                name="API",
                slug="payments-api",
            )
            await db.commit()
            assert svc.team_id == team.id
            assert (
                await ServiceRepo.get_by_slug(db, TEST_ORG_ID, "payments-api")
            ).id == svc.id

    async def test_roster_reorder_members(self, app):
        async with app.state.session_factory() as db:
            team = await TeamRepo.create(db, TEST_ORG_ID, name="T", slug="t")
            from backend.db.repos import UserRepo

            u1 = await UserRepo.create(
                db,
                username="a@test",
                email="a@test.com",
                password_hash="x",
                role="viewer",
                primary_org_id=TEST_ORG_ID,
            )
            u2 = await UserRepo.create(
                db,
                username="b@test",
                email="b@test.com",
                password_hash="x",
                role="viewer",
                primary_org_id=TEST_ORG_ID,
            )
            roster = await RosterRepo.create(
                db,
                TEST_ORG_ID,
                team_id=team.id,
                name="r",
                anchor_date=date(2026, 5, 4),
            )
            await RosterRepo.add_member(
                db,
                TEST_ORG_ID,
                roster_id=roster.id,
                user_id=u1.id,
                position_index=0,
            )
            await RosterRepo.add_member(
                db,
                TEST_ORG_ID,
                roster_id=roster.id,
                user_id=u2.id,
                position_index=1,
            )
            await db.commit()

            await RosterRepo.reorder_members(
                db, TEST_ORG_ID, roster.id, ordered_user_ids=[u2.id, u1.id]
            )
            await db.commit()
            members = await RosterRepo.list_members(
                db, TEST_ORG_ID, roster.id
            )
            assert [m.user_id for m in members] == [u2.id, u1.id]

    async def test_priority_rule_llm_log(self, app):
        async with app.state.session_factory() as db:
            inc = await IncidentRepo.create(
                db,
                TEST_ORG_ID,
                title="t",
                description="d",
                severity="critical",
            )
            await PriorityRuleRepo.log_llm_override(
                db,
                TEST_ORG_ID,
                incident_id=inc.id,
                rule_priority="P2",
                llm_priority="P0",
                llm_reason="data loss imminent",
            )
            await db.commit()

    async def test_incident_assignment_replaces_active(self, app):
        async with app.state.session_factory() as db:
            from backend.db.repos import UserRepo

            inc = await IncidentRepo.create(
                db, TEST_ORG_ID, title="t", description="d", severity="high"
            )
            u1 = await UserRepo.create(
                db,
                username="x@test",
                email="x@test.com",
                password_hash="x",
                role="viewer",
                primary_org_id=TEST_ORG_ID,
            )
            u2 = await UserRepo.create(
                db,
                username="y@test",
                email="y@test.com",
                password_hash="x",
                role="viewer",
                primary_org_id=TEST_ORG_ID,
            )
            await IncidentAssignmentRepo.assign(
                db, TEST_ORG_ID, incident_id=inc.id, user_id=u1.id
            )
            await IncidentAssignmentRepo.assign(
                db, TEST_ORG_ID, incident_id=inc.id, user_id=u2.id
            )
            await db.commit()
            active = await IncidentAssignmentRepo.get_active(
                db, TEST_ORG_ID, inc.id
            )
            assert active.assigned_to == u2.id
            history = await IncidentAssignmentRepo.list_for_incident(
                db, TEST_ORG_ID, inc.id
            )
            assert len(history) == 2


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


class TestPagingAPI:
    async def test_team_lifecycle(self, client: AsyncClient, auth_headers):
        create = await client.post(
            "/teams",
            json={"name": "Platform", "slug": "platform"},
            headers=auth_headers,
        )
        assert create.status_code == 201
        team_id = create.json()["id"]

        listed = await client.get("/teams", headers=auth_headers)
        assert listed.status_code == 200
        assert listed.json()["total"] == 1

        dup = await client.post(
            "/teams",
            json={"name": "Other", "slug": "platform"},
            headers=auth_headers,
        )
        assert dup.status_code == 409

        deleted = await client.delete(
            f"/teams/{team_id}", headers=auth_headers
        )
        assert deleted.status_code == 204

    async def test_service_requires_existing_team(
        self, client: AsyncClient, auth_headers
    ):
        bogus_team = str(uuid.uuid4())
        resp = await client.post(
            "/services",
            json={
                "team_id": bogus_team,
                "name": "Svc",
                "slug": "svc",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 400

    async def test_roster_member_add_and_on_call(
        self, client: AsyncClient, app, auth_headers
    ):
        team = await client.post(
            "/teams",
            json={"name": "Platform", "slug": "platform"},
            headers=auth_headers,
        )
        team_id = team.json()["id"]
        roster_resp = await client.post(
            "/rosters",
            json={
                "team_id": team_id,
                "name": "Primary",
                "pattern": "daily",
                "pattern_length": 1,
                "anchor_date": "2026-05-04",
                "handoff_time": "00:00",
                "time_zone": "UTC",
            },
            headers=auth_headers,
        )
        assert roster_resp.status_code == 201
        roster_id = roster_resp.json()["id"]

        # Need at least one user to populate roster.
        from backend.db.repos import UserRepo

        async with app.state.session_factory() as db:
            u1 = await UserRepo.create(
                db,
                username="r1@test",
                email="r1@test.com",
                password_hash="x",
                role="viewer",
                primary_org_id=TEST_ORG_ID,
            )
            await db.commit()

        add = await client.post(
            f"/rosters/{roster_id}/members",
            json={"user_id": str(u1.id), "position_index": 0},
            headers=auth_headers,
        )
        assert add.status_code == 201

        from urllib.parse import quote

        at = quote("2026-05-05T12:00:00+00:00", safe="")
        resp = await client.get(
            f"/rosters/{roster_id}/on-call?at={at}", headers=auth_headers
        )
        assert resp.status_code == 200
        assert resp.json()["user_id"] == str(u1.id)

    async def test_priority_rule_crud(self, client: AsyncClient, auth_headers):
        resp = await client.post(
            "/priority-rules",
            json={
                "name": "Critical alerts",
                "rule_index": 0,
                "condition": {"severity": ["critical"]},
                "priority": "P0",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        rule_id = resp.json()["id"]

        listed = await client.get("/priority-rules", headers=auth_headers)
        assert listed.json()["total"] == 1

        deleted = await client.delete(
            f"/priority-rules/{rule_id}", headers=auth_headers
        )
        assert deleted.status_code == 204

    async def test_incident_create_applies_priority_rules(
        self, client: AsyncClient, app, auth_headers
    ):
        # Pre-seed a P0 rule for critical incidents.
        await client.post(
            "/priority-rules",
            json={
                "name": "Critical",
                "condition": {"severity": ["critical"]},
                "priority": "P0",
            },
            headers=auth_headers,
        )

        create = await client.post(
            "/incidents",
            json={
                "title": "API down",
                "description": "503s rising",
                "severity": "critical",
            },
            headers=auth_headers,
        )
        assert create.status_code == 201
        incident_id = create.json()["id"]

        panel = await client.get(
            f"/incidents/{incident_id}/paging", headers=auth_headers
        )
        assert panel.status_code == 200
        body = panel.json()
        assert body["priority"] == "P0"
        assert body["response_mode"] == "page"
        assert body["assignment"] is None

    async def test_incident_assign_and_release(
        self, client: AsyncClient, auth_headers
    ):
        create = await client.post(
            "/incidents",
            json={"title": "x", "description": "y", "severity": "low"},
            headers=auth_headers,
        )
        incident_id = create.json()["id"]

        ack = await client.post(
            f"/incidents/{incident_id}/assign",
            json={},
            headers=auth_headers,
        )
        assert ack.status_code == 200
        body = ack.json()
        assert body["assigned_by"] == "self_ack"

        release = await client.post(
            f"/incidents/{incident_id}/release",
            headers=auth_headers,
        )
        assert release.status_code == 204
