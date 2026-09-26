"""Sign-in protection (X2, KI-014) and ingest-token hygiene (KI-043, KI-044)."""

from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from backend.api.auth import create_mfa_token
from backend.auth.signin_throttle import SignInThrottle
from backend.db.models import AuditEntry, IngestToken
from backend.db.repos import IngestLogRepo, UserRepo
from tests.test_ingest import (
    TEST_ORG_ID,
    _create_paged_service,
    admin_headers as _admin_headers_fixture,
    app as _app_fixture,
    client as _client_fixture,
)

app = pytest.fixture(_app_fixture.__wrapped__)
client = pytest.fixture(_client_fixture.__wrapped__)
admin_headers = pytest.fixture(_admin_headers_fixture.__wrapped__)


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _small_throttle(app, *, account=3, address=6) -> _Clock:
    clock = _Clock()
    app.state.signin_throttle = SignInThrottle(
        account_limit=account, address_limit=address, window_seconds=600, clock=clock
    )
    return clock


def _client_from(app, address: str) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app, client=(address, 4321)),
        base_url="http://test",
    )


async def _user(app, username="alice", password="right-horse-battery"):
    from backend.api.auth import hash_password

    async with app.state.session_factory() as db:
        user = await UserRepo.create(
            db,
            username=username,
            email=f"{username}@example.test",
            password_hash=hash_password(password),
            role="operator",
            primary_org_id=TEST_ORG_ID,
        )
        await db.commit()
        return user


async def _login(c: AsyncClient, username: str, password: str):
    return await c.post(
        "/auth/login", json={"username": username, "password": password}
    )


async def _lockout_audits(app) -> list[AuditEntry]:
    async with app.state.session_factory() as db:
        rows = await db.execute(
            select(AuditEntry).where(AuditEntry.entry_type == "sign_in_lockout")
        )
        return list(rows.scalars())


# ── T08: the throttle itself ──────────────────────────────────────────────


def test_t08_window_expires_and_zero_disables():
    clock = _Clock()
    throttle = SignInThrottle(
        account_limit=2, address_limit=0, window_seconds=60, clock=clock
    )
    for _ in range(2):
        throttle.record_failure(address="a", account="u")
    assert throttle.retry_after(address="a", account="u") == pytest.approx(60)
    assert throttle.retry_after(address="b", account="u") is None  # other address
    clock.now += 61
    assert throttle.retry_after(address="a", account="u") is None
    for _ in range(50):  # address limit 0: never blocked by address
        throttle.record_failure(address="a")
    assert throttle.retry_after(address="a") is None
    off = SignInThrottle(account_limit=0, address_limit=0)
    for _ in range(50):
        off.record_failure(address="a", account="u")
    assert off.retry_after(address="a", account="u") is None


# ── T01/T02/T07: login ────────────────────────────────────────────────────


async def test_t01_t07_login_locks_after_failures_even_for_the_right_password(
    app, client
):
    _small_throttle(app)
    await _user(app)
    for _ in range(3):
        assert (await _login(client, "alice", "wrong")).status_code == 401
    blocked = await _login(client, "alice", "right-horse-battery")
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) > 0
    assert "Too many failed attempts" in blocked.json()["detail"]
    # Signing in by email is the same account.
    assert (
        await _login(client, "alice@example.test", "right-horse-battery")
    ).status_code == 429
    # Another address is unaffected.
    async with _client_from(app, "10.0.0.2") as other:
        assert (await _login(other, "alice", "right-horse-battery")).status_code == 200
    # One audit entry for the lockout, not one per blocked attempt, no secret.
    [audit] = await _lockout_audits(app)
    assert audit.tool_parameters["endpoint"] == "login"
    assert audit.tool_parameters["scope"] == "account"
    assert "right-horse-battery" not in str(audit.tool_parameters)
    assert "wrong" not in str(audit.tool_parameters)


async def test_t01_a_success_clears_the_count(app, client):
    _small_throttle(app)
    await _user(app)
    for _ in range(2):
        await _login(client, "alice", "wrong")
    assert (await _login(client, "alice", "right-horse-battery")).status_code == 200
    for _ in range(2):
        assert (await _login(client, "alice", "wrong")).status_code == 401
    assert (await _login(client, "alice", "right-horse-battery")).status_code == 200


async def test_t02_unknown_accounts_behave_the_same(app, client):
    _small_throttle(app)
    for _ in range(3):
        assert (await _login(client, "nobody", "x")).status_code == 401
    assert (await _login(client, "nobody", "x")).status_code == 429


async def test_t03_the_address_limit_stops_spraying(app, client):
    _small_throttle(app, account=10, address=4)
    for index in range(4):
        assert (await _login(client, f"user{index}", "x")).status_code == 401
    assert (await _login(client, "user99", "x")).status_code == 429
    audits = await _lockout_audits(app)
    assert [a.tool_parameters["scope"] for a in audits] == ["address"]


async def test_t08_the_lock_lifts_when_the_window_passes(app, client):
    clock = _small_throttle(app)
    await _user(app)
    for _ in range(3):
        await _login(client, "alice", "wrong")
    assert (await _login(client, "alice", "right-horse-battery")).status_code == 429
    clock.now += 601
    assert (await _login(client, "alice", "right-horse-battery")).status_code == 200


# ── T04: MFA verify ───────────────────────────────────────────────────────


async def test_t04_mfa_verify_is_limited_per_user_and_address(app, client):
    _small_throttle(app)
    user = await _user(app)
    challenge = create_mfa_token(user.id, user.role)
    body = {"mfa_token": challenge, "totp_code": "000000"}
    for _ in range(3):
        assert (await client.post("/auth/mfa/verify", json=body)).status_code == 401
    assert (await client.post("/auth/mfa/verify", json=body)).status_code == 429


# ── T05: register, reset consume, invite accept ───────────────────────────


async def test_t05_register_conflicts_are_limited_per_address(app, client):
    _small_throttle(app, address=3)
    await _user(app)
    body = {
        "username": "alice",
        "email": "a2@example.test",
        "password": "longpassword1",
    }
    for _ in range(3):
        assert (await client.post("/auth/register", json=body)).status_code == 409
    assert (await client.post("/auth/register", json=body)).status_code == 429


async def test_t05_reset_and_invite_tokens_are_limited_per_address(app, client):
    _small_throttle(app, address=3)
    for index in range(2):
        resp = await client.post(
            f"/auth/password-reset/bogus-{index}", json={"password": "longpassword1"}
        )
        assert resp.status_code == 400
    invite = await client.post(
        "/invites/bogus/accept",
        json={"username": "newbie", "password": "longpassword1"},
    )
    assert invite.status_code == 400
    blocked = await client.post(
        "/auth/password-reset/bogus-9", json={"password": "longpassword1"}
    )
    assert blocked.status_code == 429


# ── T06: SSO callbacks refuse a blocked address first ─────────────────────


async def test_t06_sso_callbacks_refuse_a_blocked_address_before_idp_work(app, client):
    _small_throttle(app, address=2)
    for _ in range(2):
        assert (await client.get("/auth/sso/acme/callback")).status_code == 400
    assert (
        await client.get("/auth/sso/acme/callback?code=c&state=s")
    ).status_code == 429
    assert (
        await client.post("/auth/saml/acme/acs", data={"SAMLResponse": "x"})
    ).status_code == 429
    async with _client_from(app, "10.0.0.3") as other:
        assert (await other.get("/auth/sso/acme/callback")).status_code == 400


# ── T09: KI-043 ───────────────────────────────────────────────────────────


async def test_t09_a_token_with_deliveries_is_revoked_not_deleted(
    app, client, admin_headers
):
    created = await client.post(
        "/ingest-tokens",
        json={"name": "t09", "provider": "generic"},
        headers=admin_headers,
    )
    assert created.status_code == 201, created.text
    used_id = created.json()["id"]
    async with app.state.session_factory() as db:
        await IngestLogRepo.create(
            db,
            TEST_ORG_ID,
            ingest_token_id=uuid.UUID(used_id),
            provider="generic",
            raw_payload={"title": "x"},
            dedup_action="created",
        )
        await db.commit()
    refused = await client.delete(f"/ingest-tokens/{used_id}", headers=admin_headers)
    assert refused.status_code == 409
    assert "revoke" in refused.json()["detail"].lower()
    revoked = await client.post(
        f"/ingest-tokens/{used_id}/revoke", headers=admin_headers
    )
    assert revoked.status_code in (200, 204)

    fresh = await client.post(
        "/ingest-tokens",
        json={"name": "t09b", "provider": "generic"},
        headers=admin_headers,
    )
    deleted = await client.delete(
        f"/ingest-tokens/{fresh.json()['id']}", headers=admin_headers
    )
    assert deleted.status_code == 204


# ── T10: KI-044 ───────────────────────────────────────────────────────────


async def test_t10_deleting_a_service_revokes_its_intake_url(
    app, client, admin_headers
):
    service = await _create_paged_service(client, app, admin_headers, name="Gone")
    before = await client.post(
        service["intake_url"], json={"title": "still here", "id": "g1"}
    )
    assert before.status_code == 200
    async with app.state.session_factory() as db:
        token_ids = (
            (
                await db.execute(
                    select(IngestToken.id).where(
                        IngestToken.service_id == uuid.UUID(service["id"])
                    )
                )
            )
            .scalars()
            .all()
        )
    assert token_ids

    gone = await client.delete(f"/services/{service['id']}", headers=admin_headers)
    assert gone.status_code == 204

    after = await client.post(
        service["intake_url"], json={"title": "after delete", "id": "g2"}
    )
    assert after.status_code in (401, 403, 404)  # before KI-044: 200, unscoped incident
    async with app.state.session_factory() as db:
        rows = (
            (await db.execute(select(IngestToken).where(IngestToken.id.in_(token_ids))))
            .scalars()
            .all()
        )
    assert rows and not any(row.is_active for row in rows)
