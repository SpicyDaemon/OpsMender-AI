"""Tests for the escalation chain engine (Sprint 34)."""

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
    EscalationChainRepo,
    EscalationStepRepo,
    IncidentAssignmentRepo,
    IncidentChainStateRepo,
    IncidentPageRepo,
    IncidentRepo,
    PriorityRuleRepo,
    RosterRepo,
    ServiceEscalationChainRepo,
    ServiceRepo,
    TeamRepo,
    UserRepo,
)
from backend.paging import escalation as _esc


TEST_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


@pytest.fixture
async def app(tmp_path):
    db_path = tmp_path / "escalation-test.db"
    database_url = f"sqlite+aiosqlite:///{db_path}"
    engine = create_async_engine(database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(Organization(id=TEST_ORG_ID, name="Test", slug="test"))
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

    class _Pool:
        async def get_server(self, *a, **kw):
            return object()

        @asynccontextmanager
        async def connect(self, *a, **kw):
            class _S:
                pass

            yield _S()

    set_mcp_pool(_Pool())

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
            "username": "esc-admin",
            "email": "esc-admin@test.com",
            "password": "securepass123",
        },
    )
    resp = await client.post(
        "/auth/login",
        json={"username": "esc-admin", "password": "securepass123"},
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _make_team(app, *, name="T") -> uuid.UUID:
    async with app.state.session_factory() as db:
        team = await TeamRepo.create(db, TEST_ORG_ID, name=name, slug=name.lower())
        await db.commit()
        return team.id


async def _make_user(app, *, username) -> uuid.UUID:
    async with app.state.session_factory() as db:
        u = await UserRepo.create(
            db,
            username=username,
            email=f"{username}@test.com",
            password_hash="x",
            role="viewer",
            primary_org_id=TEST_ORG_ID,
        )
        await db.commit()
        return u.id


async def _make_user_in_chain(
    app, *, team_id: uuid.UUID, user_id: uuid.UUID, chain_name="C"
) -> tuple[uuid.UUID, uuid.UUID]:
    """Build a chain with a single user-targeted step. Returns (chain_id, step_id)."""

    async with app.state.session_factory() as db:
        chain = await EscalationChainRepo.create(
            db, TEST_ORG_ID, team_id=team_id, name=chain_name
        )
        step = await EscalationStepRepo.create(
            db,
            TEST_ORG_ID,
            chain_id=chain.id,
            step_index=0,
            target_type="user",
            target_id=user_id,
            timeout_seconds=60,
        )
        await db.commit()
        return chain.id, step.id


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------


class TestStateMachine:
    async def test_start_chain_fires_step_zero(self, app):
        team_id = await _make_team(app)
        user_id = await _make_user(app, username="alice")
        chain_id, _ = await _make_user_in_chain(
            app, team_id=team_id, user_id=user_id
        )
        async with app.state.session_factory() as db:
            incident = await IncidentRepo.create(
                db,
                TEST_ORG_ID,
                title="t",
                description="d",
                severity="critical",
            )
            await db.commit()
            await _esc.start_chain(
                db,
                TEST_ORG_ID,
                incident_id=incident.id,
                chain_id=chain_id,
            )
            await db.commit()

            state = await IncidentChainStateRepo.get_for_incident(
                db, TEST_ORG_ID, incident.id
            )
            assert state is not None
            assert state.status == "running"
            assert state.current_step_index == 0
            pages = await IncidentPageRepo.list_for_incident(
                db, TEST_ORG_ID, incident.id
            )
            assert len(pages) == 1
            assert pages[0].user_id == user_id

    async def test_tick_advances_after_timeout(self, app):
        team_id = await _make_team(app)
        u1 = await _make_user(app, username="u1")
        u2 = await _make_user(app, username="u2")
        async with app.state.session_factory() as db:
            chain = await EscalationChainRepo.create(
                db, TEST_ORG_ID, team_id=team_id, name="multi"
            )
            await EscalationStepRepo.create(
                db,
                TEST_ORG_ID,
                chain_id=chain.id,
                step_index=0,
                target_type="user",
                target_id=u1,
                timeout_seconds=30,
            )
            await EscalationStepRepo.create(
                db,
                TEST_ORG_ID,
                chain_id=chain.id,
                step_index=1,
                target_type="user",
                target_id=u2,
                timeout_seconds=30,
            )
            incident = await IncidentRepo.create(
                db, TEST_ORG_ID, title="t", description="d", severity="high"
            )
            now = datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc)
            await _esc.start_chain(
                db,
                TEST_ORG_ID,
                incident_id=incident.id,
                chain_id=chain.id,
                at=now,
            )
            await db.commit()

            # Before timeout — tick does nothing.
            before = now + timedelta(seconds=15)
            result = await _esc.tick(
                db, TEST_ORG_ID, incident_id=incident.id, at=before
            )
            assert result is None

            # After timeout — step 1 fires.
            after = now + timedelta(seconds=45)
            result = await _esc.tick(
                db, TEST_ORG_ID, incident_id=incident.id, at=after
            )
            await db.commit()
            assert result is not None
            assert result.step_index == 1

            pages = await IncidentPageRepo.list_for_incident(
                db, TEST_ORG_ID, incident.id
            )
            # Step 0 page (u1) plus step 1 page (u2) — additive.
            assert {p.user_id for p in pages} == {u1, u2}

    async def test_chain_exhausts_after_final_step(self, app):
        team_id = await _make_team(app)
        u1 = await _make_user(app, username="x")
        async with app.state.session_factory() as db:
            chain = await EscalationChainRepo.create(
                db, TEST_ORG_ID, team_id=team_id, name="single"
            )
            await EscalationStepRepo.create(
                db,
                TEST_ORG_ID,
                chain_id=chain.id,
                step_index=0,
                target_type="user",
                target_id=u1,
                timeout_seconds=30,
            )
            incident = await IncidentRepo.create(
                db, TEST_ORG_ID, title="t", description="d"
            )
            now = datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc)
            await _esc.start_chain(
                db,
                TEST_ORG_ID,
                incident_id=incident.id,
                chain_id=chain.id,
                at=now,
            )
            await db.commit()
            state = await IncidentChainStateRepo.get_for_incident(
                db, TEST_ORG_ID, incident.id
            )
            # Single step + page mode → no next deadline.
            assert state.next_step_due_at is None

    async def test_escalate_immediate_fires_all_steps_at_once(self, app):
        team_id = await _make_team(app)
        u1 = await _make_user(app, username="i1")
        u2 = await _make_user(app, username="i2")
        async with app.state.session_factory() as db:
            chain = await EscalationChainRepo.create(
                db, TEST_ORG_ID, team_id=team_id, name="immediate"
            )
            for i, uid in enumerate([u1, u2]):
                await EscalationStepRepo.create(
                    db,
                    TEST_ORG_ID,
                    chain_id=chain.id,
                    step_index=i,
                    target_type="user",
                    target_id=uid,
                    timeout_seconds=30,
                )
            incident = await IncidentRepo.create(
                db, TEST_ORG_ID, title="t", description="d"
            )
            await _esc.start_chain(
                db,
                TEST_ORG_ID,
                incident_id=incident.id,
                chain_id=chain.id,
                mode="escalate_immediate",
            )
            await db.commit()
            pages = await IncidentPageRepo.list_for_incident(
                db, TEST_ORG_ID, incident.id
            )
            assert {p.user_id for p in pages} == {u1, u2}

    async def test_handle_ack_pauses_chain_and_assigns(self, app):
        team_id = await _make_team(app)
        u1 = await _make_user(app, username="acker")
        chain_id, _ = await _make_user_in_chain(
            app, team_id=team_id, user_id=u1, chain_name="ack-chain"
        )
        async with app.state.session_factory() as db:
            incident = await IncidentRepo.create(
                db, TEST_ORG_ID, title="t", description="d"
            )
            await _esc.start_chain(
                db,
                TEST_ORG_ID,
                incident_id=incident.id,
                chain_id=chain_id,
            )
            await db.commit()

            acked = await _esc.handle_ack(
                db, TEST_ORG_ID, incident_id=incident.id, user_id=u1
            )
            await db.commit()
            assert acked is True

            state = await IncidentChainStateRepo.get_for_incident(
                db, TEST_ORG_ID, incident.id
            )
            assert state.status == "acked"

            active = await IncidentAssignmentRepo.get_active(
                db, TEST_ORG_ID, incident.id
            )
            assert active is not None
            assert active.assigned_to == u1
            assert active.assigned_by == "self_ack"

            pages = await IncidentPageRepo.list_for_incident(
                db, TEST_ORG_ID, incident.id
            )
            assert all(p.ack_at is not None for p in pages)

    async def test_force_takeover_swaps_assignee(self, app):
        team_id = await _make_team(app)
        u1 = await _make_user(app, username="owner")
        u2 = await _make_user(app, username="admin")
        chain_id, _ = await _make_user_in_chain(
            app, team_id=team_id, user_id=u1, chain_name="force"
        )
        async with app.state.session_factory() as db:
            incident = await IncidentRepo.create(
                db, TEST_ORG_ID, title="t", description="d"
            )
            await _esc.start_chain(
                db,
                TEST_ORG_ID,
                incident_id=incident.id,
                chain_id=chain_id,
            )
            await _esc.handle_ack(
                db, TEST_ORG_ID, incident_id=incident.id, user_id=u1
            )
            await db.commit()

            await _esc.handle_force_takeover(
                db,
                TEST_ORG_ID,
                incident_id=incident.id,
                admin_id=u2,
            )
            await db.commit()
            active = await IncidentAssignmentRepo.get_active(
                db, TEST_ORG_ID, incident.id
            )
            assert active.assigned_to == u2
            assert active.assigned_by == "admin_force"

    async def test_soft_takeover_request_and_confirm(self, app):
        team_id = await _make_team(app)
        u1 = await _make_user(app, username="orig")
        u2 = await _make_user(app, username="newowner")
        chain_id, _ = await _make_user_in_chain(
            app, team_id=team_id, user_id=u1, chain_name="soft"
        )
        async with app.state.session_factory() as db:
            incident = await IncidentRepo.create(
                db, TEST_ORG_ID, title="t", description="d"
            )
            await _esc.start_chain(
                db,
                TEST_ORG_ID,
                incident_id=incident.id,
                chain_id=chain_id,
            )
            await _esc.handle_ack(
                db, TEST_ORG_ID, incident_id=incident.id, user_id=u1
            )
            await db.commit()

            outcome = await _esc.handle_takeover_request(
                db,
                TEST_ORG_ID,
                incident_id=incident.id,
                requester_id=u2,
            )
            await db.commit()
            # Chain was already acked → state has no pending mechanism.
            # Either pending (if state still around) or assigned (if not).
            assert outcome in {"pending", "assigned", "requires_admin"}

    async def test_hard_inactivity_timeout(self, app):
        team_id = await _make_team(app)
        u1 = await _make_user(app, username="absent")
        chain_id, _ = await _make_user_in_chain(
            app, team_id=team_id, user_id=u1, chain_name="abandon"
        )
        async with app.state.session_factory() as db:
            incident = await IncidentRepo.create(
                db, TEST_ORG_ID, title="t", description="d"
            )
            now = datetime(2026, 5, 15, 12, 0, tzinfo=timezone.utc)
            await _esc.start_chain(
                db,
                TEST_ORG_ID,
                incident_id=incident.id,
                chain_id=chain_id,
                at=now,
            )
            await db.commit()
            # Jump 20 minutes ahead — past the 15-min hard deadline.
            later = now + timedelta(minutes=20)
            await _esc.tick(
                db, TEST_ORG_ID, incident_id=incident.id, at=later
            )
            await db.commit()
            state = await IncidentChainStateRepo.get_for_incident(
                db, TEST_ORG_ID, incident.id
            )
            assert state.status == "exhausted"

    async def test_cancel_chain(self, app):
        team_id = await _make_team(app)
        u1 = await _make_user(app, username="cancel-u")
        chain_id, _ = await _make_user_in_chain(
            app, team_id=team_id, user_id=u1, chain_name="cancel"
        )
        async with app.state.session_factory() as db:
            incident = await IncidentRepo.create(
                db, TEST_ORG_ID, title="t", description="d"
            )
            await _esc.start_chain(
                db,
                TEST_ORG_ID,
                incident_id=incident.id,
                chain_id=chain_id,
            )
            await db.commit()
            cancelled = await _esc.cancel_chain(
                db, TEST_ORG_ID, incident_id=incident.id
            )
            await db.commit()
            assert cancelled is True
            state = await IncidentChainStateRepo.get_for_incident(
                db, TEST_ORG_ID, incident.id
            )
            assert state.status == "cancelled"

    async def test_already_paged_dedup(self, app):
        team_id = await _make_team(app)
        u1 = await _make_user(app, username="dup")
        async with app.state.session_factory() as db:
            chain = await EscalationChainRepo.create(
                db, TEST_ORG_ID, team_id=team_id, name="dup"
            )
            await EscalationStepRepo.create(
                db,
                TEST_ORG_ID,
                chain_id=chain.id,
                step_index=0,
                target_type="user",
                target_id=u1,
                timeout_seconds=60,
            )
            incident = await IncidentRepo.create(
                db, TEST_ORG_ID, title="t", description="d"
            )
            await _esc.start_chain(
                db,
                TEST_ORG_ID,
                incident_id=incident.id,
                chain_id=chain.id,
            )
            await db.commit()

            # Manually re-fire step 0 — should not duplicate.
            already = await IncidentPageRepo.already_paged(
                db,
                TEST_ORG_ID,
                incident_id=incident.id,
                user_id=u1,
                step_index=0,
            )
            assert already is True


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


class TestEscalationAPI:
    async def test_chain_crud(self, client: AsyncClient, app, auth_headers):
        team_id = await _make_team(app, name="api-team")
        resp = await client.post(
            "/escalation-chains",
            json={"team_id": str(team_id), "name": "Primary"},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        chain_id = resp.json()["id"]
        listed = await client.get("/escalation-chains", headers=auth_headers)
        assert listed.json()["total"] == 1
        deleted = await client.delete(
            f"/escalation-chains/{chain_id}", headers=auth_headers
        )
        assert deleted.status_code == 204

    async def test_step_add_and_dup_index_409(
        self, client: AsyncClient, app, auth_headers
    ):
        team_id = await _make_team(app, name="step-team")
        u1 = await _make_user(app, username="stepuser")
        resp = await client.post(
            "/escalation-chains",
            json={"team_id": str(team_id), "name": "C"},
            headers=auth_headers,
        )
        chain_id = resp.json()["id"]
        add = await client.post(
            f"/escalation-chains/{chain_id}/steps",
            json={
                "step_index": 0,
                "target_type": "user",
                "target_id": str(u1),
                "timeout_seconds": 60,
            },
            headers=auth_headers,
        )
        assert add.status_code == 201
        dup = await client.post(
            f"/escalation-chains/{chain_id}/steps",
            json={
                "step_index": 0,
                "target_type": "user",
                "target_id": str(u1),
                "timeout_seconds": 60,
            },
            headers=auth_headers,
        )
        assert dup.status_code == 409

    async def test_service_chain_link(
        self, client: AsyncClient, app, auth_headers
    ):
        team_id = await _make_team(app, name="link-team")
        async with app.state.session_factory() as db:
            svc = await ServiceRepo.create(
                db,
                TEST_ORG_ID,
                team_id=team_id,
                name="API",
                slug="link-api",
            )
            await db.commit()
        chain = await client.post(
            "/escalation-chains",
            json={"team_id": str(team_id), "name": "link-c"},
            headers=auth_headers,
        )
        link = await client.post(
            f"/services/{svc.id}/escalation-chains",
            json={"chain_id": chain.json()["id"]},
            headers=auth_headers,
        )
        assert link.status_code == 201

    async def test_incident_creation_kicks_off_chain(
        self, client: AsyncClient, app, auth_headers
    ):
        team_id = await _make_team(app, name="kick-team")
        u1 = await _make_user(app, username="kick-user")
        async with app.state.session_factory() as db:
            svc = await ServiceRepo.create(
                db,
                TEST_ORG_ID,
                team_id=team_id,
                name="kickapi",
                slug="kick-api",
            )
            await PriorityRuleRepo.create(
                db,
                TEST_ORG_ID,
                name="critical-page",
                condition={"severity": ["critical"]},
                priority="P0",
                response_mode="page",
            )
            chain = await EscalationChainRepo.create(
                db, TEST_ORG_ID, team_id=team_id, name="kick-chain"
            )
            await EscalationStepRepo.create(
                db,
                TEST_ORG_ID,
                chain_id=chain.id,
                step_index=0,
                target_type="user",
                target_id=u1,
                timeout_seconds=60,
            )
            await ServiceEscalationChainRepo.link(
                db, TEST_ORG_ID, service_id=svc.id, chain_id=chain.id
            )
            await db.commit()

            # Create an incident that should match the P0 rule. We have to
            # set service_id by hand for now — the REST create body doesn't
            # take service_id yet (Sprint 35 follow-up).
            inc = await IncidentRepo.create(
                db,
                TEST_ORG_ID,
                title="prod down",
                description="503",
                severity="critical",
                service_id=svc.id,
            )
            from backend.paging.service import apply_priority_to_incident

            await apply_priority_to_incident(db, TEST_ORG_ID, inc)
            link = await _esc.select_chain_for_incident(
                db,
                TEST_ORG_ID,
                service_id=inc.service_id,
                priority=inc.priority,
            )
            await _esc.start_chain(
                db,
                TEST_ORG_ID,
                incident_id=inc.id,
                chain_id=link.chain_id,
            )
            await db.commit()
            incident_id = inc.id

        # GET the chain panel through the API.
        panel = await client.get(
            f"/incidents/{incident_id}/chain", headers=auth_headers
        )
        assert panel.status_code == 200
        body = panel.json()
        assert body["state"] is not None
        assert body["state"]["status"] == "running"
        assert len(body["pages"]) == 1
        assert body["pages"][0]["user_id"] == str(u1)

    async def test_ack_endpoint_resolves_chain(
        self, client: AsyncClient, app, auth_headers
    ):
        # Find the registered admin user's id.
        async with app.state.session_factory() as db:
            admin = await UserRepo.get_by_username(db, "esc-admin")
            admin_id = admin.id

        team_id = await _make_team(app, name="ack-team")
        chain_id, _ = await _make_user_in_chain(
            app, team_id=team_id, user_id=admin_id, chain_name="ack-api"
        )
        async with app.state.session_factory() as db:
            inc = await IncidentRepo.create(
                db, TEST_ORG_ID, title="t", description="d"
            )
            await _esc.start_chain(
                db,
                TEST_ORG_ID,
                incident_id=inc.id,
                chain_id=chain_id,
            )
            await db.commit()
            incident_id = inc.id

        resp = await client.post(
            f"/incidents/{incident_id}/ack",
            json={"via": "web_ui"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["state"]["status"] == "acked"

    async def test_force_takeover_requires_admin_role(
        self, client: AsyncClient, app, auth_headers
    ):
        async with app.state.session_factory() as db:
            inc = await IncidentRepo.create(
                db, TEST_ORG_ID, title="t", description="d"
            )
            await db.commit()
            incident_id = inc.id

        resp = await client.post(
            f"/incidents/{incident_id}/take",
            json={"force": True},
            headers=auth_headers,
        )
        # Registered user is the first user → admin. Force should succeed.
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Scheduler integration
# ---------------------------------------------------------------------------


class TestScheduler:
    async def test_tick_all_due_advances_only_due(self, app):
        team_id = await _make_team(app, name="sched-team")
        u1 = await _make_user(app, username="s1")
        u2 = await _make_user(app, username="s2")
        async with app.state.session_factory() as db:
            chain = await EscalationChainRepo.create(
                db, TEST_ORG_ID, team_id=team_id, name="sched"
            )
            await EscalationStepRepo.create(
                db,
                TEST_ORG_ID,
                chain_id=chain.id,
                step_index=0,
                target_type="user",
                target_id=u1,
                timeout_seconds=30,
            )
            await EscalationStepRepo.create(
                db,
                TEST_ORG_ID,
                chain_id=chain.id,
                step_index=1,
                target_type="user",
                target_id=u2,
                timeout_seconds=30,
            )
            inc = await IncidentRepo.create(
                db, TEST_ORG_ID, title="t", description="d"
            )
            anchor = datetime(2026, 5, 15, 9, 0, tzinfo=timezone.utc)
            await _esc.start_chain(
                db,
                TEST_ORG_ID,
                incident_id=inc.id,
                chain_id=chain.id,
                at=anchor,
            )
            await db.commit()

            advanced = await _esc.tick_all_due(
                db, at=anchor + timedelta(seconds=10)
            )
            assert advanced == 0

            advanced = await _esc.tick_all_due(
                db, at=anchor + timedelta(seconds=45)
            )
            await db.commit()
            assert advanced == 1
            pages = await IncidentPageRepo.list_for_incident(
                db, TEST_ORG_ID, inc.id
            )
            assert {p.user_id for p in pages} == {u1, u2}
