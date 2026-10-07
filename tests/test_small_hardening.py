"""M1-43 (R-19, R-20, R-25): SAML sign-in refuses a deactivated account
before minting a token; WeCom and Weixin updates are verified before they are
opened and a malformed body is a 400, not a 500; the Inbox and its settings
refuse API tokens like every other self-service route."""

from __future__ import annotations

import base64
import hashlib
import os
import struct
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from httpx import AsyncClient

from backend.api.auth import hash_password
from backend.bots.connectors.wecom import WeComAdapter
from backend.db.repos import BotConnectorRepo, UserRepo
from tests.test_saml import (
    SAMPLE_IDP_METADATA,
    TEST_ORG_ID,
    app as _app_fixture,
    auth_headers as _auth_headers_fixture,
    client as _client_fixture,
)

app = pytest.fixture(_app_fixture.__wrapped__)
client = pytest.fixture(_client_fixture.__wrapped__)
auth_headers = pytest.fixture(_auth_headers_fixture.__wrapped__)

DEACTIVATED = "This account is deactivated. Ask an admin to reactivate it."
MESSAGE = (
    "<xml><ToUserName>bot</ToUserName><FromUserName>someone</FromUserName>"
    "<CreateTime>1348831860</CreateTime><MsgType>text</MsgType>"
    "<Content>/help</Content><MsgId>1</MsgId></xml>"
)


# ── R-19: SAML refuses a deactivated account at sign-in ────────────────────


async def test_saml_refuses_a_deactivated_account_at_sign_in(
    client: AsyncClient, app, auth_headers, monkeypatch
):
    monkeypatch.setenv("OPSMENDER_SAML_SP_CERT", "cert")
    monkeypatch.setenv("OPSMENDER_SAML_SP_KEY", "key")
    saved = await client.put(
        f"/organizations/{TEST_ORG_ID}/saml",
        json={"idp_metadata_xml": SAMPLE_IDP_METADATA, "default_role": "operator"},
        headers=auth_headers,
    )
    assert saved.status_code == 200, saved.text
    async with app.state.session_factory() as db:
        user = await UserRepo.create(
            db,
            username="gone",
            email="gone@acme.com",
            password_hash=hash_password("gone-password-1"),
            role="operator",
            primary_org_id=TEST_ORG_ID,
        )
        user.is_active = False
        await db.commit()
    from backend.api.routes import saml as saml_route

    async def _metadata(_config):
        return {"entityId": "https://idp.example.com/saml"}

    def _assertion(**_kwargs):
        return (
            {"email": ["gone@acme.com"], "name": ["Gone"]},
            "name-id",
            "assertion-deactivated",
            datetime.now(timezone.utc) + timedelta(minutes=5),
        )

    monkeypatch.setattr(saml_route, "fetch_idp_metadata", _metadata)
    monkeypatch.setattr(saml_route, "build_settings", lambda **_kwargs: object())
    monkeypatch.setattr(saml_route, "process_acs", _assertion)

    resp = await client.post(
        "/auth/saml/saml-org/acs",
        data={"SAMLResponse": "stub"},
        follow_redirects=False,
    )

    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == DEACTIVATED
    assert "sso_token" not in resp.headers.get("location", "")
    async with app.state.session_factory() as db:
        user = await UserRepo.get_by_email(db, "gone@acme.com")
    # Nothing about the account changed.
    assert (user.is_active, user.auth_source) == (False, "local")


# ── R-20: WeCom and Weixin are verified first ──────────────────────────────

AES_KEY = base64.b64encode(os.urandom(32)).decode().rstrip("=")


def _wecom_encrypt(xml: str) -> str:
    key = base64.b64decode(AES_KEY + "=")
    message = xml.encode()
    raw = os.urandom(16) + struct.pack(">I", len(message)) + message + b"corp"
    padder = padding.PKCS7(128).padder()
    data = padder.update(raw) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(key[:16])).encryptor()
    return base64.b64encode(encryptor.update(data) + encryptor.finalize()).decode()


def _sha1(*parts: str) -> str:
    return hashlib.sha1("".join(sorted(parts)).encode()).hexdigest()


async def _connector(app, platform: str) -> str:
    async with app.state.session_factory() as db:
        connector = await BotConnectorRepo.create(
            db,
            TEST_ORG_ID,
            name=f"{platform} bot",
            platform=platform,
            credentials={"token": f"{platform}-token", "encoding_aes_key": AES_KEY},
            allowed_capabilities=[],
            is_enabled=True,
        )
        await db.commit()
        return str(connector.id)


@pytest.fixture
def dispatched(monkeypatch):
    """What reached dispatch, which runs only after verification."""
    seen: list[dict] = []

    async def _capture(*, connector, adapter, payload, db):
        seen.append(payload)
        return {"ok": True}

    monkeypatch.setattr(
        "backend.api.routes.bot_webhooks._dispatch_verified_payload", _capture
    )
    return seen


@pytest.fixture
def decrypts(monkeypatch):
    """Every WeCom decrypt attempt."""
    calls: list[str] = []
    original = WeComAdapter._decrypt

    def _track(self, aes_key, msg_encrypt):
        calls.append(msg_encrypt)
        return original(self, aes_key, msg_encrypt)

    monkeypatch.setattr(WeComAdapter, "_decrypt", _track)
    return calls


async def test_wecom_verifies_before_decrypting(
    client: AsyncClient, app, dispatched, decrypts
):
    connector = await _connector(app, "wecom")
    url = f"/bot-connectors/{connector}/wecom/webhook"
    encrypt = _wecom_encrypt(MESSAGE)
    body = f"<xml><Encrypt>{encrypt}</Encrypt></xml>"
    stamp = {"timestamp": "1700000000", "nonce": "n1"}
    good = _sha1("wecom-token", "1700000000", "n1", encrypt)

    forged = await client.post(
        url, params={**stamp, "msg_signature": "0" * 40}, content=body
    )
    malformed = await client.post(
        url, params={**stamp, "msg_signature": good}, content=b"not xml <"
    )
    garbage = await client.post(
        url,
        params={
            **stamp,
            "msg_signature": _sha1("wecom-token", "1700000000", "n1", "AAAA"),
        },
        content="<xml><Encrypt>AAAA</Encrypt></xml>",
    )
    signed = await client.post(
        url, params={**stamp, "msg_signature": good}, content=body
    )

    assert (forged.status_code, malformed.status_code, garbage.status_code) == (
        403,
        400,
        400,
    )
    assert signed.status_code == 200, signed.text
    # Only signed bodies were ever decrypted, and only the good one dispatched.
    assert decrypts == ["AAAA", encrypt]
    assert dispatched == [{"_xml_content": MESSAGE}]


async def test_weixin_verifies_before_parsing(client: AsyncClient, app, dispatched):
    connector = await _connector(app, "weixin")
    url = f"/bot-connectors/{connector}/weixin/webhook"
    stamp = {"timestamp": "1700000000", "nonce": "n2"}
    good = _sha1("weixin-token", "1700000000", "n2")

    unsigned = await client.post(url, content=MESSAGE)
    forged = await client.post(
        url, params={**stamp, "signature": "0" * 40}, content=MESSAGE
    )
    malformed = await client.post(
        url, params={**stamp, "signature": good}, content=b"\xff\xfe not xml"
    )
    signed = await client.post(
        url, params={**stamp, "signature": good}, content=MESSAGE
    )

    assert (unsigned.status_code, forged.status_code, malformed.status_code) == (
        400,
        403,
        400,
    )
    assert signed.status_code == 200, signed.text
    assert dispatched == [{"_xml_content": MESSAGE}]


# ── R-25: the Inbox refuses API tokens ─────────────────────────────────────


async def test_the_inbox_and_its_settings_refuse_api_tokens(
    client: AsyncClient, auth_headers
):
    token = await client.post(
        "/api/v1/api-tokens",
        json={"name": "inbox-reader", "role": "admin"},
        headers=auth_headers,
    )
    assert token.status_code == 201, token.text
    bearer = {"Authorization": f"Bearer {token.json()['token']}"}
    calls = [
        ("GET", "/notifications", None),
        ("GET", "/notifications/unread-count", None),
        ("GET", "/notifications/preferences", None),
        ("PUT", "/notifications/preferences", {}),
        ("POST", "/notifications/read-all", None),
    ]

    for method, path, body in calls:
        refused = await client.request(method, path, json=body, headers=bearer)
        assert refused.status_code == 401, (path, refused.text)
        assert (
            refused.json()["detail"] == "API tokens are not accepted for this endpoint"
        )
    for method, path, _ in calls[:3]:
        signed_in = await client.request(method, path, headers=auth_headers)
        assert signed_in.status_code == 200, (path, signed_in.text)
