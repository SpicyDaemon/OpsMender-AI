"""Tests for per-tenant SSO (OIDC) - Sprint 30.

Reuses the in-memory SQLite + ASGI fixtures from tests/test_api.py via
shared imports rather than copy-pasting the fixture wiring.
"""

from __future__ import annotations

import json
import secrets
import time
import uuid
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from httpx import ASGITransport, AsyncClient, MockTransport, Request, Response
from jose import jwk
from jose import jwt as jose_jwt
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.app import create_app
from backend.api.deps import get_db, set_session_factory
from backend.config_loader import set_env_path
from backend.db.models import Base, Organization

TEST_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


@pytest.fixture
async def app(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    set_session_factory(factory)

    async with factory() as db:
        org = Organization(id=TEST_ORG_ID, name="Test Org", slug="test-org")
        db.add(org)
        await db.commit()

    tmp_env = tmp_path / ".env"
    tmp_env.write_text(
        "OPSMENDER_TIER=2\n"
        "OPSMENDER_LOG_LEVEL=INFO\n"
        "OPSMENDER_AUDIT_LOG=./logs/audit.jsonl\n"
        "OPSMENDER_JWT_SECRET=test-secret\n"
        "OPSMENDER_DATABASE_URL=sqlite+aiosqlite://\n"
        f"OPSMENDER_MCP_SERVERS_JSON={json.dumps([])}\n"
    )
    set_env_path(tmp_env)

    application = create_app()
    application.state.session_factory = factory

    async def _override_db():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    application.dependency_overrides[get_db] = _override_db
    yield application
    set_env_path(None)
    await engine.dispose()


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def auth_headers(client: AsyncClient, app):
    await client.post(
        "/auth/register",
        json={
            "username": "ssotestadmin",
            "email": "ssoadmin@test.com",
            "password": "securepass123",
        },
    )
    from backend.db.repos import UserRepo

    async with app.state.session_factory() as db:
        user = await UserRepo.get_by_username(db, "ssotestadmin")
        if user:
            user.primary_org_id = TEST_ORG_ID
            await db.commit()

    resp = await client.post(
        "/auth/login",
        json={"username": "ssotestadmin", "password": "securepass123"},
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _patch_idp(monkeypatch):
    """Replace the OIDC client's network calls with deterministic stubs."""
    from backend.auth import oidc as oidc_mod

    async def fake_get_discovery(url):
        return {
            "authorization_endpoint": "https://idp.example.com/oauth2/authorize",
            "token_endpoint": "https://idp.example.com/oauth2/token",
            "jwks_uri": "https://idp.example.com/jwks",
            "issuer": "https://idp.example.com",
        }

    monkeypatch.setattr(oidc_mod, "_get_discovery", fake_get_discovery)
    oidc_mod.reset_caches()


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


class TestSSOCRUD:
    async def test_get_missing_returns_unconfigured_state(self, client, auth_headers):
        resp = await client.get(
            f"/organizations/{TEST_ORG_ID}/sso", headers=auth_headers
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["configured"] is False
        assert body["is_active"] is False
        assert body["id"] is None

    async def test_create_sso(self, client, auth_headers):
        resp = await client.put(
            f"/organizations/{TEST_ORG_ID}/sso",
            json={
                "provider": "oidc",
                "discovery_url": "https://idp.example.com/.well-known/openid-configuration",
                "client_id": "opsmender-app",
                "client_secret": "supersecret",
                "default_role": "operator",
                "allowed_email_domains": "acme.com",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["has_client_secret"] is True
        assert "client_secret" not in body
        assert "client_secret_encrypted" not in body

    async def test_create_requires_secret_first_time(self, client, auth_headers):
        resp = await client.put(
            f"/organizations/{TEST_ORG_ID}/sso",
            json={
                "provider": "oidc",
                "discovery_url": "https://idp.example.com/.well-known/openid-configuration",
                "client_id": "opsmender-app",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 400

    async def test_update_preserves_existing_secret(self, client, app, auth_headers):
        await client.put(
            f"/organizations/{TEST_ORG_ID}/sso",
            json={
                "provider": "oidc",
                "discovery_url": "https://idp.example.com/.well-known/openid-configuration",
                "client_id": "opsmender-app",
                "client_secret": "original-secret",
            },
            headers=auth_headers,
        )

        from backend.db.repos import OrgSSOConfigRepo

        async with app.state.session_factory() as db:
            row = await OrgSSOConfigRepo.get_for_org(db, TEST_ORG_ID)
            original = row.client_secret_encrypted

        resp = await client.put(
            f"/organizations/{TEST_ORG_ID}/sso",
            json={
                "provider": "oidc",
                "discovery_url": "https://idp.example.com/.well-known/openid-configuration",
                "client_id": "opsmender-app",
                "default_role": "admin",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200

        async with app.state.session_factory() as db:
            row = await OrgSSOConfigRepo.get_for_org(db, TEST_ORG_ID)
            assert row.client_secret_encrypted == original
            assert row.default_role == "admin"

    async def test_delete_sso(self, client, auth_headers):
        await client.put(
            f"/organizations/{TEST_ORG_ID}/sso",
            json={
                "provider": "oidc",
                "discovery_url": "https://idp.example.com/.well-known/openid-configuration",
                "client_id": "opsmender-app",
                "client_secret": "x",
            },
            headers=auth_headers,
        )
        resp = await client.delete(
            f"/organizations/{TEST_ORG_ID}/sso", headers=auth_headers
        )
        assert resp.status_code == 204

    async def test_resolve_tenant_reports_sso(self, client, auth_headers):
        await client.post(
            f"/organizations/{TEST_ORG_ID}/domains",
            json={"domain": "sso.example.com"},
            headers=auth_headers,
        )
        resp = await client.get("/tenant/resolve", headers={"Host": "sso.example.com"})
        assert resp.json()["sso_enabled"] is False

        await client.put(
            f"/organizations/{TEST_ORG_ID}/sso",
            json={
                "provider": "oidc",
                "discovery_url": "https://idp.example.com/.well-known/openid-configuration",
                "client_id": "opsmender-app",
                "client_secret": "x",
            },
            headers=auth_headers,
        )
        body = (
            await client.get("/tenant/resolve", headers={"Host": "sso.example.com"})
        ).json()
        assert body["sso_enabled"] is True
        assert body["sso_login_path"] == "/auth/sso/test-org/login"


# ---------------------------------------------------------------------------
# Login flow
# ---------------------------------------------------------------------------


class TestSSOLoginFlow:
    async def test_login_disabled_returns_400(self, client):
        resp = await client.get("/auth/sso/test-org/login", follow_redirects=False)
        assert resp.status_code == 400

    async def test_login_unknown_org_returns_404(self, client):
        resp = await client.get("/auth/sso/no-such-org/login", follow_redirects=False)
        assert resp.status_code == 404

    async def test_login_redirects_to_idp(self, client, app, auth_headers, monkeypatch):
        await client.put(
            f"/organizations/{TEST_ORG_ID}/sso",
            json={
                "provider": "oidc",
                "discovery_url": "https://idp.example.com/.well-known/openid-configuration",
                "client_id": "opsmender-app",
                "client_secret": "supersecret",
            },
            headers=auth_headers,
        )
        _patch_idp(monkeypatch)

        resp = await client.get("/auth/sso/test-org/login", follow_redirects=False)
        assert resp.status_code == 302
        loc = resp.headers["location"]
        assert loc.startswith("https://idp.example.com/oauth2/authorize?")
        qs = parse_qs(urlparse(loc).query)
        assert qs["client_id"] == ["opsmender-app"]
        assert "state" in qs
        assert "nonce" in qs

    async def test_callback_jit_provisions_user(
        self, client, app, auth_headers, monkeypatch
    ):
        await client.put(
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
        _patch_idp(monkeypatch)

        async def fake_exchange(config, *, code, redirect_uri, nonce=None):
            return {
                "sub": "idp-user-123",
                "email": "alice@acme.com",
                "name": "Alice Anderson",
                "iss": "https://idp.example.com",
                "aud": "opsmender-app",
                "nonce": nonce,
            }

        from backend.api.routes import sso as sso_route_mod

        monkeypatch.setattr(sso_route_mod, "exchange_code", fake_exchange)

        login = await client.get("/auth/sso/test-org/login", follow_redirects=False)
        state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]

        cb = await client.get(
            f"/auth/sso/test-org/callback?code=stub&state={state}",
            follow_redirects=False,
        )
        assert cb.status_code == 302
        assert "/login#sso_token=" in cb.headers["location"]

        from backend.db.repos import UserRepo

        async with app.state.session_factory() as db:
            user = await UserRepo.get_by_email(db, "alice@acme.com")
            assert user is not None
            assert user.auth_source == "oidc:test-org"
            assert user.role == "operator"
            assert user.primary_org_id == TEST_ORG_ID
            assert await UserRepo.is_member(db, user.id, TEST_ORG_ID)

    async def test_callback_blocks_disallowed_email_domain(
        self, client, app, auth_headers, monkeypatch
    ):
        await client.put(
            f"/organizations/{TEST_ORG_ID}/sso",
            json={
                "provider": "oidc",
                "discovery_url": "https://idp.example.com/.well-known/openid-configuration",
                "client_id": "opsmender-app",
                "client_secret": "x",
                "allowed_email_domains": "acme.com",
            },
            headers=auth_headers,
        )
        _patch_idp(monkeypatch)

        async def fake_exchange(config, *, code, redirect_uri, nonce=None):
            return {"email": "eve@evil.com", "nonce": nonce}

        from backend.api.routes import sso as sso_route_mod

        monkeypatch.setattr(sso_route_mod, "exchange_code", fake_exchange)

        login = await client.get("/auth/sso/test-org/login", follow_redirects=False)
        state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]

        cb = await client.get(
            f"/auth/sso/test-org/callback?code=stub&state={state}",
            follow_redirects=False,
        )
        assert cb.status_code == 403

    async def test_callback_rejects_bogus_state(
        self, client, auth_headers, monkeypatch
    ):
        await client.put(
            f"/organizations/{TEST_ORG_ID}/sso",
            json={
                "provider": "oidc",
                "discovery_url": "https://idp.example.com/.well-known/openid-configuration",
                "client_id": "opsmender-app",
                "client_secret": "x",
            },
            headers=auth_headers,
        )
        _patch_idp(monkeypatch)

        cb = await client.get(
            "/auth/sso/test-org/callback?code=stub&state=not-a-jwt",
            follow_redirects=False,
        )
        assert cb.status_code == 400


# ---------------------------------------------------------------------------
# id token signature algorithms (M1-58)
# ---------------------------------------------------------------------------

IDP_ISSUER = "https://idp.example.com"


@pytest.fixture(scope="module")
def idp_keys():
    """Per algorithm, the key that signs the id token and the key the
    provider publishes for it. The symmetric key is published as is, so an
    HS256 token signed with it carries a valid signature."""
    rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ec_key = ec.generate_private_key(ec.SECP256R1())
    symmetric = {"kty": "oct", "k": secrets.token_urlsafe(32)}
    return {
        "RS256": (rsa_key, jwk.construct(rsa_key, "RS256").public_key().to_dict()),
        "ES256": (ec_key, jwk.construct(ec_key, "ES256").public_key().to_dict()),
        "HS256": (symmetric, symmetric),
    }


def _serve_idp(monkeypatch, *, listed: list[str] | None, published: dict) -> dict:
    """Serve the provider's discovery document, token endpoint and key set
    from memory. The token endpoint answers with ``issued["id_token"]``."""
    from backend.auth import oidc as oidc_mod

    discovery = {
        "issuer": IDP_ISSUER,
        "authorization_endpoint": f"{IDP_ISSUER}/oauth2/authorize",
        "token_endpoint": f"{IDP_ISSUER}/oauth2/token",
        "jwks_uri": f"{IDP_ISSUER}/jwks",
    }
    if listed is not None:
        discovery["id_token_signing_alg_values_supported"] = listed
    issued: dict[str, str] = {}

    def handler(request: Request) -> Response:
        if request.url.path == "/.well-known/openid-configuration":
            return Response(200, json=discovery)
        if request.url.path == "/oauth2/token":
            return Response(200, json={"id_token": issued["id_token"]})
        if request.url.path == "/jwks":
            return Response(200, json={"keys": [published]})
        return Response(404)

    transport = MockTransport(handler)
    monkeypatch.setattr(
        oidc_mod,
        "httpx",
        SimpleNamespace(
            AsyncClient=lambda **kwargs: AsyncClient(transport=transport, **kwargs)
        ),
    )
    monkeypatch.setattr(oidc_mod, "_discovery_cache", {})
    monkeypatch.setattr(oidc_mod, "_jwks_cache", {})
    return issued


async def _sign_in_with_id_token(
    client, auth_headers, monkeypatch, idp_keys, *, listed, algorithm, email
):
    """Sign in through OIDC with an id token signed by ``algorithm``'s key,
    from a provider that lists ``listed`` and publishes that key."""
    saved = await client.put(
        f"/organizations/{TEST_ORG_ID}/sso",
        json={
            "provider": "oidc",
            "discovery_url": f"{IDP_ISSUER}/.well-known/openid-configuration",
            "client_id": "opsmender-app",
            "client_secret": "x",
            "default_role": "operator",
        },
        headers=auth_headers,
    )
    assert saved.status_code == 200, saved.text
    signing_key, published = idp_keys[algorithm]
    issued = _serve_idp(monkeypatch, listed=listed, published=published)
    login = await client.get("/auth/sso/test-org/login", follow_redirects=False)
    query = parse_qs(urlparse(login.headers["location"]).query)
    now = int(time.time())
    issued["id_token"] = jose_jwt.encode(
        {
            "iss": IDP_ISSUER,
            "aud": "opsmender-app",
            "sub": email,
            "email": email,
            "nonce": query["nonce"][0],
            "iat": now,
            "exp": now + 300,
        },
        signing_key,
        algorithm=algorithm,
    )
    return await client.get(
        f"/auth/sso/test-org/callback?code=stub&state={query['state'][0]}",
        follow_redirects=False,
    )


class TestIdTokenAlgorithms:
    """The id token is accepted only when signed with an asymmetric algorithm
    the provider's discovery document lists, RS256 when it lists none. The
    provider always publishes the signing key, so every signature here is
    valid and the algorithm alone decides."""

    @pytest.mark.parametrize(
        ("listed", "algorithm"),
        [(None, "RS256"), (["ES256"], "ES256")],
        ids=["rs256-when-none-listed", "es256-when-only-es256-listed"],
    )
    async def test_id_token_signed_with_a_listed_algorithm_signs_in(
        self, client, app, auth_headers, monkeypatch, idp_keys, listed, algorithm
    ):
        from backend.db.repos import UserRepo

        resp = await _sign_in_with_id_token(
            client,
            auth_headers,
            monkeypatch,
            idp_keys,
            listed=listed,
            algorithm=algorithm,
            email="ada@acme.com",
        )

        assert resp.status_code == 302, resp.text
        assert "/login#sso_token=" in resp.headers["location"]
        async with app.state.session_factory() as db:
            user = await UserRepo.get_by_email(db, "ada@acme.com")
        assert user is not None
        assert user.auth_source == "oidc:test-org"

    @pytest.mark.parametrize(
        ("listed", "algorithm"),
        [(["RS256", "HS256"], "HS256"), (None, "HS256"), (["ES256"], "RS256")],
        ids=[
            "hs256-even-when-listed",
            "hs256-when-none-listed",
            "rs256-when-only-es256-listed",
        ],
    )
    async def test_id_token_signed_with_another_algorithm_is_refused(
        self, client, app, auth_headers, monkeypatch, idp_keys, listed, algorithm
    ):
        from backend.db.repos import UserRepo

        resp = await _sign_in_with_id_token(
            client,
            auth_headers,
            monkeypatch,
            idp_keys,
            listed=listed,
            algorithm=algorithm,
            email="mallory@acme.com",
        )

        assert resp.status_code == 400, resp.text
        assert resp.json()["detail"] == (
            "SSO failed: id_token validation failed: "
            "The specified alg value is not allowed"
        )
        async with app.state.session_factory() as db:
            assert await UserRepo.get_by_email(db, "mallory@acme.com") is None


# ---------------------------------------------------------------------------
# Secret-at-rest helper
# ---------------------------------------------------------------------------


class TestSecretsHelper:
    async def test_roundtrip(self, monkeypatch):
        monkeypatch.setenv("OPSMENDER_JWT_SECRET", "test-secret")
        from backend.auth.secrets import encrypt_secret, decrypt_secret

        ct = encrypt_secret("hello-world")
        assert ct != "hello-world"
        assert decrypt_secret(ct) == "hello-world"

    async def test_decrypt_with_wrong_key_raises(self, monkeypatch):
        monkeypatch.setenv("OPSMENDER_JWT_SECRET", "test-secret")
        from backend.auth.secrets import encrypt_secret, decrypt_secret

        ct = encrypt_secret("payload")
        monkeypatch.setenv("OPSMENDER_SECRET_KEY", "different-key")
        with pytest.raises(ValueError):
            decrypt_secret(ct)
