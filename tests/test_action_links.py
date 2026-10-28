"""Signed action links for chat cards (M1-99).

A link opens a confirm page; it never acts on open. The action runs once, on
POST, under the signed-in person's role; used, expired, tampered and other-org
links are refused, a refusal leaves the link usable, and the audit log records
every attempt.
"""

from __future__ import annotations

import dataclasses
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.app import create_app
from backend.api.deps import get_db, set_session_factory
from backend.api.auth import hash_password
from backend.bots import action_links
from backend.bots.action_links import KEY_PREFIX, LINK_PATH, action_link, link_secret
from backend.bots.actions import (
    IncidentActionError,
    make_incident_action_token,
    verify_incident_action_token,
)
from backend.config_loader import set_env_path
from backend.db.models import (
    AuditEntry,
    Base,
    IncidentComment,
    NativeActionInvocation,
    Organization,
)
from backend.db.repos import (
    BotConnectorRepo,
    IncidentAssignmentRepo,
    IncidentRepo,
    UserRepo,
)

TEST_ORG_ID = uuid.UUID("00000000-0000-0000-0000-0000000000a9")
OTHER_ORG_ID = uuid.UUID("00000000-0000-0000-0000-0000000000b9")
PASSWORD = "securepass123"


@pytest.fixture
async def app(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'action-links.db'}"
    engine = create_async_engine(database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(Organization(id=TEST_ORG_ID, name="Links Org", slug="links-org"))
        session.add(Organization(id=OTHER_ORG_ID, name="Other Org", slug="other-org"))
        await session.commit()
    set_session_factory(factory)

    env_file = tmp_path / ".env"
    env_file.write_text(
        "OPSMENDER_TIER=2\n"
        "OPSMENDER_LOG_LEVEL=INFO\n"
        "OPSMENDER_AUDIT_LOG=./logs/audit.jsonl\n"
        "OPSMENDER_JWT_SECRET=test-secret\n"
        f"OPSMENDER_DATABASE_URL={database_url}\n"
        "OPSMENDER_MCP_SERVERS_JSON=[]\n"
    )
    set_env_path(env_file)

    application = create_app()
    application.state.session_factory = factory

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
    ) as http:
        yield http


@pytest.fixture
def notified(monkeypatch):
    """The incident events sent to channels after an action."""
    calls = []

    async def _notify(db, incident_id, org_id, event_type):
        calls.append((incident_id, event_type))

    monkeypatch.setattr("backend.api.routes.incidents._notify_channels", _notify)
    return calls


async def _user(app, name: str, *, role: str = "operator") -> uuid.UUID:
    async with app.state.session_factory() as db:
        user = await UserRepo.create(
            db,
            username=name,
            email=f"{name}@test.com",
            password_hash=hash_password(PASSWORD),
            role=role,
            primary_org_id=TEST_ORG_ID,
        )
        await UserRepo.add_to_organization(
            db, user_id=user.id, org_id=TEST_ORG_ID, role=role
        )
        await db.commit()
        return user.id


async def _headers(client, name: str) -> dict[str, str]:
    resp = await client.post(
        "/auth/login", json={"username": name, "password": PASSWORD}
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _setup(app, *, enabled: bool = True):
    async with app.state.session_factory() as db:
        connector = await BotConnectorRepo.create(
            db,
            TEST_ORG_ID,
            name="Ops channel",
            platform="mattermost",
            allowed_capabilities=["incident_updates"],
            status="connected",
            is_enabled=enabled,
        )
        incident = await IncidentRepo.create(
            db,
            TEST_ORG_ID,
            title="Checkout errors",
            description="500s",
            severity="high",
        )
        await db.commit()
        return connector, incident


def _token(link: str) -> str:
    assert "#token=" in link
    return link.split("#token=", 1)[1]


def _link(app, connector, incident_id, action="acknowledge", **kwargs) -> str:
    return _token(
        action_link(
            app.state.config,
            connector=connector,
            incident_id=incident_id,
            action=action,
            **kwargs,
        )
    )


async def _rows(app, model):
    async with app.state.session_factory() as db:
        return list((await db.execute(select(model))).scalars())


async def _owner(app, incident_id):
    async with app.state.session_factory() as db:
        active = await IncidentAssignmentRepo.get_active(db, TEST_ORG_ID, incident_id)
        return active.assigned_to if active is not None else None


async def _status(app, incident_id) -> str:
    async with app.state.session_factory() as db:
        return (await IncidentRepo.get_by_id(db, TEST_ORG_ID, incident_id)).status


# -- The link ---------------------------------------------------------------


async def test_link_opens_the_confirm_page_with_the_token_in_the_fragment(app):
    connector, incident = await _setup(app)
    link = action_link(
        app.state.config,
        connector=connector,
        incident_id=incident.id,
        action="resolve",
        base_url="https://ops.example.com/",
    )
    assert link.startswith(f"https://ops.example.com{LINK_PATH}#token=")
    claims = verify_incident_action_token(
        _token(link), secret=link_secret(app.state.config)
    )
    assert claims.org_id == TEST_ORG_ID
    assert claims.incident_id == incident.id
    assert claims.action == "resolve"
    assert claims.connector_id == connector.id
    # Signed with a key of its own: the JWT secret itself does not verify it.
    with pytest.raises(IncidentActionError, match="invalid_action_token"):
        verify_incident_action_token(_token(link), secret="test-secret")


async def test_link_lifetime_comes_from_the_setting_within_bounds(app):
    connector, incident = await _setup(app)
    now = datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc)
    secret = link_secret(app.state.config)
    for minutes, expected in ((1440, 1440), (60, 60), (1, 5), (999999, 7 * 24 * 60)):
        app.state.config.auth.action_link_ttl_minutes = minutes
        claims = verify_incident_action_token(
            _link(app, connector, incident.id, now=now), secret=secret, now=now
        )
        assert claims.expires_at - now == timedelta(minutes=expected)


async def test_the_link_key_comes_from_the_jwt_secret(app, client):
    """Rotating OPSMENDER_JWT_SECRET ends every link."""
    await _user(app, "olive")
    headers = await _headers(client, "olive")
    connector, incident = await _setup(app)
    token = _link(app, connector, incident.id)
    config = app.state.config
    before = link_secret(config)
    config.auth.jwt_secret = "a-rotated-secret"
    try:
        assert link_secret(config) != before
        with pytest.raises(IncidentActionError, match="invalid_action_token"):
            verify_incident_action_token(token, secret=link_secret(config))
        preview = await client.post(
            "/action-links/preview", headers=headers, json={"token": token}
        )
        assert preview.json()["state"] == "invalid"
    finally:
        config.auth.jwt_secret = "test-secret"


async def test_each_link_has_its_own_nonce(app):
    connector, incident = await _setup(app)
    secret = link_secret(app.state.config)
    first = verify_incident_action_token(
        _link(app, connector, incident.id), secret=secret
    )
    second = verify_incident_action_token(
        _link(app, connector, incident.id), secret=secret
    )
    assert first.nonce != second.nonce


# -- Preview never acts -------------------------------------------------------


async def test_preview_needs_a_session(app, client):
    connector, incident = await _setup(app)
    resp = await client.post(
        "/action-links/preview", json={"token": _link(app, connector, incident.id)}
    )
    assert resp.status_code == 401


async def test_preview_changes_nothing(app, client):
    await _user(app, "olive")
    headers = await _headers(client, "olive")
    connector, incident = await _setup(app)
    token = _link(app, connector, incident.id)
    for _ in range(2):
        resp = await client.post(
            "/action-links/preview", headers=headers, json={"token": token}
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["state"] == "ready"
        assert body["action"] == "acknowledge"
        assert body["action_label"] == "Acknowledge"
        assert body["incident_id"] == str(incident.id)
        assert body["incident_title"] == "Checkout errors"
        assert body["channel_name"] == "Ops channel"
        assert body["can_act"] is True
    assert await _owner(app, incident.id) is None
    assert await _status(app, incident.id) == "open"
    assert await _rows(app, NativeActionInvocation) == []
    assert await _rows(app, AuditEntry) == []


# -- Confirm acts once ----------------------------------------------------------


async def test_confirm_acknowledges_once_and_records_it(app, client, notified):
    olive = await _user(app, "olive")
    headers = await _headers(client, "olive")
    connector, incident = await _setup(app)
    token = _link(app, connector, incident.id)

    resp = await client.post(
        "/action-links/confirm", headers=headers, json={"token": token}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "acknowledged"
    assert resp.json()["message"] == "You acknowledged the incident."
    assert resp.json()["changed"] is True
    assert await _owner(app, incident.id) == olive
    assert notified == [(incident.id, "incident.acknowledged")]

    uses = await _rows(app, NativeActionInvocation)
    assert len(uses) == 1
    nonce = verify_incident_action_token(
        token, secret=link_secret(app.state.config)
    ).nonce
    assert uses[0].idempotency_key == KEY_PREFIX + nonce
    assert uses[0].connector_id == connector.id
    assert uses[0].status == "applied"
    assert uses[0].actor_user_id == olive
    assert uses[0].result_status == "acknowledged"

    comments = await _rows(app, IncidentComment)
    assert any("(via link)" in comment.body for comment in comments)

    entries = await _rows(app, AuditEntry)
    assert len(entries) == 1
    assert entries[0].entry_type == "action_link"
    assert entries[0].tool_name == "acknowledge"
    assert entries[0].permitted is True
    assert entries[0].result == {"status": "acknowledged"}
    assert entries[0].tool_parameters == {
        "incident_id": str(incident.id),
        "channel_id": str(connector.id),
        "user_id": str(olive),
    }


async def test_second_use_is_refused_and_changes_nothing(app, client, notified):
    olive = await _user(app, "olive")
    await _user(app, "omar")
    connector, incident = await _setup(app)
    token = _link(app, connector, incident.id)
    first = await client.post(
        "/action-links/confirm",
        headers=await _headers(client, "olive"),
        json={"token": token},
    )
    assert first.status_code == 200

    omar = await _headers(client, "omar")
    again = await client.post(
        "/action-links/confirm", headers=omar, json={"token": token}
    )
    assert again.status_code == 409
    assert again.json()["detail"] == "This link was already used."
    assert await _owner(app, incident.id) == olive
    assert len(await _rows(app, NativeActionInvocation)) == 1
    assert len(notified) == 1

    preview = await client.post(
        "/action-links/preview", headers=omar, json={"token": token}
    )
    assert preview.json()["state"] == "used"
    assert preview.json()["used_at"] is not None
    assert preview.json()["used_result"] == "acknowledged"
    assert preview.json()["can_act"] is False

    refused = [entry for entry in await _rows(app, AuditEntry) if not entry.permitted]
    assert [entry.block_reason for entry in refused] == ["link_used"]


async def test_resolve_through_a_link_refreshes_the_cards(app, client, notified):
    await _user(app, "olive")
    headers = await _headers(client, "olive")
    connector, incident = await _setup(app)
    resp = await client.post(
        "/action-links/confirm",
        headers=headers,
        json={"token": _link(app, connector, incident.id, action="resolve")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "resolved"
    assert await _status(app, incident.id) == "resolved"
    assert notified == [(incident.id, "incident.resolved")]


async def test_a_use_that_lost_the_race_is_refused(app, monkeypatch):
    """Two confirms can both find a link ready; the use key lets one through."""
    olive = await _user(app, "olive")
    omar = await _user(app, "omar")
    connector, incident = await _setup(app)
    token = _link(app, connector, incident.id)
    config = app.state.config
    async with app.state.session_factory() as db:
        await action_links.use_link(
            db, config, token, org_id=TEST_ORG_ID, user_id=olive
        )
        await db.commit()
    real_read = action_links.read_link

    async def _stale_read(*args, **kwargs):
        link = await real_read(*args, **kwargs)
        return dataclasses.replace(link, state="ready", use=None)

    monkeypatch.setattr(action_links, "read_link", _stale_read)
    async with app.state.session_factory() as db:
        with pytest.raises(IncidentActionError, match="link_used"):
            await action_links.use_link(
                db, config, token, org_id=TEST_ORG_ID, user_id=omar
            )
    assert await _owner(app, incident.id) == olive
    assert len(await _rows(app, NativeActionInvocation)) == 1


async def test_escalate_through_a_link_reaches_the_escalation_engine(app, client):
    """Escalate needs the channel factory; with no chain it changes nothing."""
    await _user(app, "olive")
    headers = await _headers(client, "olive")
    connector, incident = await _setup(app)
    resp = await client.post(
        "/action-links/confirm",
        headers=headers,
        json={"token": _link(app, connector, incident.id, action="escalate")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "no_escalation_target"
    assert resp.json()["changed"] is False
    assert "Nothing to escalate" in resp.json()["message"]


async def test_escalate_that_runs_out_the_chain_is_a_change(app, client):
    from backend.db.repos import (
        EscalationChainRepo,
        IncidentChainStateRepo,
        TeamRepo,
    )

    await _user(app, "olive")
    headers = await _headers(client, "olive")
    connector, incident = await _setup(app)
    async with app.state.session_factory() as db:
        team = await TeamRepo.create(db, TEST_ORG_ID, name="t-link", slug="t-link")
        chain = await EscalationChainRepo.create(
            db, TEST_ORG_ID, team_id=team.id, name="c-link", description=None
        )
        await IncidentChainStateRepo.create(
            db, TEST_ORG_ID, incident_id=incident.id, chain_id=chain.id
        )
        await db.commit()
    resp = await client.post(
        "/action-links/confirm",
        headers=headers,
        json={"token": _link(app, connector, incident.id, action="escalate")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "chain_exhausted"
    assert resp.json()["changed"] is True
    async with app.state.session_factory() as db:
        state = await IncidentChainStateRepo.get_for_incident(
            db, TEST_ORG_ID, incident.id
        )
        assert state.status == "exhausted"


async def test_start_ai_session_through_a_link_dispatches_the_session(
    app, client, monkeypatch
):
    olive = await _user(app, "olive")
    headers = await _headers(client, "olive")
    connector, incident = await _setup(app)
    async with app.state.session_factory() as db:
        # The incident's owner may start its AI session.
        await IncidentAssignmentRepo.assign(
            db, TEST_ORG_ID, incident_id=incident.id, user_id=olive
        )
        await db.commit()
    dispatched = []

    async def dispatch(app_, db, *, org_id, result):
        dispatched.append(result.session_id)

    monkeypatch.setattr(
        "backend.api.routes.action_links.dispatch_native_session_result", dispatch
    )
    resp = await client.post(
        "/action-links/confirm",
        headers=headers,
        json={"token": _link(app, connector, incident.id, action="start_ai_session")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] in {"session_started", "session_queued"}
    assert body["changed"] is True
    assert dispatched == [uuid.UUID(body["session_id"])]


async def test_a_refused_ai_session_is_audited_and_leaves_the_link_usable(
    app, client, monkeypatch
):
    olive = await _user(app, "olive")
    headers = await _headers(client, "olive")
    connector, incident = await _setup(app)
    async with app.state.session_factory() as db:
        await IncidentAssignmentRepo.assign(
            db, TEST_ORG_ID, incident_id=incident.id, user_id=olive
        )
        await db.commit()

    async def full(*args, **kwargs):
        raise ValueError("All configured incident-response models are at capacity")

    monkeypatch.setattr("backend.bots.actions.admit_session", full)
    token = _link(app, connector, incident.id, action="start_ai_session")
    resp = await client.post(
        "/action-links/confirm", headers=headers, json={"token": token}
    )
    assert resp.status_code == 409
    assert resp.json()["detail"] == (
        "The AI session did not start: All configured incident-response models "
        "are at capacity"
    )
    assert await _rows(app, NativeActionInvocation) == []
    [entry] = await _rows(app, AuditEntry)
    assert entry.permitted is False and entry.block_reason == "session_not_admitted"
    preview = await client.post(
        "/action-links/preview", headers=headers, json={"token": token}
    )
    assert preview.json()["state"] == "ready"


# -- Refusals -------------------------------------------------------------------


async def test_viewer_is_refused_and_the_link_stays_usable(app, client):
    await _user(app, "vera", role="viewer")
    olive = await _user(app, "olive")
    connector, incident = await _setup(app)
    token = _link(app, connector, incident.id)

    vera = await _headers(client, "vera")
    preview = await client.post(
        "/action-links/preview", headers=vera, json={"token": token}
    )
    assert preview.json()["state"] == "ready"
    assert preview.json()["can_act"] is False
    refused = await client.post(
        "/action-links/confirm", headers=vera, json={"token": token}
    )
    assert refused.status_code == 403
    assert await _owner(app, incident.id) is None
    assert await _rows(app, NativeActionInvocation) == []

    done = await client.post(
        "/action-links/confirm",
        headers=await _headers(client, "olive"),
        json={"token": token},
    )
    assert done.status_code == 200, done.text
    assert await _owner(app, incident.id) == olive
    entries = await _rows(app, AuditEntry)
    assert sorted((entry.permitted, entry.block_reason or "") for entry in entries) == [
        (False, "actor_not_authorized"),
        (True, ""),
    ]


async def test_a_refused_action_leaves_no_use(app, client, monkeypatch):
    """The use and the action share one savepoint: when the action refuses
    after the use is written, both roll back."""
    await _user(app, "olive")
    headers = await _headers(client, "olive")
    connector, incident = await _setup(app)
    token = _link(app, connector, incident.id)

    async def _refuse(*args, **kwargs):
        raise IncidentActionError("actor_not_on_team")

    monkeypatch.setattr(action_links, "execute_incident_action", _refuse)
    resp = await client.post(
        "/action-links/confirm", headers=headers, json={"token": token}
    )
    assert resp.status_code == 403
    assert "team" in resp.json()["detail"]
    assert await _rows(app, NativeActionInvocation) == []
    monkeypatch.undo()

    preview = await client.post(
        "/action-links/preview", headers=headers, json={"token": token}
    )
    assert preview.json()["state"] == "ready"


async def test_narrowed_api_token_acts_with_its_own_role(app, client):
    await _user(app, "ada", role="admin")
    admin = await _headers(client, "ada")
    created = await client.post(
        "/api/v1/api-tokens",
        headers=admin,
        json={"name": "viewer-script", "role": "viewer"},
    )
    assert created.status_code == 201, created.text
    connector, incident = await _setup(app)
    resp = await client.post(
        "/action-links/confirm",
        headers={"Authorization": f"Bearer {created.json()['token']}"},
        json={"token": _link(app, connector, incident.id)},
    )
    assert resp.status_code == 403
    assert await _owner(app, incident.id) is None
    assert await _rows(app, NativeActionInvocation) == []


async def test_expired_link_is_refused_but_names_its_incident(app, client):
    await _user(app, "olive")
    headers = await _headers(client, "olive")
    connector, incident = await _setup(app)
    token = _link(
        app,
        connector,
        incident.id,
        now=datetime.now(timezone.utc) - timedelta(days=2),
    )
    preview = await client.post(
        "/action-links/preview", headers=headers, json={"token": token}
    )
    assert preview.json()["state"] == "expired"
    assert preview.json()["incident_id"] == str(incident.id)
    assert preview.json()["can_act"] is False

    resp = await client.post(
        "/action-links/confirm", headers=headers, json={"token": token}
    )
    assert resp.status_code == 410
    assert (
        resp.json()["detail"]
        == "This link has expired. Open the incident to act there."
    )
    assert await _owner(app, incident.id) is None
    assert await _rows(app, NativeActionInvocation) == []


async def test_turned_off_channel_stops_its_links(app, client):
    await _user(app, "olive")
    headers = await _headers(client, "olive")
    connector, incident = await _setup(app, enabled=False)
    token = _link(app, connector, incident.id)
    preview = await client.post(
        "/action-links/preview", headers=headers, json={"token": token}
    )
    assert preview.json()["state"] == "channel_off"
    resp = await client.post(
        "/action-links/confirm", headers=headers, json={"token": token}
    )
    assert resp.status_code == 409
    assert await _owner(app, incident.id) is None


async def test_tampered_foreign_and_unlinked_tokens_are_invalid(app, client):
    await _user(app, "olive")
    headers = await _headers(client, "olive")
    connector, incident = await _setup(app)
    good = _link(app, connector, incident.id)
    secret = link_secret(app.state.config)
    body, signature = good.split(".", 1)
    forged = [
        good + "x",
        f"{body}.{signature[::-1]}",
        "not-a-token",
        # Signed with the JWT secret instead of the link key.
        make_incident_action_token(
            secret="test-secret",
            org_id=TEST_ORG_ID,
            incident_id=incident.id,
            action="acknowledge",
            connector_id=connector.id,
        ),
        # Another org's link.
        make_incident_action_token(
            secret=secret,
            org_id=OTHER_ORG_ID,
            incident_id=incident.id,
            action="acknowledge",
            connector_id=connector.id,
        ),
        # A chat button's token names no channel; it is not a link.
        make_incident_action_token(
            secret=secret,
            org_id=TEST_ORG_ID,
            incident_id=incident.id,
            action="acknowledge",
        ),
        # A channel that does not exist.
        make_incident_action_token(
            secret=secret,
            org_id=TEST_ORG_ID,
            incident_id=incident.id,
            action="acknowledge",
            connector_id=uuid.uuid4(),
        ),
    ]
    for token in forged:
        preview = await client.post(
            "/action-links/preview", headers=headers, json={"token": token}
        )
        assert preview.status_code == 200, token
        assert preview.json() == {
            **preview.json(),
            "state": "invalid",
            "message": "This link is not valid.",
            "incident_id": None,
            "incident_title": None,
            "can_act": False,
        }, token
        resp = await client.post(
            "/action-links/confirm", headers=headers, json={"token": token}
        )
        assert resp.status_code == 400, token
        assert resp.json()["detail"] == "This link is not valid."
    assert await _owner(app, incident.id) is None
    assert await _rows(app, NativeActionInvocation) == []
    entries = await _rows(app, AuditEntry)
    assert len(entries) == len(forged)
    assert {(entry.permitted, entry.block_reason) for entry in entries} == {
        (False, "invalid_action_token")
    }
