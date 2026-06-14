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
from backend.api.session_runner import (
    _preferred_mcp_ids_for_incident,
    _resolve_mcp_context,
)
from backend.config_loader import MCPServerConfig, set_env_path
from backend.db.models import Base, Organization
from backend.db.repos import (
    IncidentAssignmentRepo,
    IncidentRepo,
    MCPServerRepo,
    MaintenanceWindowRepo,
    OrganizationRepo,
    PriorityRuleRepo,
    RosterOverrideRepo,
    RosterRepo,
    ServiceRepo,
    TeamRepo,
    UserNotificationPrefRepo,
    UserRepo,
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
        session.add(Organization(id=TEST_ORG_ID, name="Test Org", slug="test-org"))
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
                OnCallMember(user_id=u, position_index=i) for i, u in enumerate(users)
            ],
            anchor_date=kwargs.get("anchor_date", date(2026, 5, 4)),
            pattern=kwargs.get("pattern", "weekly"),
            pattern_length=kwargs.get("pattern_length", 7),
            coverage_start_time=kwargs.get("coverage_start_time", "00:00"),
            coverage_end_time=kwargs.get("coverage_end_time", "00:00"),
            handoff_time=kwargs.get("handoff_time", "09:00"),
            time_zone=kwargs.get("time_zone", "UTC"),
        )

    def test_weekly_rotation(self):
        u1, u2, u3 = (uuid.uuid4() for _ in range(3))
        ctx = self._ctx([u1, u2, u3])
        assert on_call_at(ctx, datetime(2026, 5, 5, 12, tzinfo=timezone.utc)) == u1
        assert on_call_at(ctx, datetime(2026, 5, 12, 12, tzinfo=timezone.utc)) == u2
        assert on_call_at(ctx, datetime(2026, 5, 19, 12, tzinfo=timezone.utc)) == u3
        # Wraps after three weeks.
        assert on_call_at(ctx, datetime(2026, 5, 26, 12, tzinfo=timezone.utc)) == u1

    def test_daily_rotation(self):
        u1, u2 = uuid.uuid4(), uuid.uuid4()
        ctx = self._ctx([u1, u2], pattern="daily", pattern_length=1)
        assert on_call_at(ctx, datetime(2026, 5, 4, 12, tzinfo=timezone.utc)) == u1
        assert on_call_at(ctx, datetime(2026, 5, 5, 12, tzinfo=timezone.utc)) == u2

    def test_custom_n_days_rotation(self):
        u1, u2, u3 = (uuid.uuid4() for _ in range(3))
        ctx = self._ctx([u1, u2, u3], pattern="custom_n_days", pattern_length=3)
        # Day 0–2 → u1, day 3–5 → u2, day 6–8 → u3
        assert on_call_at(ctx, datetime(2026, 5, 5, 12, tzinfo=timezone.utc)) == u1
        assert on_call_at(ctx, datetime(2026, 5, 7, 12, tzinfo=timezone.utc)) == u2
        assert on_call_at(ctx, datetime(2026, 5, 10, 12, tzinfo=timezone.utc)) == u3

    def test_coverage_start_boundary(self):
        u1, u2 = uuid.uuid4(), uuid.uuid4()
        ctx = self._ctx(
            [u1, u2],
            pattern="daily",
            pattern_length=1,
            coverage_start_time="09:00",
            coverage_end_time="17:00",
        )
        assert on_call_at(ctx, datetime(2026, 5, 5, 8, 0, tzinfo=timezone.utc)) is None
        assert on_call_at(ctx, datetime(2026, 5, 5, 10, 0, tzinfo=timezone.utc)) == u2

    def test_overnight_coverage_uses_previous_rotation_date(self):
        u1, u2 = uuid.uuid4(), uuid.uuid4()
        ctx = self._ctx(
            [u1, u2],
            pattern="daily",
            pattern_length=1,
            coverage_start_time="18:00",
            coverage_end_time="08:00",
        )
        assert on_call_at(ctx, datetime(2026, 5, 4, 20, 0, tzinfo=timezone.utc)) == u1
        assert on_call_at(ctx, datetime(2026, 5, 5, 6, 0, tzinfo=timezone.utc)) == u1
        assert on_call_at(ctx, datetime(2026, 5, 5, 12, 0, tzinfo=timezone.utc)) is None

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
        assert on_call_at(ov_ctx, datetime(2026, 5, 5, 10, tzinfo=timezone.utc)) == u3

    def test_empty_roster_returns_none(self):
        ctx = OnCallContext(members=[], anchor_date=date(2026, 5, 4))
        assert on_call_at(ctx, datetime(2026, 5, 5, 10, tzinfo=timezone.utc)) is None

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
        assert not rule_matches({"severity": ["critical"]}, {"severity": "warning"})

    def test_rule_matches_case_insensitive(self):
        assert rule_matches({"severity": ["Critical"]}, {"severity": "critical"})

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
            members = await RosterRepo.list_members(db, TEST_ORG_ID, roster.id)
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

    async def test_org_notification_dedup_setting(self, app):
        async with app.state.session_factory() as db:
            org = await OrganizationRepo.update(
                db, TEST_ORG_ID, notification_dedup_window_minutes=15
            )
            await db.commit()

            assert org is not None
            assert org.notification_dedup_window_minutes == 15

    async def test_maintenance_window_scopes(self, app):
        async with app.state.session_factory() as db:
            team = await TeamRepo.create(db, TEST_ORG_ID, name="Ops", slug="ops")
            service = await ServiceRepo.create(
                db,
                TEST_ORG_ID,
                team_id=team.id,
                name="API",
                slug="api",
            )
            now = datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc)
            await MaintenanceWindowRepo.create(
                db,
                TEST_ORG_ID,
                name="All hands",
                description="Global deploy freeze",
                starts_at=now - timedelta(minutes=5),
                ends_at=now + timedelta(minutes=5),
                scope_type="global",
            )
            scoped = await MaintenanceWindowRepo.create(
                db,
                TEST_ORG_ID,
                name="API deploy",
                starts_at=now - timedelta(minutes=5),
                ends_at=now + timedelta(minutes=5),
                scope_type="service",
                scope_id=service.id,
            )
            await db.commit()

            active = await MaintenanceWindowRepo.list_active_at(
                db,
                TEST_ORG_ID,
                now,
                scope_type="service",
                scope_id=service.id,
            )
            assert {window.name for window in active} == {"All hands", "API deploy"}
            assert scoped.target_ids == []

    async def test_user_notification_pref_upsert(self, app):
        async with app.state.session_factory() as db:
            user = await UserRepo.create(
                db,
                username="notify@test",
                email="notify@test.com",
                password_hash="x",
                role="viewer",
                primary_org_id=TEST_ORG_ID,
            )
            pref = await UserNotificationPrefRepo.upsert(
                db,
                TEST_ORG_ID,
                user.id,
                channels={"email": "notify@test.com"},
                routing={"P0": ["email"], "P1": ["email"]},
                quiet_hours={"weekday_start": "22:00", "min_priority_to_break": "P0"},
                quiet_hours_provided=True,
            )
            await db.commit()

            assert pref.channels["email"] == "notify@test.com"
            assert pref.routing["P0"] == ["email"]

            updated = await UserNotificationPrefRepo.upsert(
                db,
                TEST_ORG_ID,
                user.id,
                routing={"P0": ["sms"]},
            )
            await db.commit()

            assert updated.id == pref.id
            assert updated.channels["email"] == "notify@test.com"
            assert updated.routing == {"P0": ["sms"]}

    async def test_incident_assignment_replaces_active(self, app):
        async with app.state.session_factory() as db:
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
            active = await IncidentAssignmentRepo.get_active(db, TEST_ORG_ID, inc.id)
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

        deleted = await client.delete(f"/teams/{team_id}", headers=auth_headers)
        assert deleted.status_code == 204

    async def test_team_slug_must_be_lowercase(
        self, client: AsyncClient, auth_headers
    ):
        """v1 — team slugs are lowercase-only; uppercase is rejected server-side
        so the value can never persist."""
        upper = await client.post(
            "/teams",
            json={"name": "Mixed", "slug": "Payments-Team"},
            headers=auth_headers,
        )
        assert upper.status_code == 422

        ok = await client.post(
            "/teams",
            json={"name": "Lower", "slug": "payments-team"},
            headers=auth_headers,
        )
        assert ok.status_code == 201
        assert ok.json()["slug"] == "payments-team"

    async def test_team_membership_add_list_remove(
        self, client: AsyncClient, app, auth_headers
    ):
        """v1 — the team edit form assigns people via add/remove member routes."""
        async with app.state.session_factory() as db:
            member = await UserRepo.create(
                db,
                username="member-one",
                email="member-one@example.com",
                password_hash="x",
                role="operator",
                primary_org_id=TEST_ORG_ID,
            )
            await db.commit()
            member_id = str(member.id)

        team = await client.post(
            "/teams",
            json={"name": "Membership", "slug": f"mem-{uuid.uuid4().hex[:6]}"},
            headers=auth_headers,
        )
        team_id = team.json()["id"]

        added = await client.post(
            f"/teams/{team_id}/members",
            json={"user_id": member_id},
            headers=auth_headers,
        )
        assert added.status_code == 201, added.text

        listed = await client.get(
            f"/teams/{team_id}/members", headers=auth_headers
        )
        assert listed.status_code == 200
        assert any(m["user_id"] == member_id for m in listed.json()["items"])

        removed = await client.delete(
            f"/teams/{team_id}/members/{member_id}", headers=auth_headers
        )
        assert removed.status_code == 204
        relisted = await client.get(
            f"/teams/{team_id}/members", headers=auth_headers
        )
        assert relisted.json()["total"] == 0

    async def test_notification_preferences_test_endpoint(
        self, client: AsyncClient, auth_headers
    ):
        """v1 My Routing — the Test notification button hits this endpoint. It
        never 500s; channels without credentials/destinations are reported as
        skipped rather than failing the request."""
        # Route P0 to email so there is at least one channel to attempt.
        await client.put(
            "/users/me/notification-preferences",
            json={
                "channels": {"email": {"address": "ops@example.com"}},
                "routing": {"P0": ["email"]},
            },
            headers=auth_headers,
        )
        resp = await client.post(
            "/users/me/notification-preferences/test", headers=auth_headers
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "results" in body and "tested" in body
        assert isinstance(body["results"], list)
        # SMTP isn't configured in tests → email attempt is skipped, not failed.
        for entry in body["results"]:
            assert entry["status"] in {"sent", "skipped", "failed"}

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

    async def test_service_saves_priority_and_preferred_mcp_context(
        self, client: AsyncClient, app, auth_headers
    ):
        async with app.state.session_factory() as db:
            first = await MCPServerRepo.create(
                db,
                TEST_ORG_ID,
                name="aws-prod",
                transport="http",
                url="http://aws-prod.local/mcp",
            )
            second = await MCPServerRepo.create(
                db,
                TEST_ORG_ID,
                name="gitlab-prod",
                transport="http",
                url="http://gitlab-prod.local/mcp",
            )
            await db.commit()

        team = await client.post(
            "/teams",
            json={"name": "Service MCP", "slug": f"svc-mcp-{uuid.uuid4().hex[:6]}"},
            headers=auth_headers,
        )
        service = await client.post(
            "/services",
            json={
                "team_id": team.json()["id"],
                "name": "AWS Prod Critical",
                "slug": f"aws-prod-critical-{uuid.uuid4().hex[:6]}",
                "priority": "P0",
                "preferred_mcp_server_ids": [str(second.id), str(first.id)],
            },
            headers=auth_headers,
        )
        assert service.status_code == 201, service.text
        data = service.json()
        assert data["priority"] == "P0"
        assert data["preferred_mcp_server_ids"] == [str(second.id), str(first.id)]
        assert data["intake_url"].startswith("/api/v1/intake/svc_")

        async with app.state.session_factory() as db:
            incident = await IncidentRepo.create(
                db,
                TEST_ORG_ID,
                title="CPU high",
                description="prod worker",
                service_id=uuid.UUID(data["id"]),
            )
            await db.commit()

        preferred_ids = await _preferred_mcp_ids_for_incident(
            app.state.session_factory,
            TEST_ORG_ID,
            incident,
        )
        assert preferred_ids == [second.id, first.id]

        class _Pool:
            async def list_servers(self, active_only=True):
                return [
                    MCPServerConfig(
                        name="aws-prod",
                        transport="http",
                        url="http://aws-prod.local/mcp",
                    ),
                    MCPServerConfig(
                        name="gitlab-prod",
                        transport="http",
                        url="http://gitlab-prod.local/mcp",
                    ),
                ]

        selected, _skill, preferred_names = await _resolve_mcp_context(
            app.state.session_factory,
            TEST_ORG_ID,
            _Pool(),
            app.state.config,
            preferred_mcp_server_ids=preferred_ids,
        )
        assert preferred_names == ["gitlab-prod", "aws-prod"]
        assert selected is not None
        assert selected.name == "gitlab-prod"

    async def test_service_intake_uses_service_priority_not_priority_rules(
        self, client: AsyncClient, app, auth_headers
    ):
        await client.post(
            "/priority-rules",
            json={
                "name": "Low would be P3",
                "condition": {"severity": ["low"]},
                "priority": "P3",
            },
            headers=auth_headers,
        )
        team = await client.post(
            "/teams",
            json={"name": "Intake Priority", "slug": f"intake-{uuid.uuid4().hex[:6]}"},
            headers=auth_headers,
        )
        service = await client.post(
            "/services",
            json={
                "team_id": team.json()["id"],
                "name": "Critical Intake",
                "slug": f"critical-intake-{uuid.uuid4().hex[:6]}",
                "priority": "P0",
            },
            headers=auth_headers,
        )
        assert service.status_code == 201, service.text
        intake_url = service.json()["intake_url"]

        ingest = await client.post(
            intake_url,
            json={
                "title": "Synthetic low alert",
                "description": "service owns priority",
                "severity": "low",
                "external_id": f"svc-priority-{uuid.uuid4()}",
            },
        )
        assert ingest.status_code == 200, ingest.text
        incident_id = uuid.UUID(ingest.json()["incident_id"])

        async with app.state.session_factory() as db:
            incident = await IncidentRepo.get_by_id(db, TEST_ORG_ID, incident_id)
        assert incident is not None
        assert incident.priority == "P0"
        assert incident.service_id == uuid.UUID(service.json()["id"])

    async def test_service_intake_drops_matching_maintenance_windows(
        self, client: AsyncClient, app, auth_headers
    ):
        team = await client.post(
            "/teams",
            json={"name": "Maintenance", "slug": f"mw-{uuid.uuid4().hex[:6]}"},
            headers=auth_headers,
        )
        services = []
        for name in ["API A", "API B", "API C"]:
            resp = await client.post(
                "/services",
                json={
                    "team_id": team.json()["id"],
                    "name": name,
                    "slug": f"{name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:6]}",
                    "priority": "P1",
                },
                headers=auth_headers,
            )
            assert resp.status_code == 201, resp.text
            services.append(resp.json())

        now = datetime.now(timezone.utc)
        mw = await client.post(
            "/maintenance-windows",
            json={
                "name": "Multi-service work",
                "starts_at": (now - timedelta(minutes=5)).isoformat(),
                "ends_at": (now + timedelta(minutes=30)).isoformat(),
                "scope_type": "service",
                "scope_ids": [services[0]["id"], services[1]["id"]],
            },
            headers=auth_headers,
        )
        assert mw.status_code == 201, mw.text
        assert set(mw.json()["scope_ids"]) == {services[0]["id"], services[1]["id"]}

        suppressed = await client.post(
            services[0]["intake_url"],
            json={
                "title": "Suppressed alert",
                "description": "covered by maintenance",
                "severity": "high",
                "external_id": f"suppressed-{uuid.uuid4()}",
            },
        )
        assert suppressed.status_code == 200, suppressed.text
        assert suppressed.json()["dedup_action"] == "skipped"
        assert suppressed.json()["incident_id"] is None

        created = await client.post(
            services[2]["intake_url"],
            json={
                "title": "Visible alert",
                "description": "not covered",
                "severity": "high",
                "external_id": f"visible-{uuid.uuid4()}",
            },
        )
        assert created.status_code == 200, created.text
        assert created.json()["incident_id"] is not None

        async with app.state.session_factory() as db:
            incidents = await IncidentRepo.list_all(db, TEST_ORG_ID)
        assert [incident.title for incident in incidents] == ["Visible alert"]

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
                role="operator",
                primary_org_id=TEST_ORG_ID,
            )
            await db.commit()

        # Rotation members must belong to the roster's team.
        await client.post(
            f"/teams/{team_id}/members",
            json={"user_id": str(u1.id)},
            headers=auth_headers,
        )

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

    async def test_disabled_roster_is_ignored_for_on_call(
        self, client: AsyncClient, app, auth_headers
    ):
        team = await client.post(
            "/teams",
            json={"name": "Disabled", "slug": f"disabled-{uuid.uuid4().hex[:6]}"},
            headers=auth_headers,
        )
        roster_resp = await client.post(
            "/rosters",
            json={
                "team_id": team.json()["id"],
                "name": "Disabled schedule",
                "pattern": "daily",
                "pattern_length": 1,
                "anchor_date": "2026-05-04",
                "coverage_start_time": "00:00",
                "coverage_end_time": "00:00",
                "time_zone": "UTC",
                "is_active": False,
            },
            headers=auth_headers,
        )
        assert roster_resp.status_code == 201
        roster_id = roster_resp.json()["id"]

        async with app.state.session_factory() as db:
            operator = await UserRepo.create(
                db,
                username=f"disabled-op-{uuid.uuid4().hex[:6]}",
                email=f"disabled-op-{uuid.uuid4().hex[:6]}@test.com",
                password_hash="x",
                role="operator",
                primary_org_id=TEST_ORG_ID,
            )
            await db.commit()
        await client.post(
            f"/teams/{team.json()['id']}/members",
            json={"user_id": str(operator.id)},
            headers=auth_headers,
        )
        add = await client.post(
            f"/rosters/{roster_id}/members",
            json={"user_id": str(operator.id), "position_index": 0},
            headers=auth_headers,
        )
        assert add.status_code == 201

        from urllib.parse import quote

        at = quote("2026-05-05T12:00:00+00:00", safe="")
        resp = await client.get(
            f"/rosters/{roster_id}/on-call?at={at}", headers=auth_headers
        )
        assert resp.status_code == 200
        assert resp.json()["user_id"] is None

    async def test_viewer_cannot_be_added_to_roster(
        self, client: AsyncClient, app, auth_headers
    ):
        team = await client.post(
            "/teams",
            json={"name": "Viewer Roster", "slug": f"viewer-{uuid.uuid4().hex[:6]}"},
            headers=auth_headers,
        )
        roster_resp = await client.post(
            "/rosters",
            json={
                "team_id": team.json()["id"],
                "name": "Primary",
                "pattern": "daily",
                "pattern_length": 1,
                "anchor_date": "2026-05-04",
                "coverage_start_time": "00:00",
                "coverage_end_time": "00:00",
                "time_zone": "UTC",
            },
            headers=auth_headers,
        )
        assert roster_resp.status_code == 201
        async with app.state.session_factory() as db:
            viewer = await UserRepo.create(
                db,
                username=f"viewer-roster-{uuid.uuid4().hex[:6]}",
                email=f"viewer-roster-{uuid.uuid4().hex[:6]}@test.com",
                password_hash="x",
                role="viewer",
                primary_org_id=TEST_ORG_ID,
            )
            await db.commit()
        add = await client.post(
            f"/rosters/{roster_resp.json()['id']}/members",
            json={"user_id": str(viewer.id), "position_index": 0},
            headers=auth_headers,
        )
        assert add.status_code == 400
        assert "Admin or Operator" in add.json()["detail"]

    async def test_on_call_range_powers_calendar(
        self, client: AsyncClient, app, auth_headers
    ):
        """Sprint 47 — /rosters/{id}/on-call/range returns one item per step
        and flags override days distinctly."""
        # Seed a team + roster + two users.
        team = await client.post(
            "/teams",
            json={"name": "RangeTeam", "slug": f"range-{uuid.uuid4().hex[:6]}"},
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
                "anchor_date": "2026-05-20",
                "handoff_time": "00:00",
                "time_zone": "UTC",
            },
            headers=auth_headers,
        )
        assert roster_resp.status_code == 201
        roster_id = roster_resp.json()["id"]

        from backend.db.repos import UserRepo

        async with app.state.session_factory() as db:
            u1 = await UserRepo.create(
                db,
                username=f"rng1-{uuid.uuid4().hex[:6]}",
                email=f"rng1-{uuid.uuid4().hex[:6]}@test.com",
                password_hash="x",
                role="operator",
                primary_org_id=TEST_ORG_ID,
            )
            u2 = await UserRepo.create(
                db,
                username=f"rng2-{uuid.uuid4().hex[:6]}",
                email=f"rng2-{uuid.uuid4().hex[:6]}@test.com",
                password_hash="x",
                role="operator",
                primary_org_id=TEST_ORG_ID,
            )
            await db.commit()
        for uid in (u1.id, u2.id):
            await client.post(
                f"/teams/{team_id}/members",
                json={"user_id": str(uid)},
                headers=auth_headers,
            )
        await client.post(
            f"/rosters/{roster_id}/members",
            json={"user_id": str(u1.id), "position_index": 0},
            headers=auth_headers,
        )

        # Create an override that covers 2026-05-22.
        override_resp = await client.post(
            f"/rosters/{roster_id}/overrides",
            json={
                "covering_user_id": str(u2.id),
                "starts_at": "2026-05-22T00:00:00+00:00",
                "ends_at": "2026-05-23T00:00:00+00:00",
                "reason": "vacation cover",
            },
            headers=auth_headers,
        )
        assert override_resp.status_code == 201

        from urllib.parse import quote

        frm = quote("2026-05-20T12:00:00+00:00", safe="")
        to = quote("2026-05-24T12:00:00+00:00", safe="")
        resp = await client.get(
            f"/rosters/{roster_id}/on-call/range?from={frm}&to={to}&step_hours=24",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        # 5 sample points: 5/20 12:00, 5/21 12:00, 5/22 12:00, 5/23 12:00, 5/24 12:00
        assert len(body["items"]) == 5
        # 5/22 falls inside the override.
        override_items = [i for i in body["items"] if i["is_override"]]
        assert len(override_items) == 1
        assert override_items[0]["user_id"] == str(u2.id)
        # All non-override items resolve to u1.
        for item in body["items"]:
            if not item["is_override"]:
                assert item["user_id"] == str(u1.id)

    async def test_on_call_range_rejects_oversize_request(
        self, client: AsyncClient, auth_headers
    ):
        team = await client.post(
            "/teams",
            json={"name": "Oversize", "slug": f"os-{uuid.uuid4().hex[:6]}"},
            headers=auth_headers,
        )
        roster_resp = await client.post(
            "/rosters",
            json={
                "team_id": team.json()["id"],
                "name": "P",
                "pattern": "daily",
                "pattern_length": 1,
                "anchor_date": "2026-01-01",
                "handoff_time": "00:00",
                "time_zone": "UTC",
            },
            headers=auth_headers,
        )
        roster_id = roster_resp.json()["id"]
        # 5000 hours / 1-hour step = 5001 samples, exceeds the 200 cap.
        from urllib.parse import quote

        frm = quote("2026-01-01T00:00:00+00:00", safe="")
        to = quote("2026-07-30T00:00:00+00:00", safe="")
        resp = await client.get(
            f"/rosters/{roster_id}/on-call/range?from={frm}&to={to}&step_hours=1",
            headers=auth_headers,
        )
        assert resp.status_code == 400

    async def test_on_call_range_rejects_inverted_range(
        self, client: AsyncClient, auth_headers
    ):
        team = await client.post(
            "/teams",
            json={"name": "Inv", "slug": f"inv-{uuid.uuid4().hex[:6]}"},
            headers=auth_headers,
        )
        roster_resp = await client.post(
            "/rosters",
            json={
                "team_id": team.json()["id"],
                "name": "P",
                "pattern": "daily",
                "pattern_length": 1,
                "anchor_date": "2026-01-01",
                "handoff_time": "00:00",
                "time_zone": "UTC",
            },
            headers=auth_headers,
        )
        from urllib.parse import quote

        frm = quote("2026-05-10T00:00:00+00:00", safe="")
        to = quote("2026-05-01T00:00:00+00:00", safe="")
        resp = await client.get(
            f"/rosters/{roster_resp.json()['id']}/on-call/range?from={frm}&to={to}",
            headers=auth_headers,
        )
        assert resp.status_code == 400

    async def test_escalation_chain_calendar_default_range_and_order(
        self, client: AsyncClient, app, auth_headers
    ):
        team = await client.post(
            "/teams",
            json={"name": "Calendar", "slug": f"cal-{uuid.uuid4().hex[:6]}"},
            headers=auth_headers,
        )
        chain = await client.post(
            "/escalation-chains",
            json={"team_id": team.json()["id"], "name": "Primary chain"},
            headers=auth_headers,
        )
        assert chain.status_code == 201
        chain_id = chain.json()["id"]

        async with app.state.session_factory() as db:
            u1 = await UserRepo.create(
                db,
                username=f"cal-a-{uuid.uuid4().hex[:6]}",
                email=f"cal-a-{uuid.uuid4().hex[:6]}@test.com",
                password_hash="x",
                role="operator",
                primary_org_id=TEST_ORG_ID,
            )
            u2 = await UserRepo.create(
                db,
                username=f"cal-b-{uuid.uuid4().hex[:6]}",
                email=f"cal-b-{uuid.uuid4().hex[:6]}@test.com",
                password_hash="x",
                role="operator",
                primary_org_id=TEST_ORG_ID,
            )
            await db.commit()

        for step_index, user_id in [(1, u2.id), (0, u1.id)]:
            step = await client.post(
                f"/escalation-chains/{chain_id}/steps",
                json={
                    "step_index": step_index,
                    "target_type": "user",
                    "target_id": str(user_id),
                    "timeout_seconds": 300,
                },
                headers=auth_headers,
            )
            assert step.status_code == 201, step.text

        resp = await client.get(
            f"/escalation-chains/{chain_id}/calendar", headers=auth_headers
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["range"] == "7d"
        assert len(body["days"]) == 7
        assert [level["level"] for level in body["days"][0]["levels"]] == [1, 2]
        assert body["days"][0]["levels"][0]["resolved_user_id"] == str(u1.id)
        assert body["days"][0]["levels"][1]["resolved_user_id"] == str(u2.id)

    async def test_escalation_chain_calendar_30d_and_90d_ranges(
        self, client: AsyncClient, auth_headers
    ):
        team = await client.post(
            "/teams",
            json={"name": "Ranges", "slug": f"ranges-{uuid.uuid4().hex[:6]}"},
            headers=auth_headers,
        )
        chain = await client.post(
            "/escalation-chains",
            json={"team_id": team.json()["id"], "name": "Range chain"},
            headers=auth_headers,
        )
        chain_id = chain.json()["id"]

        for requested, expected in [("30d", 30), ("90d", 90), ("today", 1)]:
            resp = await client.get(
                f"/escalation-chains/{chain_id}/calendar?range={requested}&start=2026-06-05",
                headers=auth_headers,
            )
            assert resp.status_code == 200
            assert resp.json()["range"] == requested
            assert len(resp.json()["days"]) == expected

    async def test_escalation_chain_calendar_resolves_roster_rotation_and_overnight(
        self, client: AsyncClient, app, auth_headers
    ):
        team = await client.post(
            "/teams",
            json={"name": "Night", "slug": f"night-{uuid.uuid4().hex[:6]}"},
            headers=auth_headers,
        )
        team_id = team.json()["id"]
        roster = await client.post(
            "/rosters",
            json={
                "team_id": team_id,
                "name": "Night rotation",
                "pattern": "daily",
                "pattern_length": 1,
                "anchor_date": "2026-06-01",
                "coverage_start_time": "18:00",
                "coverage_end_time": "08:00",
                "time_zone": "UTC",
            },
            headers=auth_headers,
        )
        roster_id = roster.json()["id"]
        async with app.state.session_factory() as db:
            alice = await UserRepo.create(
                db,
                username="alice-night",
                email="alice-night@test.com",
                password_hash="x",
                role="operator",
                primary_org_id=TEST_ORG_ID,
            )
            bob = await UserRepo.create(
                db,
                username="bob-night",
                email="bob-night@test.com",
                password_hash="x",
                role="operator",
                primary_org_id=TEST_ORG_ID,
            )
            carol = await UserRepo.create(
                db,
                username="carol-night",
                email="carol-night@test.com",
                password_hash="x",
                role="operator",
                primary_org_id=TEST_ORG_ID,
            )
            await db.commit()
        for user_id in (alice.id, bob.id, carol.id):
            await client.post(
                f"/teams/{team_id}/members",
                json={"user_id": str(user_id)},
                headers=auth_headers,
            )
        for idx, user_id in enumerate([alice.id, bob.id, carol.id]):
            add = await client.post(
                f"/rosters/{roster_id}/members",
                json={"user_id": str(user_id), "position_index": idx},
                headers=auth_headers,
            )
            assert add.status_code == 201
        chain = await client.post(
            "/escalation-chains",
            json={"team_id": team_id, "name": "Night chain"},
            headers=auth_headers,
        )
        step = await client.post(
            f"/escalation-chains/{chain.json()['id']}/steps",
            json={
                "step_index": 0,
                "target_type": "roster",
                "target_id": roster_id,
                "timeout_seconds": 300,
            },
            headers=auth_headers,
        )
        assert step.status_code == 201

        resp = await client.get(
            f"/escalation-chains/{chain.json()['id']}/calendar?range=7d&start=2026-06-01",
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        resolved = [day["levels"][0]["resolved_user_name"] for day in resp.json()["days"][:4]]
        assert resolved == ["alice-night", "bob-night", "carol-night", "alice-night"]
        first = resp.json()["days"][0]["levels"][0]
        assert first["coverage_start"] == "18:00"
        assert first["coverage_end"] == "08:00"
        assert first["status"] == "covered"

    async def test_escalation_chain_calendar_warning_statuses(
        self, client: AsyncClient, app, auth_headers
    ):
        team = await client.post(
            "/teams",
            json={"name": "Warnings", "slug": f"warn-{uuid.uuid4().hex[:6]}"},
            headers=auth_headers,
        )
        team_id = team.json()["id"]
        chain = await client.post(
            "/escalation-chains",
            json={"team_id": team_id, "name": "Warning chain"},
            headers=auth_headers,
        )
        chain_id = chain.json()["id"]

        disabled = await client.post(
            "/rosters",
            json={
                "team_id": team_id,
                "name": "Disabled roster",
                "pattern": "daily",
                "pattern_length": 1,
                "anchor_date": "2026-06-01",
                "coverage_start_time": "00:00",
                "coverage_end_time": "00:00",
                "time_zone": "UTC",
                "is_active": False,
            },
            headers=auth_headers,
        )
        empty = await client.post(
            "/rosters",
            json={
                "team_id": team_id,
                "name": "Empty roster",
                "pattern": "daily",
                "pattern_length": 1,
                "anchor_date": "2026-06-01",
                "coverage_start_time": "00:00",
                "coverage_end_time": "00:00",
                "time_zone": "UTC",
            },
            headers=auth_headers,
        )
        inactive_roster = await client.post(
            "/rosters",
            json={
                "team_id": team_id,
                "name": "Inactive roster",
                "pattern": "daily",
                "pattern_length": 1,
                "anchor_date": "2026-06-01",
                "coverage_start_time": "00:00",
                "coverage_end_time": "00:00",
                "time_zone": "UTC",
            },
            headers=auth_headers,
        )
        async with app.state.session_factory() as db:
            inactive = await UserRepo.create(
                db,
                username="inactive-calendar",
                email="inactive-calendar@test.com",
                password_hash="x",
                role="operator",
                primary_org_id=TEST_ORG_ID,
            )
            await db.commit()
            inactive_id = inactive.id
        # Realistic sequence: the user joins the team + roster while active,
        # then is deactivated later (the roster keeps the now-inactive member).
        await client.post(
            f"/teams/{team_id}/members",
            json={"user_id": str(inactive_id)},
            headers=auth_headers,
        )
        await client.post(
            f"/rosters/{inactive_roster.json()['id']}/members",
            json={"user_id": str(inactive_id), "position_index": 0},
            headers=auth_headers,
        )
        async with app.state.session_factory() as db:
            await UserRepo.update_fields(db, inactive_id, is_active=False)
            await db.commit()
        for idx, (target_type, target_id) in enumerate(
            [
                ("roster", disabled.json()["id"]),
                ("roster", empty.json()["id"]),
                ("roster", inactive_roster.json()["id"]),
                ("user", str(inactive.id)),
            ]
        ):
            step = await client.post(
                f"/escalation-chains/{chain_id}/steps",
                json={
                    "step_index": idx,
                    "target_type": target_type,
                    "target_id": target_id,
                    "timeout_seconds": 300,
                },
                headers=auth_headers,
            )
            assert step.status_code == 201, step.text

        resp = await client.get(
            f"/escalation-chains/{chain_id}/calendar?range=today&start=2026-06-05",
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        statuses = [level["status"] for level in resp.json()["days"][0]["levels"]]
        assert statuses == [
            "disabled_roster",
            "empty_roster",
            "inactive_user",
            "inactive_user",
        ]
        assert all(level["warnings"] for level in resp.json()["days"][0]["levels"])

    async def test_escalation_chain_calendar_rbac(
        self, client: AsyncClient, auth_headers
    ):
        team = await client.post(
            "/teams",
            json={"name": "RBAC", "slug": f"rbac-{uuid.uuid4().hex[:6]}"},
            headers=auth_headers,
        )
        chain = await client.post(
            "/escalation-chains",
            json={"team_id": team.json()["id"], "name": "RBAC chain"},
            headers=auth_headers,
        )
        chain_id = chain.json()["id"]
        for role in ("operator", "viewer"):
            create = await client.post(
                "/auth/users",
                headers=auth_headers,
                json={
                    "username": f"calendar-{role}",
                    "email": f"calendar-{role}@test.com",
                    "role": role,
                    "password": "temp-pass-123",
                    "require_password_change": False,
                },
            )
            assert create.status_code == 201
            login = await client.post(
                "/auth/login",
                json={"username": f"calendar-{role}", "password": "temp-pass-123"},
            )
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
            resp = await client.get(
                f"/escalation-chains/{chain_id}/calendar?start=2026-06-05",
                headers=headers,
            )
            assert resp.status_code == (200 if role == "operator" else 403)

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

    async def test_incident_create_ignores_priority_rules_for_v1(
        self, client: AsyncClient, app, auth_headers
    ):
        # Legacy rules can remain internally, but v1 priority assignment uses
        # service configuration or deterministic severity fallback.
        await client.post(
            "/priority-rules",
            json={
                "name": "Critical would be downgraded",
                "condition": {"severity": ["critical"]},
                "priority": "P3",
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

    async def test_incident_assign_and_release(self, client: AsyncClient, auth_headers):
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


# ---------------------------------------------------------------------------
# Sprint 35 Step 7 — Notification preferences + org notification settings
# ---------------------------------------------------------------------------


class TestNotificationPreferencesAPI:
    async def test_get_creates_default_pref(
        self, client: AsyncClient, auth_headers
    ):
        resp = await client.get(
            "/users/me/notification-preferences", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["channels"] == {}
        assert data["routing"] == {}
        assert data["quiet_hours"] is None

    async def test_put_updates_pref(self, client: AsyncClient, auth_headers):
        body = {
            "channels": {"slack_dm": {"handle": "@me"}, "email": {"address": "x@y"}},
            "routing": {"P0": ["slack_dm", "email"], "P1": ["slack_dm"]},
            "quiet_hours": {
                "weekday": {"start": "22:00", "end": "07:00"},
                "min_priority_to_break": "P1",
            },
        }
        resp = await client.put(
            "/users/me/notification-preferences",
            json=body,
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["channels"] == body["channels"]
        assert data["routing"] == body["routing"]
        assert data["quiet_hours"] == body["quiet_hours"]

        # GET reflects the update.
        again = await client.get(
            "/users/me/notification-preferences", headers=auth_headers
        )
        assert again.json()["routing"] == body["routing"]

    async def test_partial_update_preserves_other_fields(
        self, client: AsyncClient, auth_headers
    ):
        await client.put(
            "/users/me/notification-preferences",
            json={
                "channels": {"slack_dm": {"handle": "@me"}},
                "routing": {"P0": ["slack_dm"]},
            },
            headers=auth_headers,
        )
        resp = await client.put(
            "/users/me/notification-preferences",
            json={"routing": {"P1": ["slack_dm"]}},
            headers=auth_headers,
        )
        data = resp.json()
        assert data["channels"] == {"slack_dm": {"handle": "@me"}}
        assert data["routing"] == {"P1": ["slack_dm"]}

    async def test_requires_auth(self, client: AsyncClient):
        resp = await client.get("/users/me/notification-preferences")
        assert resp.status_code == 401


class TestOrgNotificationSettingsAPI:
    async def test_get_returns_default_window(
        self, client: AsyncClient, auth_headers
    ):
        resp = await client.get(
            f"/organizations/{TEST_ORG_ID}/notification-settings",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["notification_dedup_window_minutes"] == 10

    async def test_put_updates_window(self, client: AsyncClient, auth_headers):
        resp = await client.put(
            f"/organizations/{TEST_ORG_ID}/notification-settings",
            json={"notification_dedup_window_minutes": 25},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["notification_dedup_window_minutes"] == 25

        again = await client.get(
            f"/organizations/{TEST_ORG_ID}/notification-settings",
            headers=auth_headers,
        )
        assert again.json()["notification_dedup_window_minutes"] == 25

    async def test_put_rejects_negative(
        self, client: AsyncClient, auth_headers
    ):
        resp = await client.put(
            f"/organizations/{TEST_ORG_ID}/notification-settings",
            json={"notification_dedup_window_minutes": -5},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    async def test_get_unknown_org_returns_404(
        self, client: AsyncClient, auth_headers
    ):
        bogus = uuid.uuid4()
        resp = await client.get(
            f"/organizations/{bogus}/notification-settings",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    async def test_non_admin_forbidden(
        self, client: AsyncClient, auth_headers
    ):
        # Register a viewer-role second user.
        await client.post(
            "/auth/register",
            json={
                "username": "viewer",
                "email": "viewer@test.com",
                "password": "securepass123",
                "role": "viewer",
            },
        )
        login = await client.post(
            "/auth/login",
            json={"username": "viewer", "password": "securepass123"},
        )
        viewer_headers = {
            "Authorization": f"Bearer {login.json()['access_token']}"
        }
        resp = await client.get(
            f"/organizations/{TEST_ORG_ID}/notification-settings",
            headers=viewer_headers,
        )
        assert resp.status_code == 403


class TestMaintenanceWindowScopeFields:
    async def test_create_with_scope(self, client: AsyncClient, auth_headers):
        now = datetime.now(timezone.utc)
        scope_id = uuid.uuid4()
        resp = await client.post(
            "/maintenance-windows",
            json={
                "name": "service-mw",
                "description": "DB migration window",
                "starts_at": (now + timedelta(hours=1)).isoformat(),
                "ends_at": (now + timedelta(hours=2)).isoformat(),
                "scope_type": "service",
                "scope_id": str(scope_id),
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["scope_type"] == "service"
        assert data["scope_id"] == str(scope_id)
        assert data["description"] == "DB migration window"
        assert data["target_ids"] == [str(scope_id)]

    async def test_global_scope_default(
        self, client: AsyncClient, auth_headers
    ):
        now = datetime.now(timezone.utc)
        resp = await client.post(
            "/maintenance-windows",
            json={
                "name": "global-mw",
                "starts_at": (now + timedelta(hours=1)).isoformat(),
                "ends_at": (now + timedelta(hours=2)).isoformat(),
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        assert resp.json()["scope_type"] == "global"


# ---------------------------------------------------------------------------
# Sprint 35 Step 10 — Incident paging panel exposes suppression
# ---------------------------------------------------------------------------


class TestIncidentPagingSuppression:
    async def test_panel_returns_suppression_when_set(
        self, client: AsyncClient, app, auth_headers
    ):
        # Create the maintenance window via API to anchor scope.
        now = datetime.now(timezone.utc)
        mw_resp = await client.post(
            "/maintenance-windows",
            json={
                "name": "DB freeze",
                "starts_at": (now - timedelta(hours=1)).isoformat(),
                "ends_at": (now + timedelta(hours=1)).isoformat(),
            },
            headers=auth_headers,
        )
        assert mw_resp.status_code == 201
        mw_id = mw_resp.json()["id"]

        # Create an incident and stamp the suppression directly on the row.
        factory = app.state.session_factory
        async with factory() as session:
            incident = await IncidentRepo.create(
                session,
                TEST_ORG_ID,
                title="Outage",
                description="db",
            )
            incident.suppressed_by_maintenance_window_id = uuid.UUID(mw_id)
            await session.commit()
            incident_id = str(incident.id)

        resp = await client.get(
            f"/incidents/{incident_id}/paging", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["suppressed_by_maintenance_window"] is not None
        assert data["suppressed_by_maintenance_window"]["name"] == "DB freeze"

    async def test_panel_omits_suppression_when_unset(
        self, client: AsyncClient, app, auth_headers
    ):
        factory = app.state.session_factory
        async with factory() as session:
            incident = await IncidentRepo.create(
                session,
                TEST_ORG_ID,
                title="ok",
                description="ok",
            )
            await session.commit()
            incident_id = str(incident.id)

        resp = await client.get(
            f"/incidents/{incident_id}/paging", headers=auth_headers
        )
        assert resp.status_code == 200
        assert resp.json()["suppressed_by_maintenance_window"] is None


class TestRosterMemberTeamScoping:
    """A rotation member must be an active, non-deleted Admin/Operator who
    belongs to the roster's owning team. Enforced server-side on add + on team
    reparenting so direct API calls can't create invalid rosters."""

    async def _make_team(self, client, auth_headers, name):
        resp = await client.post(
            "/teams",
            json={"name": name, "slug": f"{name.lower()}-{uuid.uuid4().hex[:6]}"},
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text
        return resp.json()["id"]

    async def _make_roster(self, client, auth_headers, team_id, name="Primary"):
        resp = await client.post(
            "/rosters",
            json={
                "team_id": team_id,
                "name": name,
                "pattern": "daily",
                "pattern_length": 1,
                "anchor_date": "2026-05-04",
                "time_zone": "UTC",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text
        return resp.json()["id"]

    async def _make_user(self, app, *, role="operator", is_active=True, deleted=False):
        async with app.state.session_factory() as db:
            u = await UserRepo.create(
                db,
                username=f"rm-{uuid.uuid4().hex[:8]}",
                email=f"rm-{uuid.uuid4().hex[:8]}@test.com",
                password_hash="x",
                role=role,
                primary_org_id=TEST_ORG_ID,
            )
            await db.commit()
            uid = u.id
            if not is_active:
                await UserRepo.update_fields(db, uid, is_active=False)
                await db.commit()
            if deleted:
                await UserRepo.soft_delete(db, uid)
                await db.commit()
        return uid

    async def _join_team(self, client, auth_headers, team_id, user_id):
        resp = await client.post(
            f"/teams/{team_id}/members",
            json={"user_id": str(user_id)},
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text

    async def _add_member(self, client, auth_headers, roster_id, user_id, idx=0):
        return await client.post(
            f"/rosters/{roster_id}/members",
            json={"user_id": str(user_id), "position_index": idx},
            headers=auth_headers,
        )

    async def test_add_member_of_team_succeeds(self, client, app, auth_headers):
        team_id = await self._make_team(client, auth_headers, "Data")
        roster_id = await self._make_roster(client, auth_headers, team_id)
        uid = await self._make_user(app, role="operator")
        await self._join_team(client, auth_headers, team_id, uid)
        resp = await self._add_member(client, auth_headers, roster_id, uid)
        assert resp.status_code == 201, resp.text

    async def test_add_user_not_in_team_fails(self, client, app, auth_headers):
        team_id = await self._make_team(client, auth_headers, "Data")
        other_id = await self._make_team(client, auth_headers, "Web")
        roster_id = await self._make_roster(client, auth_headers, team_id)
        uid = await self._make_user(app, role="operator")
        # Joins a DIFFERENT team, not the roster's team.
        await self._join_team(client, auth_headers, other_id, uid)
        resp = await self._add_member(client, auth_headers, roster_id, uid)
        assert resp.status_code == 400
        assert "belong to the selected team" in resp.json()["detail"]

    async def test_add_viewer_in_team_fails(self, client, app, auth_headers):
        team_id = await self._make_team(client, auth_headers, "Data")
        roster_id = await self._make_roster(client, auth_headers, team_id)
        uid = await self._make_user(app, role="viewer")
        await self._join_team(client, auth_headers, team_id, uid)
        resp = await self._add_member(client, auth_headers, roster_id, uid)
        assert resp.status_code == 400
        assert "Admin or Operator" in resp.json()["detail"]

    async def test_add_inactive_user_fails(self, client, app, auth_headers):
        team_id = await self._make_team(client, auth_headers, "Data")
        roster_id = await self._make_roster(client, auth_headers, team_id)
        uid = await self._make_user(app, role="operator")
        await self._join_team(client, auth_headers, team_id, uid)
        # Deactivate after joining the team.
        async with app.state.session_factory() as db:
            await UserRepo.update_fields(db, uid, is_active=False)
            await db.commit()
        resp = await self._add_member(client, auth_headers, roster_id, uid)
        assert resp.status_code == 400
        assert "not active" in resp.json()["detail"]

    async def test_add_deleted_user_fails(self, client, app, auth_headers):
        team_id = await self._make_team(client, auth_headers, "Data")
        roster_id = await self._make_roster(client, auth_headers, team_id)
        uid = await self._make_user(app, role="operator")
        await self._join_team(client, auth_headers, team_id, uid)
        async with app.state.session_factory() as db:
            await UserRepo.soft_delete(db, uid)
            await db.commit()
        resp = await self._add_member(client, auth_headers, roster_id, uid)
        assert resp.status_code == 400
        assert resp.json()["detail"] == "User not found"

    async def test_update_roster_team_with_valid_members_succeeds(
        self, client, app, auth_headers
    ):
        team_a = await self._make_team(client, auth_headers, "Data")
        team_b = await self._make_team(client, auth_headers, "Web")
        roster_id = await self._make_roster(client, auth_headers, team_a)
        uid = await self._make_user(app, role="operator")
        # The member belongs to BOTH teams, so reparenting is allowed.
        await self._join_team(client, auth_headers, team_a, uid)
        await self._join_team(client, auth_headers, team_b, uid)
        assert (
            await self._add_member(client, auth_headers, roster_id, uid)
        ).status_code == 201
        resp = await client.put(
            f"/rosters/{roster_id}",
            json={"team_id": team_b},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["team_id"] == team_b

    async def test_update_roster_team_strands_members_fails(
        self, client, app, auth_headers
    ):
        team_a = await self._make_team(client, auth_headers, "Data")
        team_b = await self._make_team(client, auth_headers, "Web")
        roster_id = await self._make_roster(client, auth_headers, team_a)
        uid = await self._make_user(app, role="operator")
        await self._join_team(client, auth_headers, team_a, uid)  # only team A
        assert (
            await self._add_member(client, auth_headers, roster_id, uid)
        ).status_code == 201
        resp = await client.put(
            f"/rosters/{roster_id}",
            json={"team_id": team_b},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "belong to the selected team" in resp.json()["detail"]

    async def test_existing_roster_list_and_detail_still_render(
        self, client, app, auth_headers
    ):
        """Even if legacy data holds a member who isn't a team member, the
        read paths (list + members) must not break."""
        team_id = await self._make_team(client, auth_headers, "Data")
        roster_id = await self._make_roster(client, auth_headers, team_id)
        uid = await self._make_user(app, role="operator")
        # Insert a roster member directly (bypassing the route) to simulate
        # pre-existing invalid data — no team membership.
        async with app.state.session_factory() as db:
            await RosterRepo.add_member(
                db, TEST_ORG_ID, roster_id=uuid.UUID(roster_id), user_id=uid,
                position_index=0,
            )
            await db.commit()
        listed = await client.get("/rosters", headers=auth_headers)
        assert listed.status_code == 200
        assert any(r["id"] == roster_id for r in listed.json()["items"])
        members = await client.get(
            f"/rosters/{roster_id}/members", headers=auth_headers
        )
        assert members.status_code == 200
        assert members.json()["total"] == 1


class TestRosterOverrideTeamScoping(TestRosterMemberTeamScoping):
    """Coverage overrides obey the same eligibility rule as rotation members:
    the covering user must be an active, non-deleted Admin/Operator on the
    roster's team. Inherits the team/roster/user helpers from the member
    scoping suite."""

    async def _add_override(self, client, auth_headers, roster_id, user_id):
        return await client.post(
            f"/rosters/{roster_id}/overrides",
            json={
                "covering_user_id": str(user_id),
                "starts_at": "2026-07-01T00:00:00+00:00",
                "ends_at": "2026-07-02T00:00:00+00:00",
                "reason": "cover",
            },
            headers=auth_headers,
        )

    async def test_override_for_team_member_succeeds(self, client, app, auth_headers):
        team_id = await self._make_team(client, auth_headers, "Data")
        roster_id = await self._make_roster(client, auth_headers, team_id)
        uid = await self._make_user(app, role="operator")
        await self._join_team(client, auth_headers, team_id, uid)
        resp = await self._add_override(client, auth_headers, roster_id, uid)
        assert resp.status_code == 201, resp.text

    async def test_override_user_not_in_team_fails(self, client, app, auth_headers):
        team_id = await self._make_team(client, auth_headers, "Data")
        other_id = await self._make_team(client, auth_headers, "Web")
        roster_id = await self._make_roster(client, auth_headers, team_id)
        uid = await self._make_user(app, role="operator")
        await self._join_team(client, auth_headers, other_id, uid)  # wrong team
        resp = await self._add_override(client, auth_headers, roster_id, uid)
        assert resp.status_code == 400
        assert "belong to the selected team" in resp.json()["detail"]

    async def test_override_viewer_in_team_fails(self, client, app, auth_headers):
        team_id = await self._make_team(client, auth_headers, "Data")
        roster_id = await self._make_roster(client, auth_headers, team_id)
        uid = await self._make_user(app, role="viewer")
        await self._join_team(client, auth_headers, team_id, uid)
        resp = await self._add_override(client, auth_headers, roster_id, uid)
        assert resp.status_code == 400
        assert "Admin or Operator" in resp.json()["detail"]

    async def test_override_inactive_user_fails(self, client, app, auth_headers):
        team_id = await self._make_team(client, auth_headers, "Data")
        roster_id = await self._make_roster(client, auth_headers, team_id)
        uid = await self._make_user(app, role="operator")
        await self._join_team(client, auth_headers, team_id, uid)
        async with app.state.session_factory() as db:
            await UserRepo.update_fields(db, uid, is_active=False)
            await db.commit()
        resp = await self._add_override(client, auth_headers, roster_id, uid)
        assert resp.status_code == 400
        assert "not active" in resp.json()["detail"]

    async def test_override_deleted_user_fails(self, client, app, auth_headers):
        team_id = await self._make_team(client, auth_headers, "Data")
        roster_id = await self._make_roster(client, auth_headers, team_id)
        uid = await self._make_user(app, role="operator")
        await self._join_team(client, auth_headers, team_id, uid)
        async with app.state.session_factory() as db:
            await UserRepo.soft_delete(db, uid)
            await db.commit()
        resp = await self._add_override(client, auth_headers, roster_id, uid)
        assert resp.status_code == 400
        assert resp.json()["detail"] == "User not found"

    async def test_existing_override_list_still_renders(self, client, app, auth_headers):
        """Legacy overrides for a now-ineligible user must still list cleanly."""
        team_id = await self._make_team(client, auth_headers, "Data")
        roster_id = await self._make_roster(client, auth_headers, team_id)
        uid = await self._make_user(app, role="operator")  # never joined the team
        async with app.state.session_factory() as db:
            await RosterOverrideRepo.create(
                db,
                TEST_ORG_ID,
                roster_id=uuid.UUID(roster_id),
                covering_user_id=uid,
                starts_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
                ends_at=datetime(2026, 7, 2, tzinfo=timezone.utc),
                reason="legacy",
            )
            await db.commit()
        listed = await client.get(
            f"/rosters/{roster_id}/overrides", headers=auth_headers
        )
        assert listed.status_code == 200
        assert listed.json()["total"] == 1
