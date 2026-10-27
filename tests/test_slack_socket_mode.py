"""M1-97: Slack Socket Mode.

Slack buttons and slash commands used to need a public Request URL; the
2026-10-24 live round ran a tunnel for it, which many companies cannot open.
A Slack connector in Socket Mode instead opens a socket to Slack with an
app-level token named by an environment variable. These tests run the client
against an in-process Socket Mode server, the envelopes through the same
handlers as the HTTP endpoints, and two holders against one database.
"""

# The app and client fixtures come from the Slack paging tests.
# ruff: noqa: F811

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import websockets

from backend.api.routes.bot_connectors import _connector_config_checks
from backend.bots import slack_socket
from backend.bots.slack_socket import (
    SlackSocketManager,
    SocketModeClient,
    SocketModeError,
    handle_envelope,
    lease_key,
    open_socket_url,
)
from backend.db.models import BotConnector
from backend.db.repos import (
    BotConnectorRepo,
    IncidentAssignmentRepo,
    IncidentPageRepo,
    RuntimeConfigRepo,
)
from backend.paging.slack_cards import ACTION_ACK
from tests.test_slack_paging import (  # noqa: F401
    TEST_ORG_ID,
    _block_actions_payload,
    _seed_incident,
    _seed_user_and_link,
    app,
)

TOKEN = "xapp-1-A1-test"
TOKEN_ENV = "OPSMENDER_SLACK_APP_TOKEN"
RESPONSE_URL = "https://hooks.slack.com/actions/T1/1/abc"


async def _socket_connector(app, *, mode="socket", enabled=True):
    async with app.state.session_factory() as db:
        connector = await BotConnectorRepo.create(
            db,
            TEST_ORG_ID,
            name=f"slack-{mode}-{'on' if enabled else 'off'}",
            platform="slack",
            config={"inbound_mode": mode, "app_token_env": TOKEN_ENV},
            credentials={"signing_secret": "unused", "bot_token": "xoxb-t"},
            allowed_capabilities=["paging"],
            status="configured",
            is_enabled=enabled,
            native_actions_enabled=True,
        )
        await db.commit()
        return connector


class _FakeSlack:
    """An in-process Socket Mode server. It greets each connection, sends what
    the test queues ("close" drops the socket without a word) and records
    what the client sends back."""

    def __init__(self):
        self.received: list[dict] = []
        self.connections = 0
        self.queue: asyncio.Queue = asyncio.Queue()
        self.url = ""
        self._server = None

    async def __aenter__(self):
        self._server = await websockets.serve(self._serve, "127.0.0.1", 0)
        port = next(iter(self._server.sockets)).getsockname()[1]
        self.url = f"ws://127.0.0.1:{port}/link"
        return self

    async def __aexit__(self, *exc):
        for _ in range(self.connections + 1):
            self.queue.put_nowait("close")  # free any handler still waiting
        self._server.close()
        await self._server.wait_closed()

    async def _serve(self, ws):
        self.connections += 1
        await ws.send(json.dumps({"type": "hello"}))

        async def read():
            async for raw in ws:
                self.received.append(json.loads(raw))

        reader = asyncio.create_task(read())
        try:
            while True:
                message = await self.queue.get()
                if message == "close":
                    break
                await ws.send(json.dumps(message))
                if message.get("type") == "disconnect":
                    await asyncio.sleep(0.2)
                    break
        finally:
            reader.cancel()


async def _until(condition, seconds=5.0):
    deadline = asyncio.get_running_loop().time() + seconds
    while not condition():
        assert asyncio.get_running_loop().time() < deadline, "condition never held"
        await asyncio.sleep(0.01)


async def _stop(task):
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_each_envelope_is_acknowledged_before_it_is_handled():
    """Slack retries an envelope it hears nothing about within three seconds,
    so the acknowledgement goes out before the (possibly slow) handling."""
    release = asyncio.Event()
    handled: list[tuple[str, dict]] = []

    async def handle(kind, payload):
        await release.wait()
        handled.append((kind, payload))

    async with _FakeSlack() as slack:

        async def open_url(token):
            assert token == TOKEN
            return slack.url

        task = asyncio.create_task(
            SocketModeClient(TOKEN, handle, open_url=open_url).run()
        )
        for envelope in (
            {
                "envelope_id": "e1",
                "type": "interactive",
                "payload": {"type": "block_actions"},
            },
            {
                "envelope_id": "e2",
                "type": "slash_commands",
                "payload": {"command": "/ack"},
            },
            {"envelope_id": "e3", "type": "events_api", "payload": {"event": {}}},
        ):
            await slack.queue.put(envelope)
        await _until(lambda: len(slack.received) == 3, seconds=2)
        assert handled == []  # acknowledged while the handling still waits
        release.set()
        await _until(lambda: len(handled) == 2)
        await _stop(task)

    assert slack.received == [
        {"envelope_id": "e1"},
        {"envelope_id": "e2"},
        {"envelope_id": "e3"},
    ]
    # Events API envelopes are acknowledged and not handled.
    assert [kind for kind, _ in handled] == ["interactive", "slash_commands"]


async def test_it_reconnects_when_slack_asks_and_when_the_socket_drops():
    opened = []

    async def handle(kind, payload):
        pass

    async with _FakeSlack() as slack:

        async def open_url(token):
            opened.append(token)
            return slack.url

        client = SocketModeClient(
            TOKEN, handle, open_url=open_url, backoff_first=0.01, backoff_max=0.05
        )
        task = asyncio.create_task(client.run())
        await _until(lambda: slack.connections == 1)
        await slack.queue.put({"type": "disconnect", "reason": "refresh_requested"})
        await _until(lambda: slack.connections == 2)
        await slack.queue.put("close")
        await _until(lambda: slack.connections == 3)
        await _stop(task)

    assert opened == [TOKEN, TOKEN, TOKEN]


async def test_a_refused_socket_is_retried_with_a_growing_wait(monkeypatch):
    waits: list[float] = []
    real_sleep = asyncio.sleep

    async def sleep(seconds):
        waits.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(slack_socket.asyncio, "sleep", sleep)
    monkeypatch.setattr(slack_socket.random, "random", lambda: 1.0)
    calls = 0

    async def open_url(token):
        nonlocal calls
        calls += 1
        if calls == 5:
            raise asyncio.CancelledError
        raise SocketModeError("invalid_auth")

    with pytest.raises(asyncio.CancelledError):
        await SocketModeClient(
            TOKEN,
            lambda kind, payload: None,
            open_url=open_url,
            backoff_first=1.0,
            backoff_max=4.0,
        ).run()
    # The first wait, then doubling, capped at the maximum.
    assert waits == [1.0, 2.0, 4.0, 4.0]


async def test_open_socket_url_asks_slack_with_the_app_token(monkeypatch):
    real_client = httpx.AsyncClient
    answers = [
        httpx.Response(
            200, json={"ok": True, "url": "wss://wss.slack.test/link?ticket=t"}
        ),
        httpx.Response(200, json={"ok": False, "error": "invalid_auth"}),
    ]
    seen = []

    def handler(request):
        seen.append((request.url, request.headers.get("Authorization")))
        return answers.pop(0)

    monkeypatch.setattr(
        slack_socket.httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )
    assert await open_socket_url(TOKEN) == "wss://wss.slack.test/link?ticket=t"
    with pytest.raises(SocketModeError, match="invalid_auth"):
        await open_socket_url(TOKEN)
    assert [str(url) for url, _ in seen] == [slack_socket.OPEN_URL] * 2
    assert {auth for _, auth in seen} == {f"Bearer {TOKEN}"}


async def test_one_holder_at_a_time_and_a_takeover_once_renewals_stop(app):
    now = datetime.now(timezone.utc)
    key = "slack.socket_lease.test"
    async with app.state.session_factory() as db:

        async def take(holder, seconds_later):
            return await RuntimeConfigRepo.take_lease(
                db,
                TEST_ORG_ID,
                key,
                holder,
                now=now + timedelta(seconds=seconds_later),
                seconds=60,
            )

        assert await take("a", 0)
        assert not await take("b", 30)
        assert await take("a", 40)  # a renews
        assert not await take("b", 90)  # 50 s after the renewal
        assert await take("b", 101)  # 61 s after it: b takes over
        assert not await take("a", 102)
        await RuntimeConfigRepo.release_lease(db, key, "a")  # not a's to give
        assert (await RuntimeConfigRepo.lease_holder(db, key))[0] == "b"
        await RuntimeConfigRepo.release_lease(db, key, "b")
        assert await RuntimeConfigRepo.lease_holder(db, key) is None
        await db.commit()


class _Recorder:
    """Stands in for a socket client; records which tokens got a socket."""

    made: list["_Recorder"] = []

    def __init__(self, token, handle):
        self.token = token
        _Recorder.made.append(self)

    async def run(self):
        await asyncio.Event().wait()


async def test_two_managers_on_one_database_run_one_socket(app):
    _Recorder.made = []
    connector = await _socket_connector(app)
    await _socket_connector(app, mode="http")  # never gets a socket
    env = {TOKEN_ENV: TOKEN}
    factory = app.state.session_factory
    a = SlackSocketManager(app, factory, holder="a", env=env, client_factory=_Recorder)
    b = SlackSocketManager(app, factory, holder="b", env=env, client_factory=_Recorder)
    now = datetime.now(timezone.utc)

    assert await a.tick(now) == {connector.id}
    assert await b.tick(now + timedelta(seconds=5)) == set()
    assert [r.token for r in _Recorder.made] == [TOKEN]

    # a stops renewing: once the lease runs out b takes over, and a closes
    # its socket on its next tick.
    assert await b.tick(now + timedelta(seconds=61)) == {connector.id}
    assert await a.tick(now + timedelta(seconds=62)) == set()
    assert len(_Recorder.made) == 2
    assert not a._clients and set(b._clients) == {connector.id}

    # Stopping gives the lease up at once.
    await b.stop()
    assert await a.tick(now + timedelta(seconds=63)) == {connector.id}
    await a.stop()


async def test_no_socket_without_an_app_token_or_for_a_disabled_connector(app):
    _Recorder.made = []
    await _socket_connector(app, enabled=False)
    connector = await _socket_connector(app, mode="socket")
    factory = app.state.session_factory
    for env in ({}, {TOKEN_ENV: "xoxb-not-an-app-token"}):
        manager = SlackSocketManager(
            app, factory, holder="x", env=env, client_factory=_Recorder
        )
        assert await manager.tick() == set()
    manager = SlackSocketManager(
        app, factory, holder="x", env={TOKEN_ENV: TOKEN}, client_factory=_Recorder
    )
    assert await manager.tick() == {connector.id}
    await manager.stop()
    async with factory() as db:
        assert await RuntimeConfigRepo.lease_holder(db, lease_key(connector.id)) is None


@pytest.fixture
def answers(monkeypatch):
    posted: list[tuple[str, str]] = []

    async def post(url, text):
        posted.append((url, text))

    monkeypatch.setattr("backend.api.routes.slack_paging._post_answer", post)
    return posted


async def test_a_card_click_over_the_socket_acts_and_answers_the_person(app, answers):
    connector = await _socket_connector(app)
    user, _ = await _seed_user_and_link(
        app, connector_id=connector.id, slack_user_id="U_SOCK"
    )
    incident = await _seed_incident(app)
    async with app.state.session_factory() as db:
        await IncidentPageRepo.create(
            db, TEST_ORG_ID, incident_id=incident.id, user_id=user.id
        )
        await db.commit()
    payload = _block_actions_payload(
        action_id=ACTION_ACK, incident_id=incident.id, user_id="U_SOCK"
    )
    payload["response_url"] = RESPONSE_URL

    await handle_envelope(
        app, app.state.session_factory, connector.id, "interactive", payload
    )

    [(url, text)] = answers
    assert url == RESPONSE_URL
    assert "acknowledged" in text or "recorded" in text
    async with app.state.session_factory() as db:
        active = await IncidentAssignmentRepo.get_active(db, TEST_ORG_ID, incident.id)
        assert active is not None and active.assigned_to == user.id
        reloaded = await BotConnectorRepo.get_by_id(db, TEST_ORG_ID, connector.id)
        assert reloaded.callback_status == "verified"


async def test_a_slash_command_over_the_socket_answers_through_its_response_url(
    app, answers
):
    connector = await _socket_connector(app)
    await _seed_user_and_link(app, connector_id=connector.id, slack_user_id="U_CMD")
    command = {
        "command": "/opsmender-status",
        "text": "",
        "user_id": "U_CMD",
        "response_url": RESPONSE_URL,
    }

    await handle_envelope(
        app, app.state.session_factory, connector.id, "slash_commands", command
    )
    await handle_envelope(
        app,
        app.state.session_factory,
        connector.id,
        "slash_commands",
        {**command, "command": "/nope"},
    )

    assert answers[0] == (RESPONSE_URL, "No active escalation chains. :tada:")
    assert answers[1][1].startswith("Unknown command `/nope`.")


async def test_an_envelope_for_a_connector_that_left_socket_mode_does_nothing(
    app, answers
):
    connector = await _socket_connector(app)
    async with app.state.session_factory() as db:
        row = await BotConnectorRepo.get_by_id(db, TEST_ORG_ID, connector.id)
        row.config = {"inbound_mode": "http"}
        await db.commit()
    await handle_envelope(
        app,
        app.state.session_factory,
        connector.id,
        "slash_commands",
        {"command": "/opsmender-status", "user_id": "U1", "response_url": RESPONSE_URL},
    )
    assert answers == []


def _check(connector, name):
    found = [c for c in _connector_config_checks(connector) if c.name == name]
    return found[0] if found else None


def test_check_configuration_needs_an_app_token_only_in_socket_mode(monkeypatch):
    def slack(mode):
        return BotConnector(
            platform="slack",
            is_enabled=True,
            credentials={"signing_secret": "s", "bot_token": "xoxb-t"},
            config={"inbound_mode": mode, "app_token_env": "M1_97_TEST_TOKEN"},
            allowed_capabilities=["paging"],
        )

    monkeypatch.delenv("M1_97_TEST_TOKEN", raising=False)
    missing = _check(slack("socket"), "socket_mode")
    assert missing.level == "fail" and "M1_97_TEST_TOKEN" in missing.detail
    monkeypatch.setenv("M1_97_TEST_TOKEN", "xoxb-a-bot-token")
    assert _check(slack("socket"), "socket_mode").level == "fail"
    monkeypatch.setenv("M1_97_TEST_TOKEN", TOKEN)
    passed = _check(slack("socket"), "socket_mode")
    assert passed.level == "pass" and TOKEN not in passed.detail
    assert _check(slack("http"), "socket_mode") is None


async def test_the_doctor_reports_each_socket_mode_connector(app, monkeypatch):
    from backend.doctor import check_slack_socket_mode

    factory = app.state.session_factory

    async def accept(token):
        return "wss://wss.slack.test/link"

    async def refuse(token):
        raise SocketModeError("invalid_auth")

    assert await check_slack_socket_mode(factory, probe=accept) == []
    connector = await _socket_connector(app)

    monkeypatch.delenv(TOKEN_ENV, raising=False)
    [result] = await check_slack_socket_mode(factory, probe=accept)
    assert result.status == "fail" and TOKEN_ENV in result.detail

    monkeypatch.setenv(TOKEN_ENV, TOKEN)
    [result] = await check_slack_socket_mode(factory, probe=refuse)
    assert result.status == "fail" and "invalid_auth" in result.detail
    [result] = await check_slack_socket_mode(factory, probe=accept)
    assert result.status == "warn" and "no process holds" in result.detail

    now = datetime.now(timezone.utc)
    async with factory() as db:
        await RuntimeConfigRepo.take_lease(
            db, TEST_ORG_ID, lease_key(connector.id), "host:1:ab", now=now, seconds=60
        )
        await db.commit()
    [result] = await check_slack_socket_mode(factory, probe=accept)
    assert result.status == "ok" and "host:1:ab" in result.detail
    assert TOKEN not in result.detail

    async with factory() as db:
        await RuntimeConfigRepo.take_lease(
            db,
            TEST_ORG_ID,
            lease_key(connector.id),
            "host:1:ab",
            now=now - timedelta(seconds=120),
            seconds=60,
        )
        await db.commit()
    [result] = await check_slack_socket_mode(factory, probe=accept)
    assert result.status == "warn" and "stopped renewing" in result.detail
