"""Ownership and closure lifecycle (D-021 acknowledgement lock, X1a).

Every close path stops paging; every ownership path is an acknowledgement;
an acknowledgement is a live lock that resumes the next level on release or
15 minutes without assignee activity; snooze is a timed pause; only the
current owner (or an admin force) can confirm a takeover.

Timing is driven through the engine's ``at`` parameter; no test sleeps.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.parse
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.app import create_app
from backend.api.auth import hash_password
from backend.api.deps import get_db, set_mcp_pool, set_session_factory
from backend.config_loader import set_env_path
from backend.db.models import (
    Base,
    IncidentChainState,
    IncidentComment,
    IncidentPage,
    NotificationEscalation,
    Organization,
)
from backend.db.repos import (
    BotConnectorRepo,
    BotUserLinkRepo,
    EscalationChainRepo,
    EscalationStepRepo,
    IncidentAssignmentRepo,
    IncidentChainStateRepo,
    IncidentRepo,
    IngestTokenRepo,
    NotificationEscalationRepo,
    ServiceEscalationChainRepo,
    ServiceRepo,
    TeamRepo,
    UserRepo,
)
from backend.ingest.service import generate_token, hash_token
from backend.paging import escalation as _esc

TEST_ORG_ID = uuid.UUID("00000000-0000-0000-0000-00000000c0de")
OTHER_ORG_ID = uuid.UUID("00000000-0000-0000-0000-00000000beef")
SIGNING_SECRET = "lifecycle-signing-secret"
PASSWORD = "securepass123"
T0 = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
LOCK = timedelta(seconds=_esc.ACK_LOCK_INACTIVITY_SECONDS)


@pytest.fixture
async def app(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'lifecycle.db'}"
    engine = create_async_engine(database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(Organization(id=TEST_ORG_ID, name="Lifecycle", slug="lifecycle"))
        session.add(Organization(id=OTHER_ORG_ID, name="Other", slug="other"))
        await session.commit()
    set_session_factory(factory)
    env = tmp_path / ".env"
    env.write_text(
        "OPSMENDER_TIER=2\n"
        "OPSMENDER_LOG_LEVEL=INFO\n"
        "OPSMENDER_AUDIT_LOG=./logs/audit.jsonl\n"
        "OPSMENDER_JWT_SECRET=test-secret\n"
        f"OPSMENDER_DATABASE_URL={database_url}\n"
        f"OPSMENDER_MCP_SERVERS_JSON={json.dumps([])}\n"
    )
    set_env_path(env)
    application = create_app()
    application.state.session_factory = factory

    class _Pool:
        async def get_server(self, *a, **kw):
            return object()

        @asynccontextmanager
        async def connect(self, *a, **kw):
            yield object()

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


# ── Fixtures ────────────────────────────────────────────────────────────────


async def _user(
    app, name: str, *, role: str = "operator", org_id: uuid.UUID = TEST_ORG_ID
) -> uuid.UUID:
    async with app.state.session_factory() as db:
        user = await UserRepo.create(
            db,
            username=name,
            email=f"{name}@test.com",
            password_hash=hash_password(PASSWORD),
            role=role,
            primary_org_id=org_id,
        )
        await UserRepo.add_to_organization(
            db, user_id=user.id, org_id=org_id, role=role
        )
        await db.commit()
        return user.id


async def _headers(client, name: str) -> dict[str, str]:
    resp = await client.post(
        "/auth/login", json={"username": name, "password": PASSWORD}
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _chain(app, users: list[uuid.UUID], *, timeout: int = 60) -> uuid.UUID:
    async with app.state.session_factory() as db:
        team = await TeamRepo.create(
            db, TEST_ORG_ID, name="Team", slug=f"team-{uuid.uuid4().hex[:8]}"
        )
        chain = await EscalationChainRepo.create(
            db, TEST_ORG_ID, team_id=team.id, name=f"chain-{uuid.uuid4().hex[:6]}"
        )
        for index, user_id in enumerate(users):
            await EscalationStepRepo.create(
                db,
                TEST_ORG_ID,
                chain_id=chain.id,
                step_index=index,
                target_type="user",
                target_id=user_id,
                timeout_seconds=timeout,
            )
        await db.commit()
        return chain.id


async def _incident(
    app,
    chain_id: uuid.UUID,
    *,
    at: datetime = T0,
    title: str = "lifecycle",
    external_id: str | None = None,
) -> uuid.UUID:
    async with app.state.session_factory() as db:
        incident = await IncidentRepo.create(
            db,
            TEST_ORG_ID,
            title=title,
            description="d",
            external_id=external_id,
            external_source="generic" if external_id else None,
        )
        await _esc.start_chain(
            db, TEST_ORG_ID, incident_id=incident.id, chain_id=chain_id, at=at
        )
        await db.commit()
        return incident.id


async def _state(app, incident_id: uuid.UUID) -> IncidentChainState:
    async with app.state.session_factory() as db:
        return (
            await db.execute(
                select(IncidentChainState).where(
                    IncidentChainState.incident_id == incident_id
                )
            )
        ).scalar_one()


async def _recorded_pages(app, incident_id: uuid.UUID) -> list[tuple[uuid.UUID, int]]:
    async with app.state.session_factory() as db:
        rows = (
            await db.execute(
                select(IncidentPage).where(
                    IncidentPage.incident_id == incident_id,
                    IncidentPage.channel == "recorded",
                )
            )
        ).scalars()
        return sorted(
            ((row.user_id, row.step_index) for row in rows),
            key=lambda page: (page[1], str(page[0])),
        )


async def _owner(app, incident_id: uuid.UUID) -> uuid.UUID | None:
    async with app.state.session_factory() as db:
        active = await IncidentAssignmentRepo.get_active(db, TEST_ORG_ID, incident_id)
        return active.assigned_to if active is not None else None


async def _stage(app, incident_id: uuid.UUID, user_id: uuid.UUID) -> uuid.UUID:
    """A running staged notification escalation for the incident."""
    async with app.state.session_factory() as db:
        stage = await NotificationEscalationRepo.create(
            db,
            TEST_ORG_ID,
            incident_id=incident_id,
            user_id=user_id,
            priority="P1",
            stages=[{"channel_id": "x", "delay_seconds": 60}] * 2,
        )
        stage.next_stage_due_at = T0 + timedelta(minutes=1)
        await db.commit()
        return stage.id


async def _stage_status(app, stage_id: uuid.UUID) -> str:
    async with app.state.session_factory() as db:
        return (await db.get(NotificationEscalation, stage_id)).status


async def _run(app, fn, *args, **kwargs):
    async with app.state.session_factory() as db:
        result = await fn(db, TEST_ORG_ID, *args, **kwargs)
        await db.commit()
        return result


async def _tick(app, at: datetime) -> int:
    async with app.state.session_factory() as db:
        changed = await _esc.tick_all_due(db, at=at)
        await db.commit()
        return changed


async def _comments(app, incident_id: uuid.UUID) -> list[str]:
    async with app.state.session_factory() as db:
        rows = (
            await db.execute(
                select(IncidentComment)
                .where(IncidentComment.incident_id == incident_id)
                .order_by(IncidentComment.created_at)
            )
        ).scalars()
        return [row.body for row in rows]


@dataclass
class World:
    app: object
    client: AsyncClient
    admin: dict[str, str]
    admin_id: uuid.UUID
    level1: uuid.UUID
    level2: uuid.UUID
    level3: uuid.UUID


@pytest.fixture
async def world(app, client) -> World:
    admin_id = await _user(app, "lc-admin", role="admin")
    return World(
        app=app,
        client=client,
        admin=await _headers(client, "lc-admin"),
        admin_id=admin_id,
        level1=await _user(app, "lc-l1"),
        level2=await _user(app, "lc-l2"),
        level3=await _user(app, "lc-l3"),
    )


async def _three_levels(w: World, **kw) -> uuid.UUID:
    chain_id = await _chain(w.app, [w.level1, w.level2, w.level3])
    return await _incident(w.app, chain_id, **kw)


def _slack_signed(body: bytes) -> dict[str, str]:
    ts = str(int(time.time()))
    digest = hmac.new(
        SIGNING_SECRET.encode(), f"v0:{ts}:{body.decode()}".encode(), hashlib.sha256
    ).hexdigest()
    return {
        "X-Slack-Request-Timestamp": ts,
        "X-Slack-Signature": f"v0={digest}",
        "Content-Type": "application/x-www-form-urlencoded",
    }


async def _slack(
    w: World, command: str, text: str, slack_user: str, user_id: uuid.UUID
):
    async with w.app.state.session_factory() as db:
        connector = (await BotConnectorRepo.list_all(db, TEST_ORG_ID)) or []
        connector = next((c for c in connector if c.platform == "slack"), None)
        if connector is None:
            connector = await BotConnectorRepo.create(
                db,
                TEST_ORG_ID,
                name="slack",
                platform="slack",
                credentials={"signing_secret": SIGNING_SECRET, "bot_token": "xoxb-t"},
                allowed_capabilities=["paging"],
                status="configured",
                is_enabled=True,
                native_actions_enabled=True,
            )
        if (
            await BotUserLinkRepo.get_by_platform_user(
                db, TEST_ORG_ID, connector_id=connector.id, platform_user_id=slack_user
            )
            is None
        ):
            await BotUserLinkRepo.create(
                db,
                TEST_ORG_ID,
                connector_id=connector.id,
                platform_user_id=slack_user,
                opsmender_user_id=user_id,
            )
        await db.commit()
    body = urllib.parse.urlencode(
        {"command": command, "text": text, "user_id": slack_user, "team_id": "T1"}
    ).encode()
    resp = await w.client.post(
        "/bot/slack/commands", content=body, headers=_slack_signed(body)
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["text"]


# ── Chain states used by the close matrix ───────────────────────────────────


async def _make_running(w: World, incident_id: uuid.UUID) -> None:
    assert (await _state(w.app, incident_id)).status == "running"


async def _make_paused(w: World, incident_id: uuid.UUID) -> None:
    await _run(
        w.app,
        _esc.snooze,
        incident_id=incident_id,
        actor_id=w.level1,
        until=T0 + timedelta(minutes=30),
        at=T0 + timedelta(minutes=1),
    )
    assert (await _state(w.app, incident_id)).status == "paused"


async def _make_acked(w: World, incident_id: uuid.UUID) -> None:
    await _run(
        w.app,
        _esc.acknowledge,
        incident_id=incident_id,
        assignee_id=w.level1,
        at=T0 + timedelta(minutes=1),
    )
    state = await _state(w.app, incident_id)
    assert state.status == "acked" and state.finished_at is None


# ── Close paths ─────────────────────────────────────────────────────────────


async def _close_patch(w: World, incident_id: uuid.UUID) -> None:
    resp = await w.client.patch(
        f"/incidents/{incident_id}", json={"status": "resolved"}, headers=w.admin
    )
    assert resp.status_code == 200, resp.text


async def _close_bulk(w: World, incident_id: uuid.UUID) -> None:
    resp = await w.client.post(
        "/incidents/bulk",
        json={"action": "resolve", "incident_ids": [str(incident_id)]},
        headers=w.admin,
    )
    assert resp.status_code == 200, resp.text


async def _close_keypad3(w: World, incident_id: uuid.UUID) -> None:
    from backend.api.routes.voice import encode_voice_ack_token

    token = encode_voice_ack_token(
        org_id=TEST_ORG_ID, incident_id=incident_id, user_id=w.level1
    )
    resp = await w.client.post(f"/paging/voice/ack/{token}", data={"Digits": "3"})
    assert "Incident resolved" in resp.text


async def _close_recovery(w: World, incident_id: uuid.UUID) -> None:
    raw = generate_token()
    async with w.app.state.session_factory() as db:
        await IngestTokenRepo.create(
            db,
            TEST_ORG_ID,
            name=f"recovery-{uuid.uuid4().hex[:6]}",
            provider="generic",
            token_hash=hash_token(raw),
        )
        incident = await IncidentRepo.get_by_id(db, TEST_ORG_ID, incident_id)
        await db.commit()
    resp = await w.client.post(
        "/incidents/ingest",
        headers={"X-OpsMender-Token": raw},
        json={"title": "clear", "id": incident.external_id, "status": "resolved"},
    )
    assert resp.json()["dedup_action"] == "updated", resp.text


async def _close_chat(w: World, incident_id: uuid.UUID) -> None:
    from backend.bots.actions import IncidentActionTokenClaims, execute_incident_action

    async with w.app.state.session_factory() as db:
        result = await execute_incident_action(
            db,
            claims=IncidentActionTokenClaims(
                org_id=TEST_ORG_ID,
                incident_id=incident_id,
                action="resolve",
                channel_id=None,
                message_id=None,
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                nonce=uuid.uuid4().hex,
            ),
            actor_user_id=w.admin_id,
        )
        await db.commit()
    assert result.status == "resolved"


async def _close_slack(w: World, incident_id: uuid.UUID) -> None:
    text = await _slack(w, "/resolve", str(incident_id), "U_ADMIN", w.admin_id)
    assert "resolved" in text


async def _close_combine(w: World, incident_id: uuid.UUID) -> None:
    async with w.app.state.session_factory() as db:
        primary = await IncidentRepo.create(
            db, TEST_ORG_ID, title="primary", description="p"
        )
        await db.commit()
    resp = await w.client.post(
        f"/incidents/{primary.id}/combine",
        json={"secondary_ids": [str(incident_id)]},
        headers=w.admin,
    )
    assert resp.status_code == 200, resp.text


CLOSE_PATHS = {
    "web-patch": _close_patch,
    "bulk-resolve": _close_bulk,
    "keypad-3": _close_keypad3,
    "recovery": _close_recovery,
    "chat-resolve": _close_chat,
    "slack-resolve": _close_slack,
    "combine": _close_combine,
}
CHAIN_STATES = {
    "running": _make_running,
    "paused": _make_paused,
    "acked": _make_acked,
}


@pytest.mark.parametrize("state_name", list(CHAIN_STATES))
@pytest.mark.parametrize("path", list(CLOSE_PATHS))
async def test_l01_every_close_path_stops_the_chain_and_stages(world, path, state_name):
    incident_id = await _three_levels(world, external_id=f"lc-{uuid.uuid4().hex[:8]}")
    await CHAIN_STATES[state_name](world, incident_id)
    stage_id = await _stage(world.app, incident_id, world.level1)
    pages_before = await _recorded_pages(world.app, incident_id)

    await CLOSE_PATHS[path](world, incident_id)

    state = await _state(world.app, incident_id)
    assert state.status == "cancelled"
    assert state.finished_at is not None
    assert state.next_step_due_at is None and state.paused_until is None
    assert await _stage_status(world.app, stage_id) == "resolved"
    # Nothing can bring it back: a day of scheduler ticks pages nobody.
    for minutes in (2, 16, 31, 60 * 24):
        await _tick(world.app, T0 + timedelta(minutes=minutes))
    assert await _recorded_pages(world.app, incident_id) == pages_before


# ── Ownership paths ─────────────────────────────────────────────────────────


async def _own_ack(w: World, incident_id, user_id, headers):
    resp = await w.client.post(
        f"/incidents/{incident_id}/ack", json={"via": "web_ui"}, headers=headers
    )
    assert resp.status_code == 200, resp.text


async def _own_bulk(w: World, incident_id, user_id, headers):
    resp = await w.client.post(
        "/incidents/bulk",
        json={"action": "acknowledge", "incident_ids": [str(incident_id)]},
        headers=headers,
    )
    assert resp.json()["succeeded"] == 1, resp.text


async def _own_take(w: World, incident_id, user_id, headers):
    resp = await w.client.post(
        f"/incidents/{incident_id}/assign", json={}, headers=headers
    )
    assert resp.status_code == 200, resp.text


async def _own_keypad1(w: World, incident_id, user_id, headers):
    from backend.api.routes.voice import encode_voice_ack_token

    token = encode_voice_ack_token(
        org_id=TEST_ORG_ID, incident_id=incident_id, user_id=user_id
    )
    resp = await w.client.post(f"/paging/voice/ack/{token}", data={"Digits": "1"})
    assert "You are now the owner" in resp.text


async def _own_chat(w: World, incident_id, user_id, headers):
    from backend.bots.actions import IncidentActionTokenClaims, execute_incident_action

    async with w.app.state.session_factory() as db:
        result = await execute_incident_action(
            db,
            claims=IncidentActionTokenClaims(
                org_id=TEST_ORG_ID,
                incident_id=incident_id,
                action="acknowledge",
                channel_id=None,
                message_id=None,
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                nonce=uuid.uuid4().hex,
            ),
            actor_user_id=user_id,
        )
        await db.commit()
    assert result.status == "acknowledged"


async def _own_slack(w: World, incident_id, user_id, headers):
    text = await _slack(w, "/ack", str(incident_id), "U_L1", user_id)
    assert "acknowledged" in text


OWN_PATHS = {
    "ack": _own_ack,
    "bulk-ack": _own_bulk,
    "take": _own_take,
    "keypad-1": _own_keypad1,
    "chat-ack": _own_chat,
    "slack-ack": _own_slack,
}


@pytest.mark.parametrize("path", list(OWN_PATHS))
async def test_l02_every_ownership_path_acknowledges(world, path):
    incident_id = await _three_levels(world, at=datetime.now(timezone.utc))
    stage_id = await _stage(world.app, incident_id, world.level1)
    headers = await _headers(world.client, "lc-l1")

    await OWN_PATHS[path](world, incident_id, world.level1, headers)

    assert await _owner(world.app, incident_id) == world.level1
    state = await _state(world.app, incident_id)
    assert state.status == "acked"
    assert state.finished_at is None
    assert state.last_activity_at is not None
    assert await _stage_status(world.app, stage_id) == "acked"
    async with world.app.state.session_factory() as db:
        incident = await IncidentRepo.get_by_id(db, TEST_ORG_ID, incident_id)
        first_ack = incident.acknowledged_at
    assert first_ack is not None

    # Acknowledging again changes nothing and never re-stamps the first ack.
    if path in {"ack", "take"}:
        await OWN_PATHS[path](world, incident_id, world.level1, headers)
    async with world.app.state.session_factory() as db:
        incident = await IncidentRepo.get_by_id(db, TEST_ORG_ID, incident_id)
        assert incident.acknowledged_at == first_ack
        rows = await IncidentAssignmentRepo.list_for_incident(
            db, TEST_ORG_ID, incident_id
        )
        assert len(rows) == 1

    # The lock holds: nothing escalates before it lapses.
    lapse = state.next_step_due_at.replace(tzinfo=timezone.utc)
    await _tick(world.app, lapse - timedelta(seconds=1))
    assert await _recorded_pages(world.app, incident_id) == [(world.level1, 0)]


async def test_l02_assigning_someone_else_never_assigns_the_actor(world):
    incident_id = await _three_levels(world, at=datetime.now(timezone.utc))
    resp = await world.client.post(
        f"/incidents/{incident_id}/assign",
        json={"user_id": str(world.level2)},
        headers=world.admin,
    )
    assert resp.status_code == 200, resp.text
    assert await _owner(world.app, incident_id) == world.level2
    assert (await _state(world.app, incident_id)).status == "acked"

    other = await _three_levels(world, at=datetime.now(timezone.utc))
    resp = await world.client.post(
        "/incidents/bulk",
        json={
            "action": "acknowledge",
            "incident_ids": [str(other)],
            "user_id": str(world.level3),
        },
        headers=world.admin,
    )
    assert resp.json()["succeeded"] == 1, resp.text
    assert await _owner(world.app, other) == world.level3
    comments = await _comments(world.app, other)
    assert any("Assigned the incident to lc-l3" in c for c in comments)


async def test_l02_ack_respects_someone_elses_live_lock(world):
    incident_id = await _three_levels(world, at=datetime.now(timezone.utc))
    await _run(
        world.app, _esc.acknowledge, incident_id=incident_id, assignee_id=world.level1
    )
    resp = await world.client.post(
        f"/incidents/{incident_id}/ack",
        json={"via": "web_ui"},
        headers=await _headers(world.client, "lc-l2"),
    )
    assert resp.status_code == 409
    assert await _owner(world.app, incident_id) == world.level1


async def test_l02_acknowledging_a_closed_incident_is_rejected(world):
    incident_id = await _three_levels(world, at=datetime.now(timezone.utc))
    await _close_patch(world, incident_id)
    headers = await _headers(world.client, "lc-l1")
    ack = await world.client.post(
        f"/incidents/{incident_id}/ack", json={"via": "web_ui"}, headers=headers
    )
    take = await world.client.post(
        f"/incidents/{incident_id}/assign", json={}, headers=headers
    )
    assert ack.status_code == take.status_code == 409
    assert await _owner(world.app, incident_id) is None


# ── L03: lock timing ────────────────────────────────────────────────────────


async def test_l03_lock_lapses_after_exactly_fifteen_minutes(world):
    incident_id = await _three_levels(world)
    ack_at = T0 + timedelta(minutes=1)
    await _run(
        world.app,
        _esc.acknowledge,
        incident_id=incident_id,
        assignee_id=world.level1,
        at=ack_at,
    )
    deadline = ack_at + LOCK

    await _tick(world.app, deadline - timedelta(milliseconds=1))
    assert await _recorded_pages(world.app, incident_id) == [(world.level1, 0)]
    assert await _owner(world.app, incident_id) == world.level1

    await _tick(world.app, deadline)
    assert await _recorded_pages(world.app, incident_id) == [
        (world.level1, 0),
        (world.level2, 1),
    ]
    assert await _owner(world.app, incident_id) is None
    state = await _state(world.app, incident_id)
    assert state.status == "running" and state.current_step_index == 1

    # A repeated tick at the same instant is inert.
    assert await _tick(world.app, deadline) == 0
    assert len(await _recorded_pages(world.app, incident_id)) == 2
    assert any(
        "No activity from lc-l1" in c for c in await _comments(world.app, incident_id)
    )


async def test_l03_activity_at_minute_fourteen_moves_the_deadline(world):
    incident_id = await _three_levels(world)
    await _run(
        world.app,
        _esc.acknowledge,
        incident_id=incident_id,
        assignee_id=world.level1,
        at=T0,
    )
    assert await _run(
        world.app,
        _esc.record_assignee_activity,
        incident_id=incident_id,
        actor_id=world.level1,
        at=T0 + timedelta(minutes=14),
    )
    await _tick(world.app, T0 + timedelta(minutes=15))
    assert len(await _recorded_pages(world.app, incident_id)) == 1
    await _tick(world.app, T0 + timedelta(minutes=29) - timedelta(milliseconds=1))
    assert len(await _recorded_pages(world.app, incident_id)) == 1
    await _tick(world.app, T0 + timedelta(minutes=29))
    assert (world.level2, 1) in await _recorded_pages(world.app, incident_id)


async def test_l03_lapse_with_no_further_level_keeps_the_owner(world):
    chain_id = await _chain(world.app, [world.level1])
    incident_id = await _incident(world.app, chain_id)
    await _run(
        world.app,
        _esc.acknowledge,
        incident_id=incident_id,
        assignee_id=world.level1,
        at=T0,
    )
    await _tick(world.app, T0 + LOCK)
    state = await _state(world.app, incident_id)
    assert state.status == "exhausted"
    assert await _owner(world.app, incident_id) == world.level1


async def test_l03_start_time_cap_never_defeats_a_lock(world):
    # The unacknowledged-chain cap is 15 min from the first page (KI-010, X1b).
    incident_id = await _three_levels(world)
    await _run(
        world.app,
        _esc.acknowledge,
        incident_id=incident_id,
        assignee_id=world.level1,
        at=T0 + timedelta(minutes=14),
    )
    state = await _state(world.app, incident_id)
    assert state.hard_deadline_at is None
    await _tick(world.app, T0 + timedelta(minutes=20))
    assert (await _state(world.app, incident_id)).status == "acked"
    await _tick(world.app, T0 + timedelta(minutes=29))
    state = await _state(world.app, incident_id)
    assert state.status == "running" and state.current_step_index == 1


# ── L04: what counts as activity ────────────────────────────────────────────


async def _acked_now(world: World) -> uuid.UUID:
    incident_id = await _three_levels(world, at=datetime.now(timezone.utc))
    await _run(
        world.app, _esc.acknowledge, incident_id=incident_id, assignee_id=world.level1
    )
    return incident_id


async def _activity(app, incident_id) -> datetime:
    return (await _state(app, incident_id)).last_activity_at


async def test_l04_assignee_writes_refresh_the_lock(world):
    incident_id = await _acked_now(world)
    headers = await _headers(world.client, "lc-l1")

    before = await _activity(world.app, incident_id)
    resp = await world.client.post(
        f"/incidents/{incident_id}/comments", json={"body": "looking"}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    after_comment = await _activity(world.app, incident_id)
    assert after_comment > before

    resp = await world.client.patch(
        f"/incidents/{incident_id}", json={"severity": "high"}, headers=headers
    )
    assert resp.status_code == 200, resp.text
    assert await _activity(world.app, incident_id) > after_comment


async def test_l04_reads_and_other_peoples_writes_do_not_count(world):
    incident_id = await _acked_now(world)
    before = await _activity(world.app, incident_id)

    other = await _headers(world.client, "lc-l2")
    resp = await world.client.post(
        f"/incidents/{incident_id}/comments", json={"body": "fyi"}, headers=other
    )
    assert resp.status_code == 201
    owner = await _headers(world.client, "lc-l1")
    assert (
        await world.client.get(f"/incidents/{incident_id}", headers=owner)
    ).status_code == 200
    assert (
        await world.client.get(f"/incidents/{incident_id}/timeline", headers=owner)
    ).status_code == 200
    await _tick(world.app, datetime.now(timezone.utc))

    assert await _activity(world.app, incident_id) == before


async def test_l04_failed_writes_do_not_count(world):
    incident_id = await _acked_now(world)
    before = await _activity(world.app, incident_id)
    await _user(world.app, "lc-viewer", role="viewer")
    viewer = await _headers(world.client, "lc-viewer")
    resp = await world.client.post(
        f"/incidents/{incident_id}/comments", json={"body": "x"}, headers=viewer
    )
    assert resp.status_code == 403
    # A rolled-back transaction leaves no trace.
    async with world.app.state.session_factory() as db:
        assert await _esc.record_assignee_activity(
            db, TEST_ORG_ID, incident_id=incident_id, actor_id=world.level1
        )
        await db.rollback()
    assert await _activity(world.app, incident_id) == before


async def test_l04_a_non_acked_chain_ignores_activity(world):
    incident_id = await _three_levels(world)
    assert not await _run(
        world.app,
        _esc.record_assignee_activity,
        incident_id=incident_id,
        actor_id=world.level1,
    )
    assert (await _state(world.app, incident_id)).last_activity_at is None


# ── L05: snooze ─────────────────────────────────────────────────────────────


async def test_l05_unacked_snooze_resumes_the_next_level_once(world):
    incident_id = await _three_levels(world)
    until = T0 + timedelta(minutes=30)
    await _run(
        world.app,
        _esc.snooze,
        incident_id=incident_id,
        actor_id=world.level1,
        until=until,
        at=T0 + timedelta(seconds=10),
    )
    await _tick(world.app, T0 + timedelta(minutes=5))  # level timeout is muted
    assert len(await _recorded_pages(world.app, incident_id)) == 1
    await _tick(world.app, until - timedelta(milliseconds=1))
    assert len(await _recorded_pages(world.app, incident_id)) == 1
    await _tick(world.app, until)
    await _tick(world.app, until)
    assert await _recorded_pages(world.app, incident_id) == [
        (world.level1, 0),
        (world.level2, 1),
    ]
    state = await _state(world.app, incident_id)
    assert state.status == "running" and state.paused_until is None


async def test_l05_acked_snooze_keeps_the_owner_and_extends_the_lock(world):
    incident_id = await _three_levels(world)
    await _run(
        world.app,
        _esc.acknowledge,
        incident_id=incident_id,
        assignee_id=world.level1,
        at=T0,
    )
    until = T0 + timedelta(hours=1)
    resumes = await _run(
        world.app,
        _esc.snooze,
        incident_id=incident_id,
        actor_id=world.level1,
        until=until,
        at=T0 + timedelta(minutes=5),
    )
    assert resumes == until
    state = await _state(world.app, incident_id)
    assert state.status == "acked"
    await _tick(world.app, T0 + timedelta(minutes=59))
    assert await _owner(world.app, incident_id) == world.level1
    await _tick(world.app, until)
    assert (world.level2, 1) in await _recorded_pages(world.app, incident_id)


async def test_l05_snooze_shorter_than_the_lock_never_shortens_it(world):
    incident_id = await _three_levels(world)
    await _run(
        world.app,
        _esc.acknowledge,
        incident_id=incident_id,
        assignee_id=world.level1,
        at=T0,
    )
    resumes = await _run(
        world.app,
        _esc.snooze,
        incident_id=incident_id,
        actor_id=world.level1,
        until=T0 + timedelta(minutes=5),
        at=T0 + timedelta(minutes=2),
    )
    assert resumes == T0 + timedelta(minutes=2) + LOCK
    await _tick(world.app, T0 + timedelta(minutes=6))
    assert await _owner(world.app, incident_id) == world.level1


async def test_l05_acknowledging_again_keeps_the_owners_snooze(world):
    incident_id = await _three_levels(world)
    await _run(
        world.app,
        _esc.acknowledge,
        incident_id=incident_id,
        assignee_id=world.level1,
        at=T0,
    )
    until = T0 + timedelta(hours=1)
    await _run(
        world.app,
        _esc.snooze,
        incident_id=incident_id,
        actor_id=world.level1,
        until=until,
        at=T0 + timedelta(minutes=1),
    )
    outcome = await _run(
        world.app,
        _esc.acknowledge,
        incident_id=incident_id,
        assignee_id=world.level1,
        at=T0 + timedelta(minutes=2),
    )
    assert outcome.status == "refreshed"
    state = await _state(world.app, incident_id)
    assert state.paused_until is not None
    await _tick(world.app, T0 + timedelta(minutes=30))
    assert await _owner(world.app, incident_id) == world.level1
    await _tick(world.app, until)
    assert (world.level2, 1) in await _recorded_pages(world.app, incident_id)


async def test_l05_repeat_snooze_replaces_the_end(world):
    incident_id = await _three_levels(world)
    for minutes in (30, 10):
        await _run(
            world.app,
            _esc.snooze,
            incident_id=incident_id,
            actor_id=world.level1,
            until=T0 + timedelta(minutes=minutes),
            at=T0 + timedelta(minutes=1),
        )
    await _tick(world.app, T0 + timedelta(minutes=10))
    assert (world.level2, 1) in await _recorded_pages(world.app, incident_id)


async def test_l05_escalate_now_clears_a_snooze_and_a_lock_without_moving_ownership(
    world,
):
    incident_id = await _three_levels(world)
    await _run(
        world.app,
        _esc.snooze,
        incident_id=incident_id,
        actor_id=world.level1,
        until=T0 + timedelta(hours=2),
        at=T0 + timedelta(minutes=1),
    )
    await _run(
        world.app,
        _esc.escalate_now,
        incident_id=incident_id,
        at=T0 + timedelta(minutes=2),
    )
    state = await _state(world.app, incident_id)
    assert state.status == "running" and state.paused_until is None
    assert (world.level2, 1) in await _recorded_pages(world.app, incident_id)

    await _run(
        world.app,
        _esc.acknowledge,
        incident_id=incident_id,
        assignee_id=world.level2,
        at=T0 + timedelta(minutes=3),
    )
    await _run(
        world.app,
        _esc.escalate_now,
        incident_id=incident_id,
        at=T0 + timedelta(minutes=4),
    )
    assert (world.level3, 2) in await _recorded_pages(world.app, incident_id)
    assert await _owner(world.app, incident_id) == world.level2
    async with world.app.state.session_factory() as db:
        assert (
            await IncidentRepo.get_by_id(db, TEST_ORG_ID, incident_id)
        ).status == "open"


async def test_l05_release_while_snoozed_resumes_escalation(world):
    incident_id = await _three_levels(world)
    await _run(
        world.app,
        _esc.acknowledge,
        incident_id=incident_id,
        assignee_id=world.level1,
        at=T0,
    )
    await _run(
        world.app,
        _esc.snooze,
        incident_id=incident_id,
        actor_id=world.level1,
        until=T0 + timedelta(hours=1),
        at=T0 + timedelta(minutes=1),
    )
    await _run(
        world.app,
        _esc.release_ownership,
        incident_id=incident_id,
        actor_id=world.level1,
        at=T0 + timedelta(minutes=2),
    )
    assert await _owner(world.app, incident_id) is None
    assert (world.level2, 1) in await _recorded_pages(world.app, incident_id)
    state = await _state(world.app, incident_id)
    assert state.status == "running" and state.paused_until is None


async def test_l05_resolve_during_a_snooze_leaves_no_zombie_timer(world):
    incident_id = await _three_levels(world, external_id=f"lc-{uuid.uuid4().hex[:8]}")
    await _make_paused(world, incident_id)
    await _close_patch(world, incident_id)
    await _tick(world.app, T0 + timedelta(hours=3))
    assert len(await _recorded_pages(world.app, incident_id)) == 1


async def test_l05_slack_snooze_uses_the_shared_path(world):
    incident_id = await _three_levels(world, at=datetime.now(timezone.utc))
    text = await _slack(world, "/snooze", f"{incident_id} 30m", "U_L1", world.level1)
    assert "Escalation resumes" in text
    state = await _state(world.app, incident_id)
    assert state.status == "paused" and state.paused_until is not None


async def test_l05_slack_snooze_rejects_durations_over_a_week(world):
    incident_id = await _three_levels(world, at=datetime.now(timezone.utc))
    for text in ("8d", "99999999d"):
        reply = await _slack(
            world, "/snooze", f"{incident_id} {text}", "U_L1", world.level1
        )
        assert "Usage" in reply
    assert (await _state(world.app, incident_id)).status == "running"
    reply = await _slack(world, "/snooze", f"{incident_id} 7d", "U_L1", world.level1)
    assert "Snoozed" in reply


async def test_l05_snoozing_a_closed_incident_does_nothing(world):
    incident_id = await _three_levels(world, external_id=f"lc-{uuid.uuid4().hex[:8]}")
    await _close_patch(world, incident_id)
    assert (
        await _run(
            world.app,
            _esc.snooze,
            incident_id=incident_id,
            actor_id=world.level1,
            until=datetime.now(timezone.utc) + timedelta(minutes=30),
        )
        is None
    )


# ── Release (web route) ─────────────────────────────────────────────────────


async def test_release_resumes_at_the_next_level_never_level_zero(world):
    incident_id = await _three_levels(world, at=datetime.now(timezone.utc))
    headers = await _headers(world.client, "lc-l1")
    await _own_ack(world, incident_id, world.level1, headers)
    resp = await world.client.post(f"/incidents/{incident_id}/release", headers=headers)
    assert resp.status_code == 204
    pages = await _recorded_pages(world.app, incident_id)
    assert pages == [(world.level1, 0), (world.level2, 1)]
    state = await _state(world.app, incident_id)
    assert state.status == "running" and state.current_step_index == 1


# ── L06: takeover ───────────────────────────────────────────────────────────


async def _pending_takeover(world: World) -> uuid.UUID:
    incident_id = await _three_levels(world, at=datetime.now(timezone.utc))
    await _run(
        world.app, _esc.acknowledge, incident_id=incident_id, assignee_id=world.level1
    )
    resp = await world.client.post(
        f"/incidents/{incident_id}/take",
        json={},
        headers=await _headers(world.client, "lc-l2"),
    )
    assert resp.status_code == 200, resp.text
    assert (
        await _state(world.app, incident_id)
    ).pending_takeover_user_id == world.level2
    return incident_id


async def test_l06_only_the_current_owner_can_confirm(world):
    incident_id = await _pending_takeover(world)
    await _user(world.app, "lc-viewer2", role="viewer")
    for name, expected in (("lc-l2", 403), ("lc-l3", 403), ("lc-viewer2", 403)):
        resp = await world.client.post(
            f"/incidents/{incident_id}/take",
            json={"confirm": True},
            headers=await _headers(world.client, name),
        )
        assert resp.status_code == expected, (name, resp.text)
    assert await _owner(world.app, incident_id) == world.level1

    resp = await world.client.post(
        f"/incidents/{incident_id}/take",
        json={"confirm": True},
        headers=await _headers(world.client, "lc-l1"),
    )
    assert resp.status_code == 200, resp.text
    assert await _owner(world.app, incident_id) == world.level2
    state = await _state(world.app, incident_id)
    assert state.status == "acked" and state.pending_takeover_user_id is None
    # A duplicate confirmation is inert.
    resp = await world.client.post(
        f"/incidents/{incident_id}/take",
        json={"confirm": True},
        headers=await _headers(world.client, "lc-l2"),
    )
    assert resp.status_code == 409
    assert await _owner(world.app, incident_id) == world.level2


async def test_l06_only_an_admin_can_force(world):
    incident_id = await _pending_takeover(world)
    resp = await world.client.post(
        f"/incidents/{incident_id}/take",
        json={"force": True},
        headers=await _headers(world.client, "lc-l3"),
    )
    assert resp.status_code == 403
    resp = await world.client.post(
        f"/incidents/{incident_id}/take", json={"force": True}, headers=world.admin
    )
    assert resp.status_code == 200, resp.text
    assert await _owner(world.app, incident_id) == world.admin_id
    assert any("admin force" in c for c in await _comments(world.app, incident_id))


async def test_l06_an_unanswered_request_expires_without_a_transfer(world):
    incident_id = await _pending_takeover(world)
    expires = (
        await _state(world.app, incident_id)
    ).pending_takeover_expires_at.replace(tzinfo=timezone.utc)
    await _tick(world.app, expires - timedelta(seconds=1))
    assert (
        await _state(world.app, incident_id)
    ).pending_takeover_user_id == world.level2
    await _tick(world.app, expires)
    state = await _state(world.app, incident_id)
    assert state.pending_takeover_user_id is None
    assert await _owner(world.app, incident_id) == world.level1


async def test_l06_a_stale_confirmation_is_rejected(world):
    incident_id = await _pending_takeover(world)
    later = datetime.now(timezone.utc) + timedelta(minutes=6)
    result = await _run(
        world.app,
        _esc.handle_takeover_confirm,
        incident_id=incident_id,
        actor_id=world.level1,
        at=later,
    )
    assert result == "expired"
    assert await _owner(world.app, incident_id) == world.level1


async def test_l06_cross_workspace_incidents_are_invisible(world):
    incident_id = await _pending_takeover(world)
    await _user(world.app, "lc-outsider", role="admin", org_id=OTHER_ORG_ID)
    resp = await world.client.post(
        f"/incidents/{incident_id}/take",
        json={"force": True},
        headers=await _headers(world.client, "lc-outsider"),
    )
    assert resp.status_code == 404
    assert await _owner(world.app, incident_id) == world.level1


async def test_l06_an_ineligible_requester_cannot_receive_the_incident(world):
    incident_id = await _pending_takeover(world)
    async with world.app.state.session_factory() as db:
        user = await UserRepo.get_by_id(db, world.level2)
        user.is_active = False
        await db.commit()
    result = await _run(
        world.app,
        _esc.handle_takeover_confirm,
        incident_id=incident_id,
        actor_id=world.level1,
    )
    assert result == "ineligible"
    assert await _owner(world.app, incident_id) == world.level1


# ── L07: handoff and eligibility ────────────────────────────────────────────


async def _service_with_chain(world: World, user_id: uuid.UUID):
    chain_id = await _chain(world.app, [user_id])
    async with world.app.state.session_factory() as db:
        chain = await EscalationChainRepo.get_by_id(db, TEST_ORG_ID, chain_id)
        service = await ServiceRepo.create(
            db,
            TEST_ORG_ID,
            team_id=chain.team_id,
            name=f"svc-{uuid.uuid4().hex[:6]}",
            slug=f"svc-{uuid.uuid4().hex[:6]}",
            priority="P1",
        )
        await ServiceEscalationChainRepo.link(
            db, TEST_ORG_ID, service_id=service.id, chain_id=chain_id
        )
        await db.commit()
        return service.id


async def _paged_incident(world: World) -> uuid.UUID:
    incident_id = await _three_levels(world, at=datetime.now(timezone.utc))
    async with world.app.state.session_factory() as db:
        incident = await IncidentRepo.get_by_id(db, TEST_ORG_ID, incident_id)
        incident.response_mode = "page"
        incident.priority = "P1"
        await db.commit()
    return incident_id


async def test_l07_handoff_restarts_only_a_live_incident(world):
    new_service = await _service_with_chain(world, world.level3)
    live = await _paged_incident(world)
    resp = await world.client.patch(
        f"/incidents/{live}",
        json={"service_id": str(new_service), "service_id_set": True},
        headers=world.admin,
    )
    assert resp.status_code == 200, resp.text
    assert (world.level3, 0) in await _recorded_pages(world.app, live)

    closed = await _paged_incident(world)
    await _run(
        world.app, _esc.acknowledge, incident_id=closed, assignee_id=world.level1
    )
    await _close_patch(world, closed)
    pages_before = await _recorded_pages(world.app, closed)
    resp = await world.client.patch(
        f"/incidents/{closed}",
        json={"service_id": str(new_service), "service_id_set": True},
        headers=world.admin,
    )
    assert resp.status_code == 200, resp.text
    assert await _recorded_pages(world.app, closed) == pages_before
    assert (await _state(world.app, closed)).status == "cancelled"
    assert await _owner(world.app, closed) == world.level1


async def test_l07_ownership_without_a_chain(world):
    async with world.app.state.session_factory() as db:
        incident = await IncidentRepo.create(
            db, TEST_ORG_ID, title="no chain", description="d"
        )
        await db.commit()
    headers = await _headers(world.client, "lc-l1")
    await _own_ack(world, incident.id, world.level1, headers)
    assert await _owner(world.app, incident.id) == world.level1
    resp = await world.client.post(f"/incidents/{incident.id}/release", headers=headers)
    assert resp.status_code == 204
    assert await _owner(world.app, incident.id) is None


async def test_l07_inactive_or_foreign_assignees_are_rejected(world):
    incident_id = await _three_levels(world, at=datetime.now(timezone.utc))
    inactive = await _user(world.app, "lc-gone")
    async with world.app.state.session_factory() as db:
        (await UserRepo.get_by_id(db, inactive)).is_active = False
        await db.commit()
    outsider = await _user(world.app, "lc-foreign", org_id=OTHER_ORG_ID)
    for target in (inactive, outsider):
        resp = await world.client.post(
            f"/incidents/{incident_id}/assign",
            json={"user_id": str(target)},
            headers=world.admin,
        )
        assert resp.status_code == 422, resp.text
        resp = await world.client.post(
            "/incidents/bulk",
            json={
                "action": "reassign",
                "incident_ids": [str(incident_id)],
                "user_id": str(target),
            },
            headers=world.admin,
        )
        assert resp.status_code == 422, resp.text
    assert await _owner(world.app, incident_id) is None


async def test_keypad_1_after_a_release_lets_the_next_responder_own_it(world):
    incident_id = await _three_levels(world, at=datetime.now(timezone.utc))
    await _run(
        world.app, _esc.acknowledge, incident_id=incident_id, assignee_id=world.level1
    )
    await _run(
        world.app,
        _esc.release_ownership,
        incident_id=incident_id,
        actor_id=world.level1,
    )
    await _own_keypad1(world, incident_id, world.level2, None)
    assert await _owner(world.app, incident_id) == world.level2


# ── L09: rows from before the upgrade ───────────────────────────────────────


async def test_l09_pre_upgrade_acked_and_paused_rows_stay_inert(world):
    acked = await _three_levels(world)
    paused = await _three_levels(world)
    async with world.app.state.session_factory() as db:
        for incident_id, status in ((acked, "acked"), (paused, "paused")):
            state = await IncidentChainStateRepo.get_for_incident(
                db, TEST_ORG_ID, incident_id
            )
            state.status = status
            # How the old code left them: an ack finished the chain; a Slack
            # snooze set a due time but no end.
            state.finished_at = T0 + timedelta(minutes=1) if status == "acked" else None
            state.next_step_due_at = (
                None if status == "acked" else T0 + timedelta(minutes=30)
            )
        await IncidentAssignmentRepo.assign(
            db,
            TEST_ORG_ID,
            incident_id=acked,
            user_id=world.level1,
            assigned_by="self_ack",
        )
        await db.commit()

    for hours in (1, 24, 24 * 30):
        await _tick(world.app, T0 + timedelta(hours=hours))
    assert len(await _recorded_pages(world.app, acked)) == 1
    assert len(await _recorded_pages(world.app, paused)) == 1
    assert not await _run(
        world.app,
        _esc.record_assignee_activity,
        incident_id=acked,
        actor_id=world.level1,
    )
    assert await _run(
        world.app, _esc.release_ownership, incident_id=acked, actor_id=world.level1
    )
    assert len(await _recorded_pages(world.app, acked)) == 1
    assert (await _state(world.app, acked)).status == "acked"


async def test_l09_a_chain_left_running_on_a_resolved_incident_is_cancelled(world):
    # Before this change a resolve never cancelled the chain (KI-011).
    incident_id = await _three_levels(world)
    async with world.app.state.session_factory() as db:
        incident = await IncidentRepo.get_by_id(db, TEST_ORG_ID, incident_id)
        incident.status = "resolved"
        await db.commit()
    await _tick(world.app, T0 + timedelta(minutes=2))
    assert (await _state(world.app, incident_id)).status == "cancelled"
    assert len(await _recorded_pages(world.app, incident_id)) == 1


async def test_chain_state_api_exposes_the_lock_fields(world):
    incident_id = await _acked_now(world)
    resp = await world.client.get(
        f"/incidents/{incident_id}/chain", headers=world.admin
    )
    assert resp.status_code == 200, resp.text
    state = resp.json()["state"]
    assert state["status"] == "acked"
    assert state["last_activity_at"] is not None
    assert "paused_until" in state


async def test_inbox_links_open_the_incident_page(world):
    incident_id = await _three_levels(world, at=datetime.now(timezone.utc))
    resp = await world.client.post(
        f"/incidents/{incident_id}/assign",
        json={"user_id": str(world.level2)},
        headers=world.admin,
    )
    assert resp.status_code == 200
    inbox = await world.client.get(
        "/notifications", headers=await _headers(world.client, "lc-l2")
    )
    links = [item["link"] for item in inbox.json()["items"]]
    assert f"/dashboard/incidents/detail?id={incident_id}" in links
