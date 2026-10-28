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
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import websockets

from backend.api.routes.bot_connectors import _connector_config_checks
from backend.bots import slack_socket
from backend.bots.slack_socket import (
    FAILURE_TEXT,
    SlackSocketManager,
    SocketModeClient,
    SocketModeError,
    handle_envelope,
    bot_app_id,
    lease_key,
    open_socket_url,
    state_key,
    token_conflicts,
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


async def _socket_connector(
    app, *, mode="socket", enabled=True, name=None, token_env=TOKEN_ENV, bot=None
):
    async with app.state.session_factory() as db:
        connector = await BotConnectorRepo.create(
            db,
            TEST_ORG_ID,
            name=name or f"slack-{mode}-{'on' if enabled else 'off'}",
            platform="slack",
            config={"inbound_mode": mode, "app_token_env": token_env},
            # Each channel its own Slack app unless a test shares one.
            credentials={
                "signing_secret": "unused",
                "bot_token": bot or f"xoxb-{uuid.uuid4().hex[:12]}",
            },
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

    def __init__(self, hello=None):
        self.hello = hello or {"type": "hello"}
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
        await ws.send(json.dumps(self.hello))

        async def read():
            async for raw in ws:
                self.received.append(json.loads(raw))

        reader = asyncio.create_task(read())
        try:
            while True:
                message = await self.queue.get()
                if message == "close":
                    break
                if message == "abort":
                    # Drop the connection without a close frame, as a network
                    # failure would, once the hello has reached the client (an
                    # abort discards what is not sent yet).
                    await asyncio.sleep(0.2)
                    ws.transport.abort()
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
    await asyncio.wait([task], timeout=2)


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


async def test_after_a_socket_said_hello_the_wait_starts_over(monkeypatch):
    """Failures before hello double the wait. When Slack asks for a new
    socket the next one opens at once; when a socket that said hello drops,
    the wait goes back to the first one."""
    monkeypatch.setattr(slack_socket.random, "random", lambda: 1.0)
    waits: list[float] = []

    async def sleep(seconds):
        waits.append(seconds)

    async def handle(kind, payload):
        pass

    async with _FakeSlack() as slack:
        await slack.queue.put("abort")
        await slack.queue.put({"type": "disconnect", "reason": "refresh_requested"})
        await slack.queue.put({"type": "disconnect", "reason": "refresh_requested"})
        calls = 0

        async def open_url(token):
            nonlocal calls
            calls += 1
            if calls <= 2:
                raise SocketModeError("internal_error")
            if calls == 6:
                raise asyncio.CancelledError
            return slack.url

        client = SocketModeClient(
            TOKEN,
            handle,
            open_url=open_url,
            backoff_first=1.0,
            backoff_max=8.0,
            sleep=sleep,
        )
        with pytest.raises(asyncio.CancelledError):
            await client.run()
        assert slack.connections == 3
        assert not client.connected

    # Two failures (1, 2); the drop after hello waits the first again (1); the
    # first refresh reconnects at once; a second within QUICK_RECONNECT_GAP
    # waits the first (1).
    assert waits == [1.0, 2.0, 1.0, 1.0]


async def test_a_socket_for_another_app_is_closed_and_not_reopened():
    handled = []

    async def handle(kind, payload):
        handled.append(kind)

    hello = {"type": "hello", "connection_info": {"app_id": "A_OTHER"}}
    async with _FakeSlack(hello=hello) as slack:
        await slack.queue.put(
            {"envelope_id": "e1", "type": "interactive", "payload": {}}
        )

        async def open_url(token):
            return slack.url

        checked = []

        async def check_app(app_id):
            checked.append(app_id)
            return app_id == "A_MINE"

        client = SocketModeClient(TOKEN, handle, open_url=open_url, backoff_first=0.01)
        client.check_app = check_app
        await asyncio.wait_for(client.run(), timeout=5)
        assert slack.connections == 1

    assert checked == ["A_OTHER"] and client.wrong_app == "A_OTHER"
    assert handled == [] and not client.connected


async def test_bot_app_id_asks_slack_which_app_the_bot_belongs_to(monkeypatch):
    real_client = httpx.AsyncClient
    seen = []

    def handler(request):
        seen.append((request.url.path, request.url.params.get("bot")))
        if request.url.path.endswith("auth.test"):
            return httpx.Response(200, json={"ok": True, "bot_id": "B1"})
        return httpx.Response(
            200, json={"ok": True, "bot": {"id": "B1", "app_id": "A1"}}
        )

    monkeypatch.setattr(
        slack_socket.httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )
    assert await bot_app_id("xoxb-1") == "A1"
    assert seen == [("/api/auth.test", None), ("/api/bots.info", "B1")]

    def broken(request):
        return httpx.Response(200, json={"ok": False, "error": "invalid_auth"})

    monkeypatch.setattr(
        slack_socket.httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(broken), **kw),
    )
    assert await bot_app_id("xoxb-1") is None


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
    """Stands in for a socket client: records which tokens got a socket and
    keeps the handler the manager gave it. Its socket is connected."""

    made: list["_Recorder"] = []
    connects = True

    def __init__(self, token, handle):
        self.token = token
        self.handle = handle
        self.connected = type(self).connects
        self.tasks: set[asyncio.Task] = set()
        _Recorder.made.append(self)

    async def run(self):
        await asyncio.Event().wait()


class _Unreachable(_Recorder):
    """A client whose socket never says hello."""

    connects = False


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
    first = a._sockets[connector.id].task
    # A renewal keeps the running socket.
    assert await a.tick(now + timedelta(seconds=20)) == {connector.id}
    assert a._sockets[connector.id].task is first and not first.done()
    assert await b.tick(now + timedelta(seconds=25)) == set()
    assert [r.token for r in _Recorder.made] == [TOKEN]

    # a stops renewing: once the lease runs out b takes over, and a closes
    # its socket on its next tick.
    assert await b.tick(now + timedelta(seconds=81)) == {connector.id}
    assert await a.tick(now + timedelta(seconds=82)) == set()
    await asyncio.wait([first], timeout=2)
    assert first.cancelled()
    assert len(_Recorder.made) == 2
    assert not a._sockets and set(b._sockets) == {connector.id}

    # Stopping gives the lease up at once.
    await b.stop()
    assert await a.tick(now + timedelta(seconds=83)) == {connector.id}
    await a.stop()


async def test_a_socket_whose_client_ended_is_started_again(app):
    class _Ends(_Recorder):
        async def run(self):
            return

    _Recorder.made = []
    connector = await _socket_connector(app)
    manager = SlackSocketManager(
        app,
        app.state.session_factory,
        holder="x",
        env={TOKEN_ENV: TOKEN},
        client_factory=_Ends,
    )
    now = datetime.now(timezone.utc)
    assert await manager.tick(now) == {connector.id}
    await asyncio.gather(manager._sockets[connector.id].task)
    assert await manager.tick(now + timedelta(seconds=20)) == {connector.id}
    assert len(_Recorder.made) == 2
    await manager.stop()


async def test_channels_that_share_an_app_token_get_no_socket(app, monkeypatch):
    from backend.doctor import check_slack_socket_mode

    _Recorder.made = []
    first = await _socket_connector(app, name="slack-first")
    second = await _socket_connector(app, name="slack-second")
    own = await _socket_connector(app, name="slack-own", token_env="M1_97_OWN_TOKEN")
    env = {TOKEN_ENV: TOKEN, "M1_97_OWN_TOKEN": "xapp-1-B2-own"}
    manager = SlackSocketManager(
        app,
        app.state.session_factory,
        holder="x",
        env=env,
        client_factory=_Recorder,
    )
    # Slack would spread the clicks of one app over both sockets, so neither
    # channel gets one; a channel with its own app is unaffected.
    assert await manager.tick() == {own.id}
    assert [r.token for r in _Recorder.made] == ["xapp-1-B2-own"]
    assert token_conflicts([first, second, own], env) == {
        first.id: ["slack-second"],
        second.id: ["slack-first"],
    }
    # One bot token is one Slack app too, whatever the app-level tokens.
    twins = [
        await _socket_connector(
            app, name="slack-twin-a", token_env="M1_97_A", bot="xoxb-same"
        ),
        await _socket_connector(
            app, name="slack-twin-b", token_env="M1_97_B", bot="xoxb-same"
        ),
    ]
    assert token_conflicts(twins, {"M1_97_A": "xapp-1-a", "M1_97_B": "xapp-1-b"}) == {
        twins[0].id: ["slack-twin-b"],
        twins[1].id: ["slack-twin-a"],
    }
    # The same token in another variable is still the same app.
    renamed = {**env, "M1_97_OWN_TOKEN": TOKEN}
    assert set(token_conflicts([first, second, own], renamed)) == {
        first.id,
        second.id,
        own.id,
    }

    for name, value in env.items():
        monkeypatch.setenv(name, value)
    shared = _connector_config_checks(first, shared_token_with=["slack-second"])
    [check] = [c for c in shared if c.name == "socket_mode"]
    assert check.level == "fail" and "slack-second" in check.detail
    assert TOKEN not in check.detail

    async def accept(token):
        return "wss://wss.slack.test/link"

    results = {
        r.name: r
        for r in await check_slack_socket_mode(app.state.session_factory, probe=accept)
    }
    assert results["Slack Socket Mode: slack-first"].status == "fail"
    assert "slack-second" in results["Slack Socket Mode: slack-first"].detail
    assert results["Slack Socket Mode: slack-own"].status != "fail"
    await manager.stop()


async def test_a_new_token_variable_restarts_the_socket(app):
    _Recorder.made = []
    connector = await _socket_connector(app)
    manager = SlackSocketManager(
        app,
        app.state.session_factory,
        holder="x",
        env={TOKEN_ENV: TOKEN, "M1_97_NEW_TOKEN": "xapp-1-C3-new"},
        client_factory=_Recorder,
    )
    now = datetime.now(timezone.utc)
    assert await manager.tick(now) == {connector.id}
    first = manager._sockets[connector.id].task
    async with app.state.session_factory() as db:
        row = await BotConnectorRepo.get_by_id(db, TEST_ORG_ID, connector.id)
        row.config = {"inbound_mode": "socket", "app_token_env": "M1_97_NEW_TOKEN"}
        await db.commit()
    assert await manager.tick(now + timedelta(seconds=20)) == {connector.id}
    await asyncio.wait([first], timeout=2)
    assert first.cancelled()
    assert [r.token for r in _Recorder.made] == [TOKEN, "xapp-1-C3-new"]
    await manager.stop()


async def test_a_holder_that_cannot_renew_closes_its_socket_before_the_lease_can_pass(
    app,
):
    _Recorder.made = []
    connector = await _socket_connector(app)
    env = {TOKEN_ENV: TOKEN}
    factory = app.state.session_factory
    a = SlackSocketManager(app, factory, holder="a", env=env, client_factory=_Recorder)
    b = SlackSocketManager(app, factory, holder="b", env=env, client_factory=_Recorder)
    now = datetime.now(timezone.utc)
    assert await a.tick(now) == {connector.id}
    task = a._sockets[connector.id].task
    # A renewal moves the fence.
    assert await a.tick(now + timedelta(seconds=30)) == {connector.id}
    assert a.fence(now + timedelta(seconds=79)) == set()
    # Renewals fail from here on (the database is unreachable from a).
    assert a.fence(now + timedelta(seconds=80)) == {connector.id}
    await asyncio.wait([task], timeout=2)
    assert task.cancelled() and not a._sockets
    # Closed before the lease could pass: b cannot take it yet.
    assert await b.tick(now + timedelta(seconds=81)) == set()
    assert await b.tick(now + timedelta(seconds=91)) == {connector.id}
    await b.stop()


async def test_a_tick_never_runs_past_a_socket_fence(app):
    _Recorder.made = []
    await _socket_connector(app)
    manager = SlackSocketManager(
        app,
        app.state.session_factory,
        holder="x",
        env={TOKEN_ENV: TOKEN},
        client_factory=_Recorder,
    )
    now = datetime.now(timezone.utc)
    assert manager.tick_budget(now) == 20.0  # no socket: a whole renewal
    await manager.tick(now)
    assert manager.tick_budget(now + timedelta(seconds=20)) == 20.0
    assert manager.tick_budget(now + timedelta(seconds=45)) == 5.0
    assert manager.tick_budget(now + timedelta(seconds=60)) == 0.05
    await manager.stop()


@pytest.mark.parametrize("later", [["raises", "raises"], ["raises", "hangs"]])
async def test_the_loop_closes_a_socket_before_its_lease_runs_out(app, later):
    """With a real fence: after one good tick, failing ticks (fast, or one that
    hangs) still close the socket before the lease could pass."""
    _Recorder.made = []
    connector = await _socket_connector(app)
    manager = SlackSocketManager(
        app,
        app.state.session_factory,
        holder="x",
        lease_seconds=3,
        renew_seconds=1,
        env={TOKEN_ENV: TOKEN},
        client_factory=_Recorder,
    )
    # Acquire the starting lease outside the loop's one-second tick budget.
    # This test starts with a held socket; slow database setup must not turn
    # it into a test of whether the first tick can finish in one second.
    await manager.tick()
    task = manager._sockets[connector.id].task
    lease_runs_out = manager._sockets[connector.id].renewed + timedelta(seconds=3)
    plan = iter([*later, *(["raises"] * 10)])

    async def tick(now=None):
        step = next(plan)
        if step == "hangs":
            await asyncio.Event().wait()
        raise RuntimeError("database unreachable")

    manager.tick = tick
    await manager.start()
    try:
        await _until(task.done, seconds=5)
        assert task.cancelled()
        # Still require closure before the real lease expires, not three
        # seconds after observing the socket or finishing database setup.
        assert datetime.now(timezone.utc) < lease_runs_out
    finally:
        await manager.stop()


@pytest.mark.parametrize("failure", ["raises", "hangs"])
async def test_a_failed_or_hung_tick_fences_the_sockets(app, monkeypatch, failure):
    manager = SlackSocketManager(
        app,
        app.state.session_factory,
        holder="x",
        renew_seconds=1,
        env={TOKEN_ENV: TOKEN},
        client_factory=_Recorder,
    )
    fenced = []

    async def broken_tick(now=None):
        if failure == "raises":
            raise RuntimeError("database unreachable")
        await asyncio.Event().wait()

    monkeypatch.setattr(manager, "tick", broken_tick)
    monkeypatch.setattr(manager, "fence", lambda now=None: fenced.append(now) or set())
    await manager.start()
    await _until(lambda: fenced, seconds=5)
    await manager.stop()


async def test_a_holder_that_cannot_connect_gives_the_socket_up(app):
    _Recorder.made = []
    connector = await _socket_connector(app)
    env = {TOKEN_ENV: TOKEN}
    factory = app.state.session_factory
    stuck = SlackSocketManager(
        app, factory, holder="stuck", env=env, client_factory=_Unreachable
    )
    healthy = SlackSocketManager(
        app, factory, holder="healthy", env=env, client_factory=_Recorder
    )
    now = datetime.now(timezone.utc)
    for seconds in (0, 20, 40):
        assert await stuck.tick(now + timedelta(seconds=seconds)) == {connector.id}
    # A minute without a connection: the lease goes, so another process can try.
    assert await stuck.tick(now + timedelta(seconds=60)) == set()
    assert not stuck._sockets
    assert await healthy.tick(now + timedelta(seconds=61)) == {connector.id}
    # A connected holder keeps it.
    assert await healthy.tick(now + timedelta(seconds=141)) == {connector.id}
    await healthy.stop()
    # The one that gave up rests a little over one renewal, so at its own
    # cadence it skips the next tick and tries again at the one after.
    assert await stuck.tick(now + timedelta(seconds=80)) == set()
    assert await stuck.tick(now + timedelta(seconds=100)) == {connector.id}
    await stuck.stop()


async def test_the_doctor_sees_a_holder_that_is_not_connected(app, monkeypatch):
    from backend.doctor import check_slack_socket_mode

    _Recorder.made = []
    connector = await _socket_connector(app)
    monkeypatch.setenv(TOKEN_ENV, TOKEN)
    factory = app.state.session_factory

    async def accept(token):
        return "wss://wss.slack.test/link"

    stuck = SlackSocketManager(
        app,
        factory,
        holder="stuck",
        env={TOKEN_ENV: TOKEN},
        client_factory=_Unreachable,
    )
    now = datetime.now(timezone.utc)
    await stuck.tick(now)
    [result] = await check_slack_socket_mode(factory, probe=accept)
    assert result.status == "warn" and "not connected" in result.detail

    # A minute later it gives the socket up, and the doctor says why.
    await stuck.tick(now + timedelta(seconds=60))
    [result] = await check_slack_socket_mode(factory, probe=accept)
    assert result.status == "warn" and "could not connect" in result.detail
    async with factory() as db:
        raw = await RuntimeConfigRepo.get_value(
            db, TEST_ORG_ID, state_key(connector.id)
        )
    assert json.loads(raw)["problem"] == "not_connecting"
    await stuck.stop()

    connected = SlackSocketManager(
        app, factory, holder="up", env={TOKEN_ENV: TOKEN}, client_factory=_Recorder
    )
    await connected.tick(now + timedelta(seconds=61))
    await connected.tick(now + timedelta(seconds=81))
    [result] = await check_slack_socket_mode(factory, probe=accept)
    assert result.status == "ok" and "held by up" in result.detail
    await connected.stop()


async def test_a_socket_for_another_app_gets_no_socket_until_the_tokens_change(
    app, monkeypatch
):
    from backend.doctor import check_slack_socket_mode

    class _OtherApp(_Recorder):
        async def run(self):
            self.wrong_app = "A_OTHER"

    _Recorder.made = []
    connector = await _socket_connector(app, bot="xoxb-app-y")
    factory = app.state.session_factory
    manager = SlackSocketManager(
        app, factory, holder="x", env={TOKEN_ENV: TOKEN}, client_factory=_OtherApp
    )
    now = datetime.now(timezone.utc)
    assert await manager.tick(now) == {connector.id}
    assert _Recorder.made[0].check_app is not None
    await asyncio.wait([manager._sockets[connector.id].task], timeout=2)
    assert await manager.tick(now + timedelta(seconds=20)) == set()
    assert await manager.tick(now + timedelta(seconds=40)) == set()
    assert len(_Recorder.made) == 1  # not reopened
    async with factory() as db:
        assert await RuntimeConfigRepo.lease_holder(db, lease_key(connector.id)) is None

    monkeypatch.setenv(TOKEN_ENV, TOKEN)

    async def accept(token):
        return "wss://wss.slack.test/link"

    [result] = await check_slack_socket_mode(factory, probe=accept)
    assert result.status == "fail" and "A_OTHER" in result.detail

    # A new bot token is a new configuration: the socket is tried again.
    async with factory() as db:
        row = await BotConnectorRepo.get_by_id(db, TEST_ORG_ID, connector.id)
        row.credentials = {"signing_secret": "unused", "bot_token": "xoxb-app-x"}
        await db.commit()
    assert await manager.tick(now + timedelta(seconds=60)) == {connector.id}
    assert len(_Recorder.made) == 2
    await manager.stop()


async def test_the_app_check_compares_with_the_bot_token_app(app, monkeypatch):
    connector = await _socket_connector(app, bot="xoxb-app-y")
    manager = SlackSocketManager(
        app,
        app.state.session_factory,
        holder="x",
        env={TOKEN_ENV: TOKEN},
        client_factory=_Recorder,
    )
    lookups = []

    async def lookup(token):
        lookups.append(token)
        return {"xoxb-app-y": "A_Y"}.get(token)

    monkeypatch.setattr(slack_socket, "bot_app_id", lookup)
    await manager.tick()
    check = _Recorder.made[-1].check_app
    assert await check("A_Y") is True
    assert await check("A_X") is False
    assert lookups == ["xoxb-app-y"]  # looked up once
    # A bot whose app Slack does not name never closes a socket.
    manager._bots[connector.id] = "xoxb-unknown"
    assert await check("A_X") is True
    await manager.stop()


async def test_stop_lets_envelopes_being_handled_finish(app, monkeypatch):
    _Recorder.made = []
    await _socket_connector(app)
    release = asyncio.Event()
    finished = []

    async def slow(app_, factory, connector_id, kind, payload):
        await release.wait()
        finished.append(kind)

    monkeypatch.setattr(slack_socket, "handle_envelope", slow)
    manager = SlackSocketManager(
        app,
        app.state.session_factory,
        holder="x",
        env={TOKEN_ENV: TOKEN},
        client_factory=_Recorder,
    )
    await manager.tick()
    pending = asyncio.create_task(_Recorder.made[-1].handle("interactive", {}))
    await asyncio.sleep(0)
    stopping = asyncio.create_task(manager.stop())
    await asyncio.sleep(0.05)
    assert not stopping.done()  # stop waits for the envelope
    # Meanwhile the socket is closed and its lease free for another process.
    other = SlackSocketManager(
        app,
        app.state.session_factory,
        holder="other",
        env={TOKEN_ENV: TOKEN},
        client_factory=_Recorder,
    )
    assert len(await other.tick()) == 1
    await other.stop()
    release.set()
    await stopping
    assert finished == ["interactive"] and not pending.cancelled()


async def test_stop_does_not_wait_forever_for_an_envelope(app, monkeypatch):
    _Recorder.made = []
    await _socket_connector(app)

    async def stuck(app_, factory, connector_id, kind, payload):
        await asyncio.Event().wait()

    monkeypatch.setattr(slack_socket, "handle_envelope", stuck)
    monkeypatch.setattr(slack_socket, "DRAIN_SECONDS", 0.05)
    manager = SlackSocketManager(
        app,
        app.state.session_factory,
        holder="x",
        env={TOKEN_ENV: TOKEN},
        client_factory=_Recorder,
    )
    await manager.tick()
    pending = asyncio.create_task(_Recorder.made[-1].handle("slash_commands", {}))
    await asyncio.sleep(0)
    await asyncio.wait_for(manager.stop(), timeout=5)
    assert pending.cancelled()


async def test_the_manager_runs_envelopes_and_answers_a_failure(
    app, answers, monkeypatch, caplog
):
    _Recorder.made = []
    connector = await _socket_connector(app)
    await _seed_user_and_link(app, connector_id=connector.id, slack_user_id="U_WIRE")
    manager = SlackSocketManager(
        app,
        app.state.session_factory,
        holder="x",
        env={TOKEN_ENV: TOKEN},
        client_factory=_Recorder,
    )
    await manager.tick()
    handle = _Recorder.made[-1].handle
    await handle(
        "slash_commands",
        {
            "command": "/opsmender-status",
            "text": "",
            "user_id": "U_WIRE",
            "response_url": RESPONSE_URL,
        },
    )
    assert answers == [(RESPONSE_URL, "No active escalation chains. :tada:")]

    async def broken(*args, **kwargs):
        raise RuntimeError(f"failed while answering {RESPONSE_URL}")

    monkeypatch.setattr(slack_socket, "handle_envelope", broken)
    with caplog.at_level("WARNING", logger="backend.bots.slack_socket"):
        await handle("interactive", {"response_url": RESPONSE_URL})
    # The person hears it failed; the log has the place, not the message.
    assert answers[-1] == (RESPONSE_URL, FAILURE_TEXT)
    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "RuntimeError" in m and 'File "' in m and "slack_socket.py" in m
        for m in messages
    )
    assert RESPONSE_URL not in caplog.text
    await manager.stop()


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


async def test_an_envelope_for_a_turned_off_connector_does_nothing(app, answers):
    connector = await _socket_connector(app)
    user, _ = await _seed_user_and_link(
        app, connector_id=connector.id, slack_user_id="U_OFF"
    )
    incident = await _seed_incident(app)
    async with app.state.session_factory() as db:
        await IncidentPageRepo.create(
            db, TEST_ORG_ID, incident_id=incident.id, user_id=user.id
        )
        row = await BotConnectorRepo.get_by_id(db, TEST_ORG_ID, connector.id)
        row.is_enabled = False
        await db.commit()
    payload = _block_actions_payload(
        action_id=ACTION_ACK, incident_id=incident.id, user_id="U_OFF"
    )
    payload["response_url"] = RESPONSE_URL
    factory = app.state.session_factory
    await handle_envelope(app, factory, connector.id, "interactive", payload)
    await handle_envelope(
        app,
        factory,
        connector.id,
        "slash_commands",
        {
            "command": "/ack",
            "text": str(incident.id),
            "user_id": "U_OFF",
            "response_url": RESPONSE_URL,
        },
    )
    assert answers == []
    async with factory() as db:
        assert (
            await IncidentAssignmentRepo.get_active(db, TEST_ORG_ID, incident.id)
            is None
        )


async def test_a_slash_command_over_the_socket_keeps_what_it_changed(
    app, answers, monkeypatch
):
    from sqlalchemy import select

    from backend.db.models import IncidentChainState
    from backend.db.repos import EscalationChainRepo, IncidentChainStateRepo, TeamRepo

    connector = await _socket_connector(app)
    await _seed_user_and_link(app, connector_id=connector.id, slack_user_id="U_SNZ")
    incident = await _seed_incident(app, title="snooze-over-socket")
    async with app.state.session_factory() as db:
        team = await TeamRepo.create(db, TEST_ORG_ID, name="t-sock", slug="t-sock")
        chain = await EscalationChainRepo.create(
            db, TEST_ORG_ID, team_id=team.id, name="c-sock", description=None
        )
        state = await IncidentChainStateRepo.create(
            db, TEST_ORG_ID, incident_id=incident.id, chain_id=chain.id
        )
        await db.commit()
        state_id = state.id

    seen_at_post = []

    async def post(url, text):
        # Another session reads what the answer reports: it is committed.
        async with app.state.session_factory() as db:
            row = (
                await db.execute(
                    select(IncidentChainState).where(IncidentChainState.id == state_id)
                )
            ).scalar_one()
            seen_at_post.append(row.status)
        answers.append((url, text))

    monkeypatch.setattr("backend.api.routes.slack_paging._post_answer", post)
    await handle_envelope(
        app,
        app.state.session_factory,
        connector.id,
        "slash_commands",
        {
            "command": "/snooze-escalation",
            "text": f"{incident.id} 30m",
            "user_id": "U_SNZ",
            "response_url": RESPONSE_URL,
        },
    )

    [(url, text)] = answers
    assert url == RESPONSE_URL and "Snoozed" in text
    assert seen_at_post == ["paused"]
    async with app.state.session_factory() as db:
        row = (
            await db.execute(
                select(IncidentChainState).where(IncidentChainState.id == state_id)
            )
        ).scalar_one()
        assert row.status == "paused"


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

    async def unreachable(token):
        raise httpx.ConnectError("no route to slack.com")

    monkeypatch.delenv(TOKEN_ENV, raising=False)
    [result] = await check_slack_socket_mode(factory, probe=accept)
    assert result.status == "fail" and TOKEN_ENV in result.detail

    monkeypatch.setenv(TOKEN_ENV, TOKEN)
    [result] = await check_slack_socket_mode(factory, probe=refuse)
    assert result.status == "fail" and "refused" in result.detail
    assert "invalid_auth" in result.detail

    async def busy(token):
        raise SocketModeError("ratelimited")

    [result] = await check_slack_socket_mode(factory, probe=busy)
    assert result.status == "fail" and "refused" not in result.detail
    assert "ratelimited" in result.detail
    # Slack never answered, so nothing refused the token.
    [result] = await check_slack_socket_mode(factory, probe=unreachable)
    assert result.status == "fail" and "refused" not in result.detail
    assert result.detail == "Could not reach Slack: ConnectError"
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

    # Past the fence but inside the lease: the holder has closed its socket.
    async with factory() as db:
        await RuntimeConfigRepo.take_lease(
            db,
            TEST_ORG_ID,
            lease_key(connector.id),
            "host:1:ab",
            now=now - timedelta(seconds=55),
            seconds=60,
        )
        await db.commit()
    [result] = await check_slack_socket_mode(factory, probe=accept)
    assert result.status == "warn" and "socket is closed" in result.detail


async def test_shutdown_stops_the_sockets_before_sweeping_tasks(tmp_path, monkeypatch):
    """Envelopes being handled finish before the session and background tasks
    they start are swept, and before the schedulers stop."""
    from types import SimpleNamespace

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from backend.api import deps
    from backend.api.app import create_app
    from backend.config_loader import AppConfig
    from backend.db.models import Base, Organization

    url = f"sqlite+aiosqlite:///{(tmp_path / 'shutdown.db').as_posix()}"
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        db.add(Organization(id=TEST_ORG_ID, name="Shutdown", slug="shutdown"))
        await db.commit()
    order: list[str] = []

    class _Manager:
        def __init__(self, app_, factory):
            pass

        async def start(self):
            pass

        async def stop(self):
            order.append("slack sockets")

    monkeypatch.setattr(slack_socket, "SlackSocketManager", _Manager)
    monkeypatch.setattr(
        "backend.reports.scheduler.ReportScheduler.stop",
        lambda self: _record(order, "schedulers"),
    )
    monkeypatch.setenv("OPSMENDER_DEPLOYMENT_MODE", "development")
    monkeypatch.setenv("OPSMENDER_ENVIRONMENT", "development")
    monkeypatch.setenv("OPSMENDER_DATABASE_URL", url)
    monkeypatch.delenv("OPSMENDER_SERVICE_ROLE", raising=False)
    env_file = tmp_path / "shutdown.env"
    env_file.write_text(
        f"OPSMENDER_JWT_SECRET=shutdown-test-secret\nOPSMENDER_DATABASE_URL={url}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "backend.services.incident_events.IncidentEventPublisher",
        lambda database_url: SimpleNamespace(database_url=database_url),
    )
    monkeypatch.setattr(deps, "_session_factory", deps._session_factory)
    application = create_app(AppConfig.load(env_file))

    async def waits(marker):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            order.append(marker)
            raise

    async with application.router.lifespan_context(application):
        application.state.session_tasks.add(asyncio.create_task(waits("session tasks")))
        application.state.background_tasks.add(
            asyncio.create_task(waits("background tasks"))
        )
        await asyncio.sleep(0)
    assert order == ["slack sockets", "session tasks", "background tasks", "schedulers"]
    await engine.dispose()


async def _record(order, marker):
    order.append(marker)
