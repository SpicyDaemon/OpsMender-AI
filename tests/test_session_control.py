"""M1-13: the incident's owner and admins control its AI session; others watch.

A manual start needs ownership first (admins excepted), and starting, taking
over or overriding a session never changes who owns the incident. Stop, model
switch, override and messages follow the same rule; sessions without an
incident keep the admin/operator rule. Approvals on an incident's session are
answered by its owner, operators of the team handling it and admins, on the
web and in chat. The paging panel reports both flags for the UI.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from backend.bots.actions import (
    IncidentActionError,
    IncidentActionTokenClaims,
    execute_incident_action,
)
from backend.db.models import ApprovalRequest, ModelConfig, Session, SessionMessage
from backend.db.repos import (
    ApprovalRequestRepo,
    EscalationChainRepo,
    IncidentRepo,
    ServiceEscalationChainRepo,
    ServiceRepo,
    SessionRepo,
    TeamRepo,
    UserRepo,
)
from backend.paging import escalation as _esc
from backend.paging.reassign import APPROVAL_FORBIDDEN, SESSION_CONTROL_FORBIDDEN
from tests.test_ownership_lifecycle import (
    TEST_ORG_ID,
    World,
    _chain,
    _headers,
    _owner,
    _user,
    app as _app_fixture,
    client as _client_fixture,
    world as _world_fixture,
)

app = pytest.fixture(_app_fixture.__wrapped__)
client = pytest.fixture(_client_fixture.__wrapped__)
world = pytest.fixture(_world_fixture.__wrapped__)

# In every test: level3 owns the incident and is on its team with "sc-mate";
# level1 is off the team but paged by the chain; level2 is off the team.


@pytest.fixture(autouse=True)
def _no_ai_run(monkeypatch):
    """Authorization only: never run a model workflow in the background."""

    async def _ready(*_args, **_kwargs):
        return None

    monkeypatch.setattr("backend.api.routes.sessions.dispatch_session_ready", _ready)
    monkeypatch.setattr(
        "backend.api.routes.sessions.schedule_session_workflow",
        lambda *_args, **_kwargs: None,
    )


async def _owned_incident(w: World) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Returns the incident, its model and the teammate who doesn't own it."""
    chain_id = await _chain(w.app, [w.level1])
    mate = await _user(w.app, f"sc-mate-{uuid.uuid4().hex[:6]}")
    async with w.app.state.session_factory() as db:
        chain = await EscalationChainRepo.get_by_id(db, TEST_ORG_ID, chain_id)
        for member in (w.level3, mate):
            await TeamRepo.add_member(db, TEST_ORG_ID, chain.team_id, user_id=member)
        model = ModelConfig(
            org_id=TEST_ORG_ID,
            name=f"control-model-{uuid.uuid4().hex[:6]}",
            provider="ollama",
            model_id="control-model",
            max_concurrent_sessions=5,
            is_default=True,
        )
        db.add(model)
        await db.flush()
        service = await ServiceRepo.create(
            db,
            TEST_ORG_ID,
            team_id=chain.team_id,
            name=f"control-{uuid.uuid4().hex[:6]}",
            slug=f"control-{uuid.uuid4().hex[:6]}",
            priority="P1",
        )
        service.model_config_ids = [str(model.id)]
        await ServiceEscalationChainRepo.link(
            db, TEST_ORG_ID, service_id=service.id, chain_id=chain_id
        )
        incident = await IncidentRepo.create(
            db,
            TEST_ORG_ID,
            title="session control",
            description="d",
            priority="P1",
            response_mode="page",
            service_id=service.id,
        )
        await _esc.start_chain(
            db,
            TEST_ORG_ID,
            incident_id=incident.id,
            chain_id=chain_id,
            at=datetime.now(timezone.utc),
        )
        await db.commit()
    owner = await _headers(w.client, "lc-l3")
    acked = await w.client.post(f"/incidents/{incident.id}/ack", json={}, headers=owner)
    assert acked.status_code == 200, acked.text
    return incident.id, model.id, mate


async def _running_session(
    w: World, incident_id: uuid.UUID, model_id: uuid.UUID, *, tier: int = 1
) -> uuid.UUID:
    async with w.app.state.session_factory() as db:
        session = await SessionRepo.create(
            db,
            TEST_ORG_ID,
            tier=tier,
            incident_id=incident_id,
            model_config_id=model_id,
            status="active",
        )
        await db.commit()
        return session.id


async def _sessions(w: World, incident_id: uuid.UUID) -> list[tuple]:
    async with w.app.state.session_factory() as db:
        rows = (
            await db.execute(
                select(
                    Session.id, Session.status, Session.tier, Session.model_config_id
                )
                .where(Session.incident_id == incident_id)
                .order_by(Session.id)
            )
        ).all()
        return [tuple(row) for row in rows]


async def _message_count(w: World, session_id: uuid.UUID) -> int:
    async with w.app.state.session_factory() as db:
        return await db.scalar(
            select(func.count())
            .select_from(SessionMessage)
            .where(SessionMessage.session_id == session_id)
        )


async def _mate_headers(w: World, mate: uuid.UUID) -> dict[str, str]:
    async with w.app.state.session_factory() as db:
        user = await UserRepo.get_by_id(db, mate)
        name = user.username
    return await _headers(w.client, name)


async def test_a_teammate_who_does_not_own_it_cannot_start_a_session(world):
    incident_id, _, mate = await _owned_incident(world)
    for headers in (
        await _mate_headers(world, mate),
        await _headers(world.client, "lc-l2"),
    ):
        resp = await world.client.post(
            "/sessions",
            json={"incident_id": str(incident_id), "tier": 1},
            headers=headers,
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["detail"] == SESSION_CONTROL_FORBIDDEN
    assert await _sessions(world, incident_id) == []
    assert await _owner(world.app, incident_id) == world.level3


async def test_the_owner_and_an_admin_start_without_changing_the_owner(world):
    incident_id, _, _ = await _owned_incident(world)
    owner = await _headers(world.client, "lc-l3")
    started = await world.client.post(
        "/sessions", json={"incident_id": str(incident_id), "tier": 1}, headers=owner
    )
    assert started.status_code == 201, started.text
    assert await _owner(world.app, incident_id) == world.level3

    # An admin taking over the running session reuses it and keeps the owner.
    taken = await world.client.post(
        "/sessions",
        json={"incident_id": str(incident_id), "tier": 1},
        headers=world.admin,
    )
    assert taken.status_code == 201, taken.text
    assert taken.json()["id"] == started.json()["id"]
    assert await _owner(world.app, incident_id) == world.level3


async def test_others_cannot_stop_switch_override_or_message_the_session(world):
    incident_id, model_id, mate = await _owned_incident(world)
    session_id = await _running_session(world, incident_id, model_id, tier=0)
    before = (
        await _sessions(world, incident_id),
        await _message_count(world, session_id),
    )
    for headers in (
        await _mate_headers(world, mate),
        await _headers(world.client, "lc-l1"),
    ):
        for path, body in (
            ("stop", None),
            ("model", {"model_config_id": str(model_id)}),
            ("override", {"tier": 1}),
            ("messages", {"content": "restart the pods"}),
            ("queue/cancel", None),
        ):
            resp = await world.client.post(
                f"/sessions/{session_id}/{path}", json=body, headers=headers
            )
            assert resp.status_code == 403, (path, resp.text)
            assert resp.json()["detail"] == SESSION_CONTROL_FORBIDDEN
    assert (
        await _sessions(world, incident_id),
        await _message_count(world, session_id),
    ) == before
    assert await _owner(world.app, incident_id) == world.level3


async def test_an_admin_override_keeps_the_owner(world):
    incident_id, model_id, _ = await _owned_incident(world)
    session_id = await _running_session(world, incident_id, model_id, tier=0)
    overridden = await world.client.post(
        f"/sessions/{session_id}/override", json={"tier": 1}, headers=world.admin
    )
    assert overridden.status_code == 200, overridden.text
    assert overridden.json()["tier"] == 1
    assert await _owner(world.app, incident_id) == world.level3


async def test_the_owner_stops_the_session(world):
    incident_id, model_id, _ = await _owned_incident(world)
    session_id = await _running_session(world, incident_id, model_id)
    owner = await _headers(world.client, "lc-l3")
    stopped = await world.client.post(f"/sessions/{session_id}/stop", headers=owner)
    assert stopped.status_code == 200, stopped.text
    assert stopped.json()["status"] == "stopped"


async def _pending_approval(w: World, session_id: uuid.UUID) -> uuid.UUID:
    async with w.app.state.session_factory() as db:
        request = await ApprovalRequestRepo.create(
            db,
            TEST_ORG_ID,
            session_id=session_id,
            action={"tool": "restart_service", "parameters": {"name": "orders"}},
            justification="Orders is down",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
        )
        await db.commit()
        return request.id


async def _approval_status(w: World, request_id: uuid.UUID) -> str:
    async with w.app.state.session_factory() as db:
        return (await db.get(ApprovalRequest, request_id)).status


async def test_approvals_follow_the_owner_and_team_rule(world):
    from backend.bots.dispatcher import _resolve_approval_from_bot

    incident_id, model_id, mate = await _owned_incident(world)
    session_id = await _running_session(world, incident_id, model_id)
    first = await _pending_approval(world, session_id)
    for name in ("lc-l2", "lc-l1"):
        headers = await _headers(world.client, name)
        for path, body in (
            ("approve", None),
            ("reject", None),
            ("extend", None),
            ("redirect", {"guidance": "drain the node first"}),
        ):
            resp = await world.client.post(
                f"/approvals/{first}/{path}", json=body, headers=headers
            )
            assert resp.status_code == 403, (name, path, resp.text)
            assert resp.json()["detail"] == APPROVAL_FORBIDDEN
    async with world.app.state.session_factory() as db:
        outsider = await UserRepo.get_by_id(db, world.level2)
        text = await _resolve_approval_from_bot(
            db, TEST_ORG_ID, first, decision="approved", resolver=outsider
        )
    assert text == APPROVAL_FORBIDDEN
    assert await _approval_status(world, first) == "pending"

    approved = await world.client.post(
        f"/approvals/{first}/approve", headers=await _mate_headers(world, mate)
    )
    assert approved.status_code == 200, approved.text
    assert await _approval_status(world, first) == "approved"

    second = await _pending_approval(world, session_id)
    rejected = await world.client.post(
        f"/approvals/{second}/reject", headers=world.admin
    )
    assert rejected.status_code == 200, rejected.text


async def test_chat_start_from_someone_who_does_not_own_it_is_refused(world):
    incident_id, _, mate = await _owned_incident(world)

    def claims() -> IncidentActionTokenClaims:
        return IncidentActionTokenClaims(
            org_id=TEST_ORG_ID,
            incident_id=incident_id,
            action="start_ai_session",
            channel_id=None,
            message_id=None,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            nonce=uuid.uuid4().hex,
        )

    async with world.app.state.session_factory() as db:
        with pytest.raises(IncidentActionError) as refused:
            await execute_incident_action(db, claims=claims(), actor_user_id=mate)
        await db.rollback()
    assert str(refused.value) == "actor_cannot_control_session"
    assert await _sessions(world, incident_id) == []


async def test_the_paging_panel_reports_session_control_and_approvals(world):
    incident_id, _, mate = await _owned_incident(world)
    expected = {
        "lc-l3": (True, True),
        "lc-admin": (True, True),
        "lc-l1": (False, False),
        "lc-l2": (False, False),
    }
    for name, flags in expected.items():
        panel = await world.client.get(
            f"/incidents/{incident_id}/paging",
            headers=await _headers(world.client, name),
        )
        assert panel.status_code == 200, panel.text
        body = panel.json()
        assert (body["can_control_session"], body["can_approve"]) == flags, name
    panel = await world.client.get(
        f"/incidents/{incident_id}/paging", headers=await _mate_headers(world, mate)
    )
    assert (panel.json()["can_control_session"], panel.json()["can_approve"]) == (
        False,
        True,
    )


async def test_chat_messages_to_the_session_follow_the_same_rule(world):
    from backend.bots.connectors.base import InboundMessage
    from backend.bots.dispatcher import dispatch_inbound
    from backend.db.repos import BotConnectorRepo, BotUserLinkRepo

    incident_id, model_id, mate = await _owned_incident(world)
    session_id = await _running_session(world, incident_id, model_id)
    async with world.app.state.session_factory() as db:
        connector = await BotConnectorRepo.create(
            db,
            TEST_ORG_ID,
            name="telegram",
            platform="telegram",
            credentials={"bot_token": "123:abc"},
            allowed_capabilities=["copilot_chat"],
            status="configured",
            is_enabled=True,
        )
        await BotUserLinkRepo.create(
            db,
            TEST_ORG_ID,
            connector_id=connector.id,
            platform_user_id="tg-mate",
            opsmender_user_id=mate,
        )
        await db.commit()
        result = await dispatch_inbound(
            db,
            connector=connector,
            message=InboundMessage(
                chat_id="chat-1",
                platform_user_id="tg-mate",
                text=f"/chat {session_id} restart the pods",
            ),
        )
    assert result.reply_text == SESSION_CONTROL_FORBIDDEN
    assert await _message_count(world, session_id) == 0
