"""M1-32 (R-07, O-12): intake refuses alert bodies over the limit (1 MiB by
default) with 413 before reading them in full, declared or streamed, and the
token's delivery log keeps a short row naming the size and the limit, never
the body. A body at the limit is accepted.

M1-51 (N-03): intake refuses a body whose objects and arrays nest more than 64
levels with 400 and the same kind of short row, and other routes answer a body
nested too deeply to read with 400 instead of a server error."""

from __future__ import annotations

import json
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from backend.config_loader import AppConfig, IngestConfig
from backend.db.models import Incident, IngestLog, UserNotificationPref
from tests.test_ingest import (
    _create_paged_service,
    _create_token,
    admin_headers as _admin_headers_fixture,
    app as _app_fixture,
    client as _client_fixture,
)

app = pytest.fixture(_app_fixture.__wrapped__)
client = pytest.fixture(_client_fixture.__wrapped__)
admin_headers = pytest.fixture(_admin_headers_fixture.__wrapped__)

MIB = 1024 * 1024


def _alert(size: int, alert_id: str = "big-1") -> bytes:
    """A JSON alert of exactly ``size`` bytes."""
    base = {"title": f"disk full {alert_id}", "severity": "high", "id": alert_id}
    base["description"] = ""
    padding = size - len(json.dumps(base).encode())
    assert padding >= 0
    base["description"] = "x" * padding
    body = json.dumps(base).encode()
    assert len(body) == size
    return body


async def _streamed(body: bytes, chunk: int = 64 * 1024):
    for start in range(0, len(body), chunk):
        yield body[start : start + chunk]


async def _route(app, client: AsyncClient, admin_headers, kind: str):
    """(url, headers) for a service intake URL or the token-authed webhook."""
    if kind == "service":
        service = await _create_paged_service(
            client,
            app,
            admin_headers,
            name=f"Big{uuid.uuid4().hex[:4]}",
            alert_grouping="off",
        )
        return service["intake_url"], {}
    raw, _ = await _create_token(app)
    return "/incidents/ingest", {"X-OpsMender-Token": raw}


async def _incidents(app) -> int:
    async with app.state.session_factory() as db:
        return await db.scalar(select(func.count()).select_from(Incident))


async def _logs(app) -> list[IngestLog]:
    async with app.state.session_factory() as db:
        return list((await db.execute(select(IngestLog))).scalars())


def _nested(depth: int, shape: str = "object") -> bytes:
    """JSON whose objects (or arrays) nest exactly ``depth`` levels."""
    if shape == "list":
        return b"[" * depth + b"]" * depth
    return b'{"a":' * depth + b"1" + b"}" * depth


def _deep_alert(depth: int, alert_id: str) -> bytes:
    """An alert whose objects nest exactly ``depth`` levels, itself included."""
    details = _nested(depth - 1).decode()
    return (
        f'{{"title": "disk full {alert_id}", "severity": "high", '
        f'"id": "{alert_id}", "details": {details}}}'
    ).encode()


def test_the_limit_defaults_to_one_mib_and_is_configurable(tmp_path):
    assert IngestConfig().max_body_bytes == MIB
    env = tmp_path / ".env"
    env.write_text("OPSMENDER_INGEST_MAX_BODY_BYTES=2048\n")
    assert AppConfig.load(env).ingest.max_body_bytes == 2048
    with pytest.raises(ValueError, match="must be >= 1024"):
        IngestConfig(max_body_bytes=100)


@pytest.mark.parametrize("kind", ["service", "webhook"])
async def test_a_declared_body_over_the_limit_gets_413(
    app, client: AsyncClient, admin_headers, kind
):
    url, headers = await _route(app, client, admin_headers, kind)
    body = _alert(MIB + 1)

    resp = await client.post(
        url, content=body, headers={**headers, "Content-Type": "application/json"}
    )

    assert resp.status_code == 413, resp.text
    assert resp.json()["detail"] == "Alert body is over the 1,048,576-byte intake limit"
    assert await _incidents(app) == 0
    [log] = await _logs(app)
    assert log.raw_payload == {"body_bytes": MIB + 1, "limit_bytes": MIB}
    assert log.error == (
        "Body over the 1,048,576-byte intake limit (1,048,577 bytes); not read"
    )
    assert log.dedup_action == "skipped" and log.incident_id is None


@pytest.mark.parametrize("kind", ["service", "webhook"])
async def test_a_streamed_body_over_the_limit_gets_413(
    app, client: AsyncClient, admin_headers, kind
):
    url, headers = await _route(app, client, admin_headers, kind)

    resp = await client.post(
        url,
        content=_streamed(_alert(2 * MIB)),
        headers={**headers, "Content-Type": "application/json"},
    )

    assert resp.request.headers.get("content-length") is None  # chunked
    assert resp.status_code == 413, resp.text
    assert await _incidents(app) == 0
    [log] = await _logs(app)
    assert log.raw_payload == {"body_bytes": None, "limit_bytes": MIB}
    assert log.error == (
        "Body over the 1,048,576-byte intake limit (more than that); not read"
    )


@pytest.mark.parametrize("kind", ["service", "webhook"])
async def test_a_body_at_the_limit_is_accepted(
    app, client: AsyncClient, admin_headers, kind
):
    url, headers = await _route(app, client, admin_headers, kind)

    declared = await client.post(
        url,
        content=_alert(MIB, "edge-1"),
        headers={**headers, "Content-Type": "application/json"},
    )
    streamed = await client.post(
        url,
        content=_streamed(_alert(MIB, "edge-2")),
        headers={**headers, "Content-Type": "application/json"},
    )

    assert declared.status_code == streamed.status_code == 200
    assert (
        declared.json()["dedup_action"]
        == streamed.json()["dedup_action"]
        == ("created")
    )
    assert await _incidents(app) == 2


async def test_a_lower_limit_from_config_applies(
    app, client: AsyncClient, admin_headers
):
    app.state.config.ingest.max_body_bytes = 4096
    url, headers = await _route(app, client, admin_headers, "service")

    small = await client.post(url, content=_alert(4096, "ok-1"))
    big = await client.post(url, content=_alert(4097, "big-2"))

    assert (small.status_code, big.status_code) == (200, 413)
    assert big.json()["detail"] == "Alert body is over the 4,096-byte intake limit"


@pytest.mark.parametrize("shape", ["object", "list"])
@pytest.mark.parametrize("kind", ["service", "webhook"])
async def test_a_body_nested_3000_levels_deep_gets_400(
    app, client: AsyncClient, admin_headers, kind, shape
):
    url, headers = await _route(app, client, admin_headers, kind)

    # The JSON parser gives up on this on Windows; elsewhere it parses and the
    # 64-level check refuses it. Both are the same refusal.
    resp = await client.post(
        url,
        content=_nested(3000, shape),
        headers={**headers, "Content-Type": "application/json"},
    )

    assert resp.status_code == 400, resp.text
    assert resp.json() == {"detail": "Alert body nests too deeply"}
    assert await _incidents(app) == 0
    [log] = await _logs(app)
    assert log.raw_payload == {"depth_limit": 64}
    assert log.error == "Body nesting deeper than the 64-level intake limit; not stored"
    assert log.dedup_action == "skipped" and log.incident_id is None


@pytest.mark.parametrize("kind", ["service", "webhook"])
async def test_a_body_nested_64_levels_is_accepted_and_65_refused(
    app, client: AsyncClient, admin_headers, kind
):
    url, headers = await _route(app, client, admin_headers, kind)
    headers = {**headers, "Content-Type": "application/json"}

    at_limit = await client.post(
        url, content=_deep_alert(64, "deep-64"), headers=headers
    )
    over = await client.post(url, content=_deep_alert(65, "deep-65"), headers=headers)

    assert at_limit.status_code == 200, at_limit.text
    assert at_limit.json()["dedup_action"] == "created"
    assert over.status_code == 400, over.text
    assert over.json() == {"detail": "Alert body nests too deeply"}
    async with app.state.session_factory() as db:
        titles = list((await db.execute(select(Incident.title))).scalars())
    assert titles == ["disk full deep-64"]
    logs = await _logs(app)
    assert sorted(log.dedup_action for log in logs) == ["created", "skipped"]
    assert [log.raw_payload for log in logs if log.dedup_action == "skipped"] == [
        {"depth_limit": 64}
    ]


async def test_other_routes_answer_400_for_a_body_nested_too_deeply(
    app, client: AsyncClient, admin_headers
):
    url = "/users/me/notification-preferences"
    saved = await client.put(
        url, json={"routing": {"P0": ["email"]}}, headers=admin_headers
    )
    assert saved.status_code == 200, saved.text
    headers = {**admin_headers, "Content-Type": "application/json"}

    # 2,000 levels parse on every platform, then fail validation with errors
    # too deep to encode for a 422. At 3,000 the JSON parser itself may give
    # up first (Python's C recursion limit is 3,000 on Windows), which FastAPI
    # answers with its own 400.
    as_list = await client.put(url, content=_nested(2000, "list"), headers=headers)
    in_field = await client.put(
        url, content=b'{"quiet_hours": ' + _nested(2000) + b"}", headers=headers
    )
    deeper = await client.put(url, content=_nested(3000, "list"), headers=headers)
    usual = await client.put(url, json={"routing": {"P7": []}}, headers=admin_headers)

    assert (as_list.status_code, in_field.status_code) == (400, 400)
    assert (
        as_list.json() == in_field.json() == {"detail": "Request body nests too deeply"}
    )
    assert deeper.status_code == 400, deeper.text
    assert usual.status_code == 422, usual.text
    assert "must be P0 to P3 or in_app" in usual.json()["detail"][0]["msg"]
    async with app.state.session_factory() as db:
        [pref] = (await db.execute(select(UserNotificationPref))).scalars().all()
    assert pref.routing == {"P0": ["email"]}
    assert pref.quiet_hours is None


async def test_other_routes_take_64_levels_and_store_nothing_deeper(
    app, client: AsyncClient, admin_headers
):
    """The body check runs before any route: a free-form field one level past
    the limit is refused and never stored, one at the limit is saved."""
    url = "/users/me/notification-preferences"
    headers = {**admin_headers, "Content-Type": "application/json"}

    def channels(levels: int) -> bytes:
        # The body, "channels" and "x" are three levels; the rest nests inside.
        return b'{"channels": {"x": ' + _nested(levels - 2) + b"}}"

    at_limit = await client.put(url, content=channels(64), headers=headers)
    assert at_limit.status_code == 200, at_limit.text
    over = await client.put(url, content=channels(65), headers=headers)
    assert over.status_code == 400, over.text
    assert over.json() == {"detail": "Request body nests too deeply"}
    # The security headers still go out with the refusal.
    assert over.headers["x-content-type-options"] == "nosniff"
    deep = await client.put(url, content=channels(1000), headers=headers)
    assert deep.status_code == 400, deep.text

    async with app.state.session_factory() as db:
        [pref] = (await db.execute(select(UserNotificationPref))).scalars().all()
    saved = pref.channels["x"]
    levels = 2
    while isinstance(saved, dict):
        saved, levels = saved.get("a"), levels + 1
    assert levels == 64 and saved == 1
    assert (await client.get(url, headers=admin_headers)).status_code == 200


async def test_a_token_nested_too_deeply_is_no_token(client: AsyncClient):
    """A bearer token whose header nests too deeply to decode is refused like
    any other bad token (401), never a server error."""
    resp = await client.get(
        "/auth/me", headers={"Authorization": f"Bearer {_deep_token()}"}
    )
    assert resp.status_code == 401, resp.text


def _deep_token(depth: int = 12000) -> str:
    """A JWT-shaped token whose header nests ``depth`` levels deep, past what
    the JSON parser reads on Windows and on Linux."""
    import base64

    header = base64.urlsafe_b64encode(_nested(depth, "list")).rstrip(b"=").decode()
    return f"{header}.e30.c2ln"


async def test_another_method_on_the_intake_path_is_checked(
    client: AsyncClient, admin_headers
):
    """Only POST is intake. PATCH /incidents/ingest is the incident update
    route, so its body is checked like any other route's."""
    resp = await client.patch(
        "/incidents/ingest",
        content=_nested(2000, "list"),
        headers={**admin_headers, "Content-Type": "application/json"},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json() == {"detail": "Request body nests too deeply"}


async def test_a_client_that_leaves_mid_body_reaches_the_route_as_sent():
    """The route gets what arrived and then the disconnect, as it would without
    the check, and answers it as it always has; the check sends nothing."""
    from backend.api.json_depth import JsonDepthLimit

    arrived = [
        {"type": "http.request", "body": b'{"a": [', "more_body": True},
        {"type": "http.disconnect"},
    ]
    incoming, seen, sent = iter(arrived), [], []

    async def route(scope, receive, send):
        while True:
            message = await receive()
            seen.append(message)
            if message["type"] == "http.disconnect":
                return

    async def receive():
        return next(incoming)

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": "PUT",
        "path": "/users/me/notification-preferences",
        "headers": [(b"content-type", b"application/json")],
    }
    await JsonDepthLimit(route)(scope, receive, send)
    assert seen == arrived
    assert sent == []


def test_a_token_nested_too_deeply_to_read_is_a_bad_token():
    from jose.exceptions import JWTError

    from backend.auth.jwt_decode import decode

    with pytest.raises(JWTError, match="Token nests too deeply to read"):
        decode(_deep_token(), "secret", algorithms=["HS256"])


def test_every_token_decode_goes_through_the_one_that_refuses_deep_tokens():
    """A new place that decodes a token must call backend.auth.jwt_decode, or a
    token nested too deeply to read would be a server error there."""
    from pathlib import Path

    backend = Path(__file__).resolve().parents[1] / "backend"
    direct = sorted(
        str(path.relative_to(backend))
        for path in backend.rglob("*.py")
        if path.name != "jwt_decode.py"
        and "jwt.decode(" in path.read_text(encoding="utf-8")
    )
    assert direct == []


async def test_an_mfa_challenge_nested_too_deeply_is_refused(client: AsyncClient):
    resp = await client.post(
        "/auth/mfa/verify", json={"mfa_token": _deep_token(), "totp_code": "000000"}
    )
    assert resp.status_code == 401, resp.text
    assert resp.json() == {"detail": "Invalid or expired MFA challenge."}


async def test_a_voice_link_nested_too_deeply_is_refused(client: AsyncClient):
    resp = await client.post(f"/paging/voice/ack/{_deep_token()}", data={"Digits": "1"})
    assert resp.status_code == 200, resp.text
    assert "This acknowledgement link is invalid or has expired." in resp.text


async def test_sign_in_states_nested_too_deeply_are_refused(app):
    """The SSO and bot sign-in callbacks read a signed state from the query
    before anything else; one nested too deeply is an invalid state."""
    from fastapi import HTTPException

    from backend.api.routes.sso import _decode_state
    from backend.auth.bot_oauth import verify_state

    with pytest.raises(HTTPException) as refused:
        _decode_state(_deep_token())
    assert refused.value.status_code == 400
    assert refused.value.detail == "Invalid state: Token nests too deeply to read"
    with pytest.raises(ValueError, match="Invalid OAuth state: Token nests too deeply"):
        verify_state(_deep_token())
