"""Tests for per-tenant SAML SSO (Sprint 30).

Strategy:
- The python3-saml signature/audience/replay validation is exercised against
  the IdP's actual response. Building one in-test would mean reproducing the
  whole signing pipeline. Instead we test the surface AIM owns:
  CRUD, XOR validation, tenant-resolve flag, route guards (503 when the SP
  keypair is missing, 400 when no active SAML config), and the helper's
  IdP metadata caching + mode dispatch.
- Full end-to-end ACS validation happens in a follow-up integration suite
  that boots a Keycloak container; out of scope for v1.
"""

from __future__ import annotations

import json
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.app import create_app
from backend.api.deps import get_db, set_session_factory
from backend.config_loader import set_env_path
from backend.db.models import Base, Organization

TEST_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000010")


# Minimal IdP EntityDescriptor for cache + dispatch tests. Not signed; we
# never call OneLogin_Saml2_Auth.process_response with this — only the
# parser, which accepts an unsigned descriptor at face value.
SAMPLE_IDP_METADATA = """<?xml version="1.0"?>
<EntityDescriptor xmlns="urn:oasis:names:tc:SAML:2.0:metadata"
                  entityID="https://idp.example.com/saml">
  <IDPSSODescriptor protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">
    <KeyDescriptor use="signing">
      <KeyInfo xmlns="http://www.w3.org/2000/09/xmldsig#">
        <X509Data><X509Certificate>MIIB</X509Certificate></X509Data>
      </KeyInfo>
    </KeyDescriptor>
    <SingleSignOnService
        Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
        Location="https://idp.example.com/sso"/>
  </IDPSSODescriptor>
</EntityDescriptor>
"""


@pytest.fixture
async def app(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    set_session_factory(factory)

    async with factory() as db:
        org = Organization(id=TEST_ORG_ID, name="SAML Org", slug="saml-org")
        db.add(org)
        await db.commit()

    tmp_env = tmp_path / ".env"
    tmp_env.write_text(
        "AIM_TIER=2\n"
        "AIM_LOG_LEVEL=INFO\n"
        "AIM_AUDIT_LOG=./logs/audit.jsonl\n"
        "AIM_JWT_SECRET=test-secret\n"
        "AIM_DATABASE_URL=sqlite+aiosqlite://\n"
        f"AIM_MCP_SERVERS_JSON={json.dumps([])}\n"
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
            "username": "samltestadmin",
            "email": "samladmin@test.com",
            "password": "securepass123",
        },
    )
    from backend.db.repos import UserRepo

    async with app.state.session_factory() as db:
        user = await UserRepo.get_by_username(db, "samltestadmin")
        if user:
            user.primary_org_id = TEST_ORG_ID
            await db.commit()

    resp = await client.post(
        "/auth/login",
        json={"username": "samltestadmin", "password": "securepass123"},
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


class TestSAMLCRUD:
    async def test_create_via_metadata_url(self, client, auth_headers):
        resp = await client.put(
            f"/organizations/{TEST_ORG_ID}/saml",
            json={
                "idp_metadata_url": "https://idp.example.com/metadata",
                "default_role": "operator",
                "allowed_email_domains": "acme.com",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["idp_metadata_url"] == "https://idp.example.com/metadata"
        assert body["has_idp_metadata_xml"] is False
        assert body["default_role"] == "operator"
        # Sensitive raw XML must never appear in the response.
        assert "idp_metadata_xml" not in body

    async def test_create_via_metadata_xml(self, client, auth_headers):
        resp = await client.put(
            f"/organizations/{TEST_ORG_ID}/saml",
            json={"idp_metadata_xml": SAMPLE_IDP_METADATA},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["idp_metadata_url"] is None
        assert body["has_idp_metadata_xml"] is True

    async def test_xor_required(self, client, auth_headers):
        resp = await client.put(
            f"/organizations/{TEST_ORG_ID}/saml",
            json={
                "idp_metadata_url": "https://idp.example.com/metadata",
                "idp_metadata_xml": SAMPLE_IDP_METADATA,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 400

    async def test_xor_required_neither(self, client, auth_headers):
        resp = await client.put(
            f"/organizations/{TEST_ORG_ID}/saml",
            json={"default_role": "viewer"},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    async def test_get_404_when_missing(self, client, auth_headers):
        resp = await client.get(
            f"/organizations/{TEST_ORG_ID}/saml", headers=auth_headers
        )
        assert resp.status_code == 404

    async def test_delete_round_trip(self, client, auth_headers):
        await client.put(
            f"/organizations/{TEST_ORG_ID}/saml",
            json={"idp_metadata_url": "https://idp.example.com/metadata"},
            headers=auth_headers,
        )
        resp = await client.delete(
            f"/organizations/{TEST_ORG_ID}/saml", headers=auth_headers
        )
        assert resp.status_code == 204
        # Second delete is a 404 (idempotency check).
        resp = await client.delete(
            f"/organizations/{TEST_ORG_ID}/saml", headers=auth_headers
        )
        assert resp.status_code == 404

    async def test_admin_required(self, client, app):
        # Anonymous → 401/403.
        resp = await client.put(
            f"/organizations/{TEST_ORG_ID}/saml",
            json={"idp_metadata_url": "https://idp.example.com/metadata"},
        )
        assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# Tenant resolve advertises saml_enabled
# ---------------------------------------------------------------------------


class TestTenantResolveExposesSAML:
    async def test_saml_enabled_flag(self, client, app, auth_headers):
        # Pin a host to the org first.
        from backend.db.repos import (
            OrganizationDomainRepo,
            OrgSAMLConfigRepo,
        )

        async with app.state.session_factory() as db:
            await OrganizationDomainRepo.create(
                db,
                org_id=TEST_ORG_ID,
                domain="aim.acme.com",
                is_primary=True,
            )
            await OrgSAMLConfigRepo.upsert(
                db,
                org_id=TEST_ORG_ID,
                idp_metadata_url="https://idp.example.com/metadata",
            )
            await db.commit()

        resp = await client.get("/tenant/resolve", headers={"Host": "aim.acme.com"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["pinned"] is True
        assert body["saml_enabled"] is True
        assert body["saml_login_path"] == "/auth/saml/saml-org/login"


# ---------------------------------------------------------------------------
# Route guards
# ---------------------------------------------------------------------------


class TestSAMLRouteGuards:
    async def test_login_503_when_sp_keypair_missing(self, client, app, auth_headers):
        from backend.db.repos import OrgSAMLConfigRepo

        async with app.state.session_factory() as db:
            await OrgSAMLConfigRepo.upsert(
                db,
                org_id=TEST_ORG_ID,
                idp_metadata_url="https://idp.example.com/metadata",
            )
            await db.commit()

        # No AIM_SAML_SP_CERT / KEY in the test env → 503.
        resp = await client.get("/auth/saml/saml-org/login")
        assert resp.status_code == 503
        assert "AIM_SAML_SP_CERT" in resp.text

    async def test_login_400_when_no_active_config(self, client, app, monkeypatch):
        # SP keypair present but no SAML row for the org.
        monkeypatch.setenv("AIM_SAML_SP_CERT", "stub")
        monkeypatch.setenv("AIM_SAML_SP_KEY", "stub")
        resp = await client.get("/auth/saml/saml-org/login")
        # Without an OrgSAMLConfig row, _resolve_active_saml returns 400.
        assert resp.status_code == 400

    async def test_login_404_for_unknown_org(self, client, monkeypatch):
        monkeypatch.setenv("AIM_SAML_SP_CERT", "stub")
        monkeypatch.setenv("AIM_SAML_SP_KEY", "stub")
        resp = await client.get("/auth/saml/no-such-slug/login")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Helper unit tests (fetch_idp_metadata, first_attribute, split_base_url)
# ---------------------------------------------------------------------------


class TestSAMLHelper:
    async def test_fetch_idp_metadata_inline_xml(self):
        from backend.auth.saml import SAMLOrgConfig, fetch_idp_metadata, reset_caches

        reset_caches()
        cfg = SAMLOrgConfig(
            org_slug="x",
            is_active=True,
            idp_metadata_url=None,
            idp_metadata_xml=SAMPLE_IDP_METADATA,
            email_attribute="email",
            name_attribute="name",
            want_assertions_signed=True,
            want_response_signed=True,
        )
        idp = await fetch_idp_metadata(cfg)
        assert idp["entityId"] == "https://idp.example.com/saml"
        assert idp["singleSignOnService"]["url"] == "https://idp.example.com/sso"

    async def test_fetch_idp_metadata_url_caches(self, monkeypatch):
        from backend.auth import saml as saml_mod
        from backend.auth.saml import SAMLOrgConfig, fetch_idp_metadata, reset_caches

        reset_caches()

        call_count = {"n": 0}

        class _Resp:
            status_code = 200
            text = SAMPLE_IDP_METADATA

        class _AsyncCtx:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url):
                call_count["n"] += 1
                return _Resp()

        def _client(*a, **kw):
            return _AsyncCtx()

        monkeypatch.setattr(saml_mod.httpx, "AsyncClient", _client)

        cfg = SAMLOrgConfig(
            org_slug="x",
            is_active=True,
            idp_metadata_url="https://idp.example.com/metadata",
            idp_metadata_xml=None,
            email_attribute="email",
            name_attribute="name",
            want_assertions_signed=True,
            want_response_signed=True,
        )
        first = await fetch_idp_metadata(cfg)
        second = await fetch_idp_metadata(cfg)
        assert first == second
        # Second call must be served from cache → only one HTTP call total.
        assert call_count["n"] == 1

    async def test_fetch_idp_metadata_neither_set_raises(self):
        from backend.auth.saml import SAMLOrgConfig, SAMLError, fetch_idp_metadata

        cfg = SAMLOrgConfig(
            org_slug="x",
            is_active=True,
            idp_metadata_url=None,
            idp_metadata_xml=None,
            email_attribute="email",
            name_attribute="name",
            want_assertions_signed=True,
            want_response_signed=True,
        )
        with pytest.raises(SAMLError):
            await fetch_idp_metadata(cfg)

    def test_first_attribute_uses_fallback(self):
        from backend.auth.saml import first_attribute

        attrs = {"email": ["alice@example.com"]}
        assert (
            first_attribute(
                attrs,
                "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
                fallback_keys=["email"],
            )
            == "alice@example.com"
        )

    def test_first_attribute_missing(self):
        from backend.auth.saml import first_attribute

        assert first_attribute({}, "anything", fallback_keys=["x", "y"]) is None

    def test_split_base_url(self):
        from backend.auth.saml import split_base_url

        base, https, host, port, path = split_base_url(
            "https://aim.acme.com/auth/saml/acme/login"
        )
        assert base == "https://aim.acme.com"
        assert https is True
        assert host == "aim.acme.com"
        assert port == 443
        assert path == "/auth/saml/acme/login"

    def test_sp_keypair_configured_property(self):
        from backend.auth.saml import SPKeypair

        assert SPKeypair(cert="", key="").configured is False
        assert SPKeypair(cert="x", key="").configured is False
        assert SPKeypair(cert="x", key="y").configured is True
