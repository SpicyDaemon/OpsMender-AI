"""M1-43 (R-19): OIDC sign-in, the same flow as SAML, refuses a deactivated
account before minting a token and changes nothing about it."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from httpx import AsyncClient

from backend.api.auth import hash_password
from backend.db.repos import UserRepo
from tests.test_sso import (
    TEST_ORG_ID,
    _patch_idp,
    app as _app_fixture,
    auth_headers as _auth_headers_fixture,
    client as _client_fixture,
)

app = pytest.fixture(_app_fixture.__wrapped__)
client = pytest.fixture(_client_fixture.__wrapped__)
auth_headers = pytest.fixture(_auth_headers_fixture.__wrapped__)


async def test_oidc_refuses_a_deactivated_account_at_sign_in(
    client: AsyncClient, app, auth_headers, monkeypatch
):
    saved = await client.put(
        f"/organizations/{TEST_ORG_ID}/sso",
        json={
            "provider": "oidc",
            "discovery_url": "https://idp.example.com/.well-known/openid-configuration",
            "client_id": "opsmender-app",
            "client_secret": "x",
            "default_role": "operator",
        },
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
    _patch_idp(monkeypatch)

    async def fake_exchange(config, *, code, redirect_uri, nonce=None):
        return {"email": "gone@acme.com", "name": "Gone", "nonce": nonce}

    from backend.api.routes import sso as sso_route

    monkeypatch.setattr(sso_route, "exchange_code", fake_exchange)
    login = await client.get("/auth/sso/test-org/login", follow_redirects=False)
    state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]

    resp = await client.get(
        f"/auth/sso/test-org/callback?code=stub&state={state}", follow_redirects=False
    )

    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == (
        "This account is deactivated. Ask an admin to reactivate it."
    )
    async with app.state.session_factory() as db:
        user = await UserRepo.get_by_email(db, "gone@acme.com")
    assert (user.is_active, user.auth_source) == (False, "local")
