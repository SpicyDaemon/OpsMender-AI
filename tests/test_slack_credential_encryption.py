"""M1-113 (S-03): a Slack connector's signing secret and bot token are sealed
at rest, every consumer reads them through one helper that also accepts a
row written before the change, and a value this server's key cannot open is
reported instead of used."""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.routes import bot_connectors as routes
from backend.api.routes.bot_connectors import _connector_config_checks
from backend.api.routes.slack_paging import _find_slack_connector
from backend.api.schemas import BotConnectorUpsert
from backend.auth.secrets import decrypt_secret
from backend.bots.connectors import credentials as sealed
from backend.bots.connectors import get_adapter
from backend.bots.slack_socket import bot_token
from backend.db.models import Base, BotConnector, Organization
from backend.db.repos import BotConnectorRepo

ORG_ID = uuid.UUID("00000000-0000-0000-0000-00000000a113")
SIGNING_SECRET = "unit-signing-secret"
BOT_TOKEN = "xoxb-unit-bot-token"


@pytest.fixture
async def factory(tmp_path, monkeypatch):
    monkeypatch.setenv("OPSMENDER_SECRET_KEY", "m1-113-test-key")
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'slack-credentials.db'}", echo=False
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    make = async_sessionmaker(engine, expire_on_commit=False)
    async with make() as db:
        db.add(Organization(id=ORG_ID, name="Acme", slug="acme-113"))
        await db.commit()
    yield make
    await engine.dispose()


def _body(**overrides) -> BotConnectorUpsert:
    values = dict(
        name="Slack incidents",
        platform="slack",
        config={"default_chat_id": "C113"},
        credentials={"signing_secret": SIGNING_SECRET, "bot_token": BOT_TOKEN},
        allowed_capabilities=["notifications"],
        lanes=["respond"],
        is_enabled=True,
        native_actions_enabled=True,
    )
    values.update(overrides)
    return BotConnectorUpsert(**values)


async def _plain_row(factory, **credentials) -> BotConnector:
    """A row as every Slack connector was stored before M1-113."""
    async with factory() as db:
        connector = await BotConnectorRepo.create(
            db,
            ORG_ID,
            name=f"legacy-{uuid.uuid4().hex[:6]}",
            platform="slack",
            config={"default_chat_id": "C1"},
            credentials=credentials
            or {"signing_secret": SIGNING_SECRET, "bot_token": BOT_TOKEN},
            allowed_capabilities=["notifications"],
            status="configured",
            is_enabled=True,
            native_actions_enabled=True,
        )
        await db.commit()
        return connector


def _memory_connector(**credentials) -> BotConnector:
    return BotConnector(
        name="slack-memory",
        platform="slack",
        config={"default_chat_id": "C1"},
        credentials=credentials,
        allowed_capabilities=["notifications"],
        status="configured",
        is_enabled=True,
        native_actions_enabled=True,
    )


def _signed(body: bytes, secret: str = SIGNING_SECRET) -> dict[str, str]:
    ts = str(int(time.time()))
    digest = hmac.new(
        secret.encode(), f"v0:{ts}:{body.decode()}".encode(), hashlib.sha256
    ).hexdigest()
    return {"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": f"v0={digest}"}


class _Capture:
    """httpx.AsyncClient stand-in that records the Authorization header."""

    headers: dict = {}

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, url, **kwargs):
        _Capture.headers = dict(kwargs.get("headers") or {})

        class _Response:
            status_code = 200

            @staticmethod
            def json():
                return {"ok": True, "channel": "C1", "ts": "1.2"}

        return _Response()


# ---------------------------------------------------------------------------
# Write paths
# ---------------------------------------------------------------------------


async def test_create_and_update_seal_slack_credentials(factory):
    async with factory() as db:
        created = await routes.create_bot_connector(
            _body(), db=db, org_id=ORG_ID, user=None
        )
        stored = await BotConnectorRepo.get_by_id(db, ORG_ID, created.id)
        saved = dict(stored.credentials)

        updated = await routes.update_bot_connector(
            created.id,
            _body(
                credentials={
                    "signing_secret": SIGNING_SECRET,
                    "bot_token": BOT_TOKEN + "2",
                }
            ),
            db=db,
            org_id=ORG_ID,
            user=None,
        )
        stored = await BotConnectorRepo.get_by_id(db, ORG_ID, created.id)
        replaced = dict(stored.credentials)

    for bag in (saved, replaced):
        assert set(bag) == {"signing_secret", "bot_token"}
        assert all(sealed.is_sealed(value) for value in bag.values())
    assert decrypt_secret(saved["signing_secret"][4:]) == SIGNING_SECRET
    assert decrypt_secret(saved["bot_token"][4:]) == BOT_TOKEN
    assert decrypt_secret(replaced["bot_token"][4:]) == BOT_TOKEN + "2"
    # Sealing is randomized, so the same secret never repeats its ciphertext.
    assert saved["signing_secret"] != replaced["signing_secret"]

    for response in (created, updated):
        dumped = response.model_dump_json()
        for secret in (SIGNING_SECRET, BOT_TOKEN, "enc:"):
            assert secret not in dumped
        assert response.credential_keys == ["bot_token", "signing_secret"]


async def test_callback_status_compares_the_secret_not_its_ciphertext(factory):
    async with factory() as db:
        created = await routes.create_bot_connector(
            _body(), db=db, org_id=ORG_ID, user=None
        )
        assert created.callback_status == "configured"
        stored = await BotConnectorRepo.get_by_id(db, ORG_ID, created.id)
        stored.callback_status = "verified"
        await db.commit()

        same = await routes.update_bot_connector(
            created.id, _body(), db=db, org_id=ORG_ID, user=None
        )
        changed = await routes.update_bot_connector(
            created.id,
            _body(credentials={"signing_secret": "another", "bot_token": BOT_TOKEN}),
            db=db,
            org_id=ORG_ID,
            user=None,
        )
    # Re-entering the same secret keeps the verified callback; a new one
    # must be verified again, exactly as before sealing.
    assert same.callback_status == "verified"
    assert changed.callback_status == "configured"


async def test_the_oauth_callback_stores_a_sealed_bot_token():
    merged = sealed.seal_credentials("slack", {"bot_token": BOT_TOKEN})
    assert sealed.is_sealed(merged["bot_token"])
    assert sealed.readable(merged["bot_token"]) == BOT_TOKEN
    # Other OAuth platforms keep their existing storage.
    assert sealed.seal_credentials("discord", {"bot_token": "d"}) == {"bot_token": "d"}


# ---------------------------------------------------------------------------
# Legacy rows
# ---------------------------------------------------------------------------


async def test_startup_seals_legacy_rows_once_and_leaves_other_platforms(factory):
    legacy = await _plain_row(factory)
    async with factory() as db:
        telegram = await BotConnectorRepo.create(
            db,
            ORG_ID,
            name="telegram",
            platform="telegram",
            config={},
            credentials={"bot_token": "tg-plain"},
            allowed_capabilities=["notifications"],
            status="configured",
            is_enabled=True,
        )
        await db.commit()

    assert await sealed.seal_legacy_credentials(factory) == 1
    assert await sealed.seal_legacy_credentials(factory) == 0

    async with factory() as db:
        row = await BotConnectorRepo.get_by_id(db, ORG_ID, legacy.id)
        other = await BotConnectorRepo.get_by_id(db, ORG_ID, telegram.id)
    assert all(sealed.is_sealed(value) for value in row.credentials.values())
    assert decrypt_secret(row.credentials["signing_secret"][4:]) == SIGNING_SECRET
    assert decrypt_secret(row.credentials["bot_token"][4:]) == BOT_TOKEN
    assert other.credentials == {"bot_token": "tg-plain"}


async def test_startup_without_a_key_keeps_rows_readable_and_logs_no_value(
    factory, monkeypatch, caplog
):
    legacy = await _plain_row(factory)

    def no_key(_value: str) -> str:
        raise RuntimeError(
            "Neither OPSMENDER_SECRET_KEY nor OPSMENDER_JWT_SECRET is set."
        )

    monkeypatch.setattr(sealed, "encrypt_secret", no_key)
    with caplog.at_level(logging.WARNING, logger=sealed.__name__):
        assert await sealed.seal_legacy_credentials(factory) == 0

    async with factory() as db:
        row = await BotConnectorRepo.get_by_id(db, ORG_ID, legacy.id)
    assert row.credentials == {"signing_secret": SIGNING_SECRET, "bot_token": BOT_TOKEN}
    assert any("keep plain credentials" in r.getMessage() for r in caplog.records)
    assert all(
        SIGNING_SECRET not in r.getMessage() and BOT_TOKEN not in r.getMessage()
        for r in caplog.records
    )
    # The plain row still serves every consumer meanwhile.
    assert bot_token(row) == BOT_TOKEN


# ---------------------------------------------------------------------------
# Consumers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("stored", ["sealed", "legacy"])
async def test_every_consumer_reads_sealed_and_legacy_values(
    factory, monkeypatch, stored
):
    if stored == "sealed":
        credentials = sealed.seal_credentials(
            "slack", {"signing_secret": SIGNING_SECRET, "bot_token": BOT_TOKEN}
        )
    else:
        credentials = {"signing_secret": SIGNING_SECRET, "bot_token": BOT_TOKEN}
    connector = _memory_connector(**credentials)
    adapter = get_adapter("slack")
    body = b"payload=%7B%7D"

    # Events signature verification and the Socket Mode token.
    adapter.verify_webhook(connector, headers=_signed(body), raw_body=body)
    assert bot_token(connector) == BOT_TOKEN

    # Outbound sends carry the plain token, never the stored form.
    monkeypatch.setattr("backend.bots.connectors.slack.httpx.AsyncClient", _Capture)
    ok, error = await adapter.send_message(connector, chat_id="C1", text="hi")
    assert (ok, error) == (True, None)
    assert _Capture.headers["Authorization"] == f"Bearer {BOT_TOKEN}"
    receipt = await adapter.send_incident_update(connector, chat_id="C1", text="hi")
    assert receipt.ok is True
    assert _Capture.headers["Authorization"] == f"Bearer {BOT_TOKEN}"
    edited = await adapter.update_incident_update(
        connector, chat_id="C1", external_message_id="1.2", text="hi"
    )
    assert edited.ok is True
    assert _Capture.headers["Authorization"] == f"Bearer {BOT_TOKEN}"

    # The interactions and slash-command routes find the connector by secret.
    await _plain_row(factory, **credentials)
    async with factory() as db:
        found = await _find_slack_connector(
            db, signing_secret_must_match=body, headers=_signed(body)
        )
        missed = await _find_slack_connector(
            db, signing_secret_must_match=body, headers=_signed(body, "wrong")
        )
    assert found is not None and missed is None

    # Readiness sees the required keys as present.
    checks = {c.name: c for c in _connector_config_checks(connector)}
    assert checks["credentials"].level == "pass"


async def test_values_sealed_under_another_key_are_reported_not_used(
    factory, monkeypatch
):
    credentials = sealed.seal_credentials(
        "slack", {"signing_secret": SIGNING_SECRET, "bot_token": BOT_TOKEN}
    )
    await _plain_row(factory, **credentials)
    monkeypatch.setenv("OPSMENDER_SECRET_KEY", "a-rotated-key")
    connector = _memory_connector(**credentials)
    adapter = get_adapter("slack")
    body = b"payload=%7B%7D"

    assert sealed.readable(credentials["bot_token"]) is None
    assert bot_token(connector) is None

    with pytest.raises(Exception) as refused:
        adapter.verify_webhook(connector, headers=_signed(body), raw_body=body)
    assert refused.value.status_code == 403
    assert "enter it again" in refused.value.detail

    sent = False

    class _Never(_Capture):
        async def post(self, url, **kwargs):
            nonlocal sent
            sent = True
            return await super().post(url, **kwargs)

    monkeypatch.setattr("backend.bots.connectors.slack.httpx.AsyncClient", _Never)
    ok, error = await adapter.send_message(connector, chat_id="C1", text="hi")
    assert ok is False and "enter it again" in error
    receipt = await adapter.send_incident_update(connector, chat_id="C1", text="hi")
    assert receipt.ok is False and "enter it again" in receipt.error
    edited = await adapter.update_incident_update(
        connector, chat_id="C1", external_message_id="1.2", text="hi"
    )
    assert edited.ok is False and "enter it again" in edited.error
    assert sent is False

    async with factory() as db:
        assert (
            await _find_slack_connector(
                db, signing_secret_must_match=body, headers=_signed(body)
            )
            is None
        )

    checks = {c.name: c for c in _connector_config_checks(connector)}
    assert checks["credentials"].level == "fail"
    assert "enter it again" in checks["credentials"].detail
    for value in (SIGNING_SECRET, BOT_TOKEN):
        assert value not in checks["credentials"].detail
