"""M1-116 (F6): a session or notification stream's bearer token travels in
the handshake's ``Sec-WebSocket-Protocol`` header, not the URL, and the
access log blanks any token that still arrives as a query parameter."""

from __future__ import annotations

import asyncio
import logging
import uuid

import pytest
from httpx import AsyncClient

from backend.api.routes import ws
from backend.auth.api_tokens import API_TOKEN_PREFIX
from backend.logging_config import (
    AccessSecretFilter,
    configure_logging,
    redact_access_secrets,
    redact_query_tokens,
)
from tests.test_ownership_lifecycle import (
    _headers,
    _user,
    app as _app_fixture,
    client as _client_fixture,
)
from tests.test_streams_follow_account import _session

app = pytest.fixture(_app_fixture.__wrapped__)
client = pytest.fixture(_client_fixture.__wrapped__)


class _Handshake:
    """A socket as the route sees it before accepting: headers and close."""

    def __init__(self, *protocols: str) -> None:
        self.headers = (
            {"sec-websocket-protocol": ", ".join(protocols)} if protocols else {}
        )
        self.accepted = False
        self.subprotocol: str | None = None
        self.closed_code: int | None = None
        self.sent: list[dict] = []

    async def accept(self, subprotocol: str | None = None) -> None:
        self.accepted = True
        self.subprotocol = subprotocol

    async def close(self, code: int) -> None:
        self.closed_code = code

    async def send_json(self, message: dict) -> None:
        self.sent.append(message)


@pytest.fixture(autouse=True)
def _fast_recheck(monkeypatch):
    monkeypatch.setattr(ws, "RECHECK_SECONDS", 0.05)


async def _viewer_token(app, client: AsyncClient) -> str:
    await _user(app, "handshake-viewer", role="viewer")
    headers = await _headers(client, "handshake-viewer")
    return headers["Authorization"].removeprefix("Bearer ")


def _stream(name: str, socket, app_session: uuid.UUID | None, **kwargs):
    if name == "session":
        return ws.session_stream(socket, app_session, **kwargs)
    return ws.notifications_stream(socket, **kwargs)


async def _run_briefly(coroutine) -> None:
    task = asyncio.create_task(coroutine)
    await asyncio.sleep(0.15)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("stream", ["session", "notifications"])
async def test_the_handshake_token_opens_the_stream_without_a_query(
    app, client: AsyncClient, stream
):
    token = await _viewer_token(app, client)
    socket = _Handshake(ws.STREAM_SUBPROTOCOL, ws.BEARER_SUBPROTOCOL_PREFIX + token)

    await _run_briefly(_stream(stream, socket, await _session(app)))

    assert socket.accepted and socket.closed_code is None
    # The server answers with the plain subprotocol the client offered, never
    # echoing the bearer one back.
    assert socket.subprotocol == ws.STREAM_SUBPROTOCOL
    assert {"type": "ping", "data": {}} in socket.sent


@pytest.mark.parametrize("stream", ["session", "notifications"])
async def test_a_handshake_token_that_does_not_sign_in_is_refused(
    app, client: AsyncClient, stream
):
    socket = _Handshake(
        ws.STREAM_SUBPROTOCOL, ws.BEARER_SUBPROTOCOL_PREFIX + "not.a.jwt"
    )

    await asyncio.wait_for(_stream(stream, socket, uuid.uuid4()), timeout=2)

    assert not socket.accepted and socket.closed_code == 1008
    assert socket.sent == []


@pytest.mark.parametrize("stream", ["session", "notifications"])
async def test_a_stream_without_any_token_is_refused(app, stream):
    socket = _Handshake(ws.STREAM_SUBPROTOCOL)

    await asyncio.wait_for(_stream(stream, socket, uuid.uuid4(), token=None), timeout=2)

    assert not socket.accepted and socket.closed_code == 1008


@pytest.mark.parametrize("stream", ["session", "notifications"])
async def test_an_api_token_in_the_handshake_is_refused(app, stream):
    socket = _Handshake(
        ws.STREAM_SUBPROTOCOL, ws.BEARER_SUBPROTOCOL_PREFIX + API_TOKEN_PREFIX + "x"
    )

    await asyncio.wait_for(_stream(stream, socket, uuid.uuid4()), timeout=2)

    assert not socket.accepted and socket.closed_code == 4401


async def test_the_handshake_token_wins_over_a_query_token(app, client: AsyncClient):
    token = await _viewer_token(app, client)
    socket = _Handshake(ws.STREAM_SUBPROTOCOL, ws.BEARER_SUBPROTOCOL_PREFIX + token)

    await _run_briefly(
        ws.notifications_stream(socket, token=API_TOKEN_PREFIX + "ignored")
    )

    assert socket.accepted and socket.closed_code is None


@pytest.mark.parametrize("stream", ["session", "notifications"])
async def test_an_older_client_may_still_send_the_query_token(
    app, client: AsyncClient, stream
):
    token = await _viewer_token(app, client)
    socket = _Handshake()

    await _run_briefly(_stream(stream, socket, await _session(app), token=token))

    assert socket.accepted and socket.closed_code is None
    assert socket.subprotocol is None


# ---------------------------------------------------------------------------
# Access-log redaction
# ---------------------------------------------------------------------------

_JWT_LIKE = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1bml0In0.c2lnbmF0dXJl"


def test_query_tokens_are_blanked_and_other_queries_kept():
    path = f"/notifications/stream?token={_JWT_LIKE}"
    assert redact_query_tokens(path) == "/notifications/stream?token=[redacted]"
    assert (
        redact_query_tokens(f"/incidents?status=open&token={_JWT_LIKE}&page=2")
        == "/incidents?status=open&token=[redacted]&page=2"
    )
    assert (
        redact_query_tokens(f"/x?access_token={_JWT_LIKE}")
        == "/x?access_token=[redacted]"
    )
    assert redact_query_tokens("/incidents?status=open&page=2") == (
        "/incidents?status=open&page=2"
    )
    # The intake redaction (S-108) still applies alongside it.
    assert redact_access_secrets(f"/api/v1/intake/abcdefghijkl?token={_JWT_LIKE}") == (
        "/api/v1/intake/abcdefgh…?token=[redacted]"
    )


def _record(msg: str, *args) -> logging.LogRecord:
    return logging.LogRecord("uvicorn.access", logging.INFO, "", 0, msg, args, None)


def test_the_access_filter_redacts_websocket_and_http_record_formats():
    filter_ = AccessSecretFilter()

    handshake = _record(
        '%s - "WebSocket %s" [accepted]',
        "127.0.0.1:5000",
        f"/notifications/stream?token={_JWT_LIKE}",
    )
    assert filter_.filter(handshake) is True
    assert _JWT_LIKE not in handshake.getMessage()
    assert "/notifications/stream?token=[redacted]" in handshake.getMessage()

    request = _record(
        '%s - "%s %s HTTP/%s" %d',
        "127.0.0.1:5000",
        "GET",
        f"/sessions/{uuid.uuid4()}/stream?token={_JWT_LIKE}",
        "1.1",
        101,
    )
    filter_.filter(request)
    assert _JWT_LIKE not in request.getMessage()
    assert request.args[4] == 101

    plain = _record("%s - %s", "127.0.0.1:5000", "/health/ready")
    filter_.filter(plain)
    assert plain.getMessage() == "127.0.0.1:5000 - /health/ready"


def test_configure_logging_installs_the_filter_on_the_access_logger():
    access = logging.getLogger("uvicorn.access")
    for existing in list(access.filters):
        if isinstance(existing, AccessSecretFilter):
            access.removeFilter(existing)
    try:
        configure_logging("INFO")
        configure_logging("INFO")
        assert sum(isinstance(f, AccessSecretFilter) for f in access.filters) == 1
    finally:
        configure_logging("INFO")
