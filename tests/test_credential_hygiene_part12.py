"""Credential hygiene (X8, Part 12).

- S-109: a password change or reset ends every older session token, over HTTP,
  on the WebSocket streams and for a pending MFA challenge; the person who
  changed their own password gets a fresh token.
- S-108: a service keeps only a hash of its intake secret. The URL is shown on
  create and on an admin rotate; the access log never prints the secret.
- S-107: a session without a service gets no integrations.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import AsyncClient
from jose import jwt

from backend.api.auth import _auth_config, hash_password, issued_before_password_change
from backend.api.routes.ws import notifications_stream, session_stream
from backend.api.session_runner import _allowed_integration_ids_for_incident
from backend.db.repos import (
    IngestTokenRepo,
    ServiceRepo,
    UserRepo,
    _intake_secret_columns,
)
from backend.ingest.service import generate_token, hash_token
from backend.logging_config import (
    IntakeSecretFilter,
    configure_logging,
    redact_intake_secrets,
)
from tests.test_api import (
    TEST_ORG_ID,
    _enable_mfa,
    app as _app_fixture,
    auth_headers as _auth_headers_fixture,
    client as _client_fixture,
)
from tests.test_api_tokens import _FakeWebSocket

app = pytest.fixture(_app_fixture.__wrapped__)
client = pytest.fixture(_client_fixture.__wrapped__)
auth_headers = pytest.fixture(_auth_headers_fixture.__wrapped__)


def _token_issued(user_id, role, *, seconds_ago: int, token_type: str = "access"):
    """A signed token whose ``iat`` is *seconds_ago* in the past."""
    settings = _auth_config()
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": str(user_id),
            "role": role,
            "token_type": token_type,
            "iat": now - timedelta(seconds=seconds_ago),
            "exp": now + timedelta(hours=1),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _user(app, username: str):
    async with app.state.session_factory() as db:
        return await UserRepo.get_by_username(db, username)


async def _stamp_password_change(app, user_id) -> None:
    async with app.state.session_factory() as db:
        user = await UserRepo.get_by_id(db, user_id)
        user.password_changed_at = datetime.now(timezone.utc)
        await db.commit()


async def _role_headers(client: AsyncClient, app, role: str) -> dict[str, str]:
    name = f"{role}-{uuid.uuid4().hex[:6]}"
    async with app.state.session_factory() as db:
        await UserRepo.create(
            db,
            username=name,
            email=f"{name}@test.com",
            password_hash=hash_password("role-pass-123"),
            role=role,
            primary_org_id=TEST_ORG_ID,
        )
        await db.commit()
    login = await client.post(
        "/auth/login", json={"username": name, "password": "role-pass-123"}
    )
    assert login.status_code == 200, login.text
    return _bearer(login.json()["access_token"])


async def _service(client: AsyncClient, headers: dict[str, str], name="Intake"):
    suffix = uuid.uuid4().hex[:6]
    team = await client.post(
        "/teams",
        json={"name": f"{name} Team", "slug": f"{name.lower()}-t-{suffix}"},
        headers=headers,
    )
    assert team.status_code == 201, team.text
    service = await client.post(
        "/services",
        json={
            "team_id": team.json()["id"],
            "name": f"{name} {suffix}",
            "slug": f"{name.lower()}-{suffix}",
            "priority": "P3",
        },
        headers=headers,
    )
    assert service.status_code == 201, service.text
    return service.json()


def _alert(tag: str) -> dict:
    return {"title": f"Part 12 {tag}", "id": f"p12-{tag}-{uuid.uuid4().hex[:6]}"}


# ── S-109: tokens end with the password ────────────────────────────────────


def test_tokens_compare_in_whole_seconds():
    changed = datetime(2026, 9, 27, 12, 0, 0, 500000, tzinfo=timezone.utc)
    user = SimpleNamespace(password_changed_at=changed)
    second = int(changed.timestamp())
    assert issued_before_password_change(second - 1, user) is True
    # The token minted right after a change, in the same second, still counts.
    assert issued_before_password_change(second, user) is False
    assert issued_before_password_change(None, user) is True
    never = SimpleNamespace(password_changed_at=None)
    assert issued_before_password_change(second - 100, never) is False


async def test_a_password_change_ends_older_tokens_and_keeps_the_changer_signed_in(
    app, client: AsyncClient, auth_headers
):
    admin = await _user(app, "testadmin")
    older = _bearer(_token_issued(admin.id, admin.role, seconds_ago=60))
    assert (await client.get("/auth/me", headers=older)).status_code == 200

    changed = await client.post(
        "/auth/me/password",
        headers=auth_headers,
        json={"current_password": "securepass123", "new_password": "a-new-pass-123"},
    )
    assert changed.status_code == 200, changed.text
    assert (await client.get("/auth/me", headers=older)).status_code == 401
    fresh = _bearer(changed.json()["access_token"])
    assert (await client.get("/auth/me", headers=fresh)).status_code == 200


async def test_an_admin_reset_ends_the_users_older_tokens(
    app, client: AsyncClient, auth_headers
):
    created = await client.post(
        "/auth/users",
        headers=auth_headers,
        json={
            "username": "tempreset",
            "email": "tempreset@test.com",
            "role": "operator",
            "password": "first-pass-123",
        },
    )
    assert created.status_code == 201, created.text
    user_id = created.json()["id"]
    older = _bearer(_token_issued(user_id, "operator", seconds_ago=60))
    assert (await client.get("/auth/me", headers=older)).status_code == 200

    temp = await client.post(
        f"/auth/users/{user_id}/set-temporary-password", headers=auth_headers
    )
    assert temp.status_code == 200, temp.text
    assert (await client.get("/auth/me", headers=older)).status_code == 401


async def test_a_reset_link_ends_the_users_older_tokens(
    app, client: AsyncClient, auth_headers
):
    created = await client.post(
        "/auth/users",
        headers=auth_headers,
        json={
            "username": "linkreset",
            "email": "linkreset@test.com",
            "role": "operator",
            "password": "first-pass-123",
        },
    )
    assert created.status_code == 201, created.text
    user_id = created.json()["id"]
    older = _bearer(_token_issued(user_id, "operator", seconds_ago=60))

    minted = await client.post(
        f"/auth/users/{user_id}/reset-password", headers=auth_headers
    )
    assert minted.status_code in (200, 201), minted.text
    url = urlparse(minted.json()["url"])
    reset_token = (parse_qs(url.query).get("token") or [url.path.rsplit("/", 1)[-1]])[0]
    consumed = await client.post(
        f"/auth/password-reset/{reset_token}", json={"password": "second-pass-123"}
    )
    assert consumed.status_code == 204, consumed.text
    assert (await client.get("/auth/me", headers=older)).status_code == 401


async def test_streams_refuse_older_and_deactivated_tokens(
    app, client: AsyncClient, auth_headers
):
    admin = await _user(app, "testadmin")
    await _stamp_password_change(app, admin.id)
    older = _token_issued(admin.id, admin.role, seconds_ago=60)

    for stream in (
        lambda ws, token: session_stream(ws, uuid.uuid4(), token=token),
        lambda ws, token: notifications_stream(ws, token=token),
    ):
        ws = _FakeWebSocket()
        await stream(ws, older)
        assert ws.closed_code == 1008
        assert not ws.accepted

    # A token from after the change is accepted.
    current = _token_issued(admin.id, admin.role, seconds_ago=0)
    ws = _FakeWebSocket()
    task = asyncio.create_task(notifications_stream(ws, token=current))
    for _ in range(100):
        if ws.accepted:
            break
        await asyncio.sleep(0.02)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert ws.accepted

    # A deactivated user's unexpired token is refused too.
    async with app.state.session_factory() as db:
        user = await UserRepo.get_by_id(db, admin.id)
        user.is_active = False
        await db.commit()
    ws = _FakeWebSocket()
    await notifications_stream(ws, token=current)
    assert ws.closed_code == 1008
    assert not ws.accepted


async def test_an_mfa_challenge_from_before_a_password_change_is_refused(
    app, client: AsyncClient, auth_headers
):
    import pyotp

    setup, _ = await _enable_mfa(client, auth_headers)
    admin = await _user(app, "testadmin")
    await _stamp_password_change(app, admin.id)
    stale = _token_issued(admin.id, admin.role, seconds_ago=60, token_type="mfa")

    resp = await client.post(
        "/auth/mfa/verify",
        json={"mfa_token": stale, "totp_code": pyotp.TOTP(setup["secret"]).now()},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid or expired MFA challenge."


# ── S-108: intake secrets are stored as a hash and shown once ──────────────


def test_a_service_hashes_its_secret_like_ingest_tokens():
    raw = "svc_example-secret"
    assert _intake_secret_columns(raw) == {
        "intake_token_hash": hash_token(raw),
        "intake_token_hint": raw[:8],
    }


async def test_the_intake_url_is_shown_once_and_stored_as_a_hash(
    app, client: AsyncClient, auth_headers
):
    service = await _service(client, auth_headers)
    url = service["intake_url"]
    assert url.startswith("/api/v1/intake/svc_")
    raw = url.rsplit("/", 1)[1]
    assert service["intake_url_hint"] == f"/api/v1/intake/{raw[:8]}…"

    async with app.state.session_factory() as db:
        row = await ServiceRepo.get_by_id(db, TEST_ORG_ID, uuid.UUID(service["id"]))
    assert row.intake_token_hash == hash_token(raw)
    assert raw not in {str(value) for value in vars(row).values()}

    listed = (await client.get("/services", headers=auth_headers)).json()["items"]
    mine = next(item for item in listed if item["id"] == service["id"])
    assert mine["intake_url"] is None
    assert mine["intake_url_hint"] == service["intake_url_hint"]

    posted = await client.post(url, json=_alert("shown-once"))
    assert posted.status_code == 200, posted.text


async def test_only_admins_and_operators_see_the_hint(
    app, client: AsyncClient, auth_headers
):
    service = await _service(client, auth_headers)
    viewer = await _role_headers(client, app, "viewer")
    operator = await _role_headers(client, app, "operator")
    for headers, hint in ((viewer, None), (operator, service["intake_url_hint"])):
        items = (await client.get("/services", headers=headers)).json()["items"]
        mine = next(item for item in items if item["id"] == service["id"])
        assert mine["intake_url"] is None
        assert mine["intake_url_hint"] == hint


async def test_rotating_issues_a_new_url_and_retires_the_old_one(
    app, client: AsyncClient, auth_headers
):
    service = await _service(client, auth_headers)
    old = service["intake_url"]
    bound = generate_token()
    async with app.state.session_factory() as db:
        await IngestTokenRepo.create(
            db,
            TEST_ORG_ID,
            name=f"bound-{uuid.uuid4().hex[:6]}",
            provider="generic",
            token_hash=hash_token(bound),
            service_id=uuid.UUID(service["id"]),
        )
        await db.commit()

    operator = await _role_headers(client, app, "operator")
    refused = await client.post(
        f"/services/{service['id']}/intake-url", headers=operator
    )
    assert refused.status_code == 403

    rotated = await client.post(
        f"/services/{service['id']}/intake-url", headers=auth_headers
    )
    assert rotated.status_code == 200, rotated.text
    new = rotated.json()["intake_url"]
    assert new.startswith("/api/v1/intake/svc_") and new != old
    assert rotated.json()["intake_url_hint"] != service["intake_url_hint"]

    assert (await client.post(old, json=_alert("old"))).status_code == 404
    assert (await client.post(new, json=_alert("new"))).status_code == 200
    # A token bound to the service in another way keeps working.
    via_header = await client.post(
        "/incidents/ingest",
        json=_alert("bound"),
        headers={"X-OpsMender-Token": bound},
    )
    assert via_header.status_code == 200, via_header.text

    # Rotating again works too: each URL's token row gets its own name.
    again = await client.post(
        f"/services/{service['id']}/intake-url", headers=auth_headers
    )
    assert again.status_code == 200, again.text
    assert (await client.post(new, json=_alert("new-old"))).status_code == 404


async def test_revoking_the_urls_token_retires_the_url(
    app, client: AsyncClient, auth_headers
):
    service = await _service(client, auth_headers)
    url = service["intake_url"]
    raw = url.rsplit("/", 1)[1]
    async with app.state.session_factory() as db:
        token = await IngestTokenRepo.get_for_service_token(
            db, TEST_ORG_ID, uuid.UUID(service["id"]), hash_token(raw)
        )
        await IngestTokenRepo.revoke(db, TEST_ORG_ID, token.id)
        await db.commit()
    assert (await client.post(url, json=_alert("revoked"))).status_code == 404


def test_the_access_log_never_prints_an_intake_secret():
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("10.0.0.1:5000", "POST", "/api/v1/intake/svc_SecretSecretSecret", "1.1", 200),
        None,
    )
    assert IntakeSecretFilter().filter(record) is True
    assert record.getMessage() == (
        '10.0.0.1:5000 - "POST /api/v1/intake/svc_Secr… HTTP/1.1" 200'
    )
    assert redact_intake_secrets("GET /health/ready") == "GET /health/ready"

    configure_logging("INFO")
    configure_logging("INFO")
    access = logging.getLogger("uvicorn.access")
    assert sum(isinstance(f, IntakeSecretFilter) for f in access.filters) == 1


# ── S-107: no service, no integrations ─────────────────────────────────────


async def test_a_session_without_a_service_gets_no_integrations(app):
    factory = app.state.session_factory
    assert (
        await _allowed_integration_ids_for_incident(factory, TEST_ORG_ID, None) == set()
    )
    no_service = SimpleNamespace(service_id=None)
    assert (
        await _allowed_integration_ids_for_incident(factory, TEST_ORG_ID, no_service)
        == set()
    )
    deleted = SimpleNamespace(service_id=uuid.uuid4())
    assert (
        await _allowed_integration_ids_for_incident(factory, TEST_ORG_ID, deleted)
        == set()
    )
