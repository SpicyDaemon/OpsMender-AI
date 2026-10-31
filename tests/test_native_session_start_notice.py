"""M1-122 (L-02): a session started from a chat card or a signed link tells
the Notification Channels it started, as a start from the dashboard does.

The live round saw two native Slack starts with real sessions and no
``notify:session.created`` delivery on either connector, while a dashboard
start delivered it: the native dispatcher published a chat event only for a
queued start and went straight to running an immediate one.
"""

# The app fixture comes from the Slack paging tests.
# ruff: noqa: F811

from __future__ import annotations

import asyncio
import uuid

import pytest

from backend.bots import native_callbacks
from backend.bots.actions import IncidentActionResult
from backend.db.repos import (
    BotActionAuditRepo,
    BotConnectorRepo,
    IncidentRepo,
    SessionRepo,
    UserRepo,
)
from tests.test_slack_paging import TEST_ORG_ID, app  # noqa: F401


@pytest.fixture
async def telegram(app):
    async with app.state.session_factory() as db:
        connector = await BotConnectorRepo.create(
            db,
            TEST_ORG_ID,
            name="telegram-notices",
            platform="telegram",
            config={"allowed_chat_ids": ["-100A"]},
            credentials={"bot_token": "BOT"},
            allowed_capabilities=["notifications"],
            status="configured",
            is_enabled=True,
        )
        await db.commit()
        return connector.id


@pytest.fixture
def sent(monkeypatch):
    rows: list[dict] = []

    async def fake_send(*, bot_token, chat_id, text, **kwargs):
        rows.append({"chat_id": chat_id, "text": text})
        return True, None

    monkeypatch.setattr("backend.bots.telegram.send_message", fake_send)
    return rows


@pytest.fixture
def ready(monkeypatch):
    dispatched: list[uuid.UUID] = []

    async def dispatch_session_ready(app_, session_id):
        dispatched.append(session_id)

    monkeypatch.setattr(
        "backend.services.session_orchestration.dispatch_session_ready",
        dispatch_session_ready,
    )
    return dispatched


async def _seed(app):
    async with app.state.session_factory() as db:
        user = await UserRepo.create(
            db,
            username="clicker",
            email="clicker@test.com",
            password_hash="x",
            role="operator",
            primary_org_id=TEST_ORG_ID,
            first_name="Casey",
            last_name="Clicker",
        )
        incident = await IncidentRepo.create(
            db, TEST_ORG_ID, title="Ratings down", description="x", severity="high"
        )
        session = await SessionRepo.create(
            db, TEST_ORG_ID, tier=1, incident_id=incident.id
        )
        await db.commit()
        return user.id, incident.id, session.id


async def _drain(app):
    tasks = list(app.state.background_tasks)
    if tasks:
        await asyncio.gather(*tasks)


async def _audits(app, connector_id):
    async with app.state.session_factory() as db:
        rows = await BotActionAuditRepo.list_by_connector(db, TEST_ORG_ID, connector_id)
    return [(row.command, row.status) for row in rows]


async def test_an_immediate_native_start_publishes_session_created_once(
    app, telegram, sent, ready
):
    user_id, incident_id, session_id = await _seed(app)
    result = IncidentActionResult(
        action="start_ai_session",
        status="session_started",
        incident_id=incident_id,
        actor_user_id=user_id,
        session_id=session_id,
    )
    async with app.state.session_factory() as db:
        await native_callbacks.dispatch_native_session_result(
            app, db, org_id=TEST_ORG_ID, result=result
        )
    await _drain(app)

    assert ready == [session_id]
    [message] = sent
    assert message["chat_id"] == "-100A"
    assert "AI session started by Casey Clicker" in message["text"]
    assert str(session_id) in message["text"]
    assert "Ratings down" in message["text"]
    assert await _audits(app, telegram) == [("notify:session.created", "ok")]


async def test_a_queued_native_start_still_publishes_only_queued(
    app, telegram, sent, ready
):
    user_id, incident_id, session_id = await _seed(app)
    result = IncidentActionResult(
        action="start_ai_session",
        status="session_queued",
        incident_id=incident_id,
        actor_user_id=user_id,
        session_id=session_id,
    )
    async with app.state.session_factory() as db:
        await native_callbacks.dispatch_native_session_result(
            app, db, org_id=TEST_ORG_ID, result=result
        )
    await _drain(app)

    assert ready == []
    [message] = sent
    assert "queued" in message["text"]
    assert await _audits(app, telegram) == [("notify:session.queued", "ok")]


async def test_a_start_that_did_not_start_publishes_nothing(app, telegram, sent, ready):
    user_id, incident_id, session_id = await _seed(app)
    result = IncidentActionResult(
        action="start_ai_session",
        status="already_active",
        incident_id=incident_id,
        actor_user_id=user_id,
        session_id=session_id,
    )
    async with app.state.session_factory() as db:
        await native_callbacks.dispatch_native_session_result(
            app, db, org_id=TEST_ORG_ID, result=result
        )
    await _drain(app)

    assert ready == [] and sent == []
    assert await _audits(app, telegram) == []
