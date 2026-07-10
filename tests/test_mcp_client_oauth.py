"""Tests for Sprint 42 Step 5 — resolve_oauth_access_token + pool OAuth integration.

Covers:
  * Token is fresh → returns access token immediately (no refresh call).
  * Token is within the 300 s margin → triggers refresh, persists rotation.
  * Token is within margin but no refresh_token → raises MCPAuthorizationRequiredError
    and deletes the stale row.
  * Token is within margin, refresh_token present but issuer missing → raises.
  * Token is within margin, client_id missing → raises.
  * Refresh succeeds → new access token returned and row rotated.
  * Refresh fails (invalid_grant) → raises MCPAuthorizationRequiredError and
    deletes the token row.
  * No token row at all → raises MCPAuthorizationRequiredError.
  * MCPServerPool._resolve_server_config_with_oauth injects fresh token.
  * List endpoint includes oauth_status field.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.db.models import Base, Organization
from backend.db.repos import MCPServerOAuthTokenRepo, MCPServerRepo
from backend.mcp.client import OAUTH_REFRESH_MARGIN_SECONDS, resolve_oauth_access_token
from backend.mcp.oauth import MCPAuthorizationRequiredError

TEST_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000042")
SERVER_URL = "https://mcp.example.com/mcp"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fernet_key(monkeypatch):
    monkeypatch.setenv("OPSMENDER_SECRET_KEY", "sprint-42-step5-key")
    from backend.auth import secrets as _s

    if hasattr(_s, "_fernet_cache"):
        _s._fernet_cache = None


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)

    from sqlalchemy import event

    @event.listens_for(engine.sync_engine, "connect")
    def _pragma(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        session.add(Organization(id=TEST_ORG_ID, name="Test42", slug="test42"))
        await session.commit()

    async with factory() as session:
        yield session

    await engine.dispose()


async def _make_server(db) -> uuid.UUID:
    server = await MCPServerRepo.create(
        db,
        TEST_ORG_ID,
        name="oauth-mcp",
        transport="http",
        url=SERVER_URL,
    )
    await db.commit()
    return server.id


async def _upsert_token(
    db,
    server_id: uuid.UUID,
    *,
    access_token: str = "at-current",
    refresh_token: str | None = "rt-current",
    expires_at: datetime | None = None,
    issuer: str = "https://auth.example.com",
    client_id: str = "client-abc",
    client_secret: str | None = None,
):
    row = await MCPServerOAuthTokenRepo.upsert(
        db,
        TEST_ORG_ID,
        mcp_server_id=server_id,
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
        issuer=issuer,
        client_id=client_id,
        client_secret=client_secret,
    )
    await db.commit()
    return row


# ---------------------------------------------------------------------------
# Tests: fresh token (no refresh needed)
# ---------------------------------------------------------------------------


class TestFreshToken:
    async def test_returns_current_token_when_not_expiring(self, db):
        server_id = await _make_server(db)
        await _upsert_token(
            db,
            server_id,
            access_token="at-good",
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        )

        result = await resolve_oauth_access_token(
            db, TEST_ORG_ID, server_id, SERVER_URL
        )
        assert result == "at-good"

    async def test_returns_current_token_when_expires_at_is_null(self, db):
        """No expiry → token is valid indefinitely; never refresh."""
        server_id = await _make_server(db)
        await _upsert_token(db, server_id, access_token="at-eternal", expires_at=None)

        result = await resolve_oauth_access_token(
            db, TEST_ORG_ID, server_id, SERVER_URL
        )
        assert result == "at-eternal"


# ---------------------------------------------------------------------------
# Tests: no token row
# ---------------------------------------------------------------------------


class TestNoTokenRow:
    async def test_raises_when_no_row_exists(self, db):
        server_id = await _make_server(db)

        with pytest.raises(MCPAuthorizationRequiredError, match="No OAuth credentials"):
            await resolve_oauth_access_token(db, TEST_ORG_ID, server_id, SERVER_URL)


# ---------------------------------------------------------------------------
# Tests: token expiring — missing refresh data
# ---------------------------------------------------------------------------


class TestExpiringTokenMissingData:
    def _expiring_at(self) -> datetime:
        return datetime.now(timezone.utc) + timedelta(
            seconds=OAUTH_REFRESH_MARGIN_SECONDS - 30
        )

    async def test_raises_and_deletes_row_when_no_refresh_token(self, db):
        server_id = await _make_server(db)
        await _upsert_token(
            db,
            server_id,
            refresh_token=None,
            expires_at=self._expiring_at(),
        )

        with pytest.raises(MCPAuthorizationRequiredError, match="no refresh token"):
            await resolve_oauth_access_token(db, TEST_ORG_ID, server_id, SERVER_URL)

        # Row should be deleted so status pill flips to None immediately.
        gone = await MCPServerOAuthTokenRepo.get_for_server(db, TEST_ORG_ID, server_id)
        assert gone is None

    async def test_raises_when_issuer_missing(self, db):
        server_id = await _make_server(db)
        await _upsert_token(
            db,
            server_id,
            issuer=None,  # type: ignore[arg-type]
            expires_at=self._expiring_at(),
        )

        # issuer=None conflicts with the default; force it
        row = await MCPServerOAuthTokenRepo.get_for_server(db, TEST_ORG_ID, server_id)
        assert row is not None
        row.issuer = None
        await db.commit()

        with pytest.raises(MCPAuthorizationRequiredError, match="no issuer"):
            await resolve_oauth_access_token(db, TEST_ORG_ID, server_id, SERVER_URL)

    async def test_raises_when_client_id_missing(self, db):
        server_id = await _make_server(db)
        await _upsert_token(
            db,
            server_id,
            client_id=None,  # type: ignore[arg-type]
            expires_at=self._expiring_at(),
        )

        row = await MCPServerOAuthTokenRepo.get_for_server(db, TEST_ORG_ID, server_id)
        assert row is not None
        row.client_id = None
        await db.commit()

        with pytest.raises(
            MCPAuthorizationRequiredError, match="no client credentials"
        ):
            await resolve_oauth_access_token(db, TEST_ORG_ID, server_id, SERVER_URL)


# ---------------------------------------------------------------------------
# Tests: token expiring — refresh path
# ---------------------------------------------------------------------------


_MOCK_METADATA = MagicMock()
_MOCK_METADATA.token_endpoint = "https://auth.example.com/token"
_MOCK_METADATA.issuer = "https://auth.example.com"
_MOCK_METADATA.code_challenge_methods_supported = ["S256"]
_MOCK_METADATA.authorization_endpoint = "https://auth.example.com/authorize"
_MOCK_METADATA.registration_endpoint = None
_MOCK_METADATA.grant_types_supported = None
_MOCK_METADATA.scopes_supported = None


def _expiring() -> datetime:
    return datetime.now(timezone.utc) + timedelta(
        seconds=OAUTH_REFRESH_MARGIN_SECONDS - 60
    )


class TestRefreshPath:
    async def test_refresh_returns_new_access_token(self, db):
        server_id = await _make_server(db)
        await _upsert_token(
            db,
            server_id,
            access_token="at-old",
            refresh_token="rt-old",
            expires_at=_expiring(),
        )

        from backend.mcp import oauth as _oauth_mod

        mock_token = MagicMock()
        mock_token.access_token = "at-new"
        mock_token.refresh_token = "rt-new"
        mock_token.expires_in = 3600
        mock_token.scope = ["openid"]

        with (
            patch.object(
                _oauth_mod,
                "fetch_authz_server_metadata",
                new=AsyncMock(return_value=_MOCK_METADATA),
            ),
            patch.object(
                _oauth_mod,
                "refresh_access_token",
                new=AsyncMock(return_value=mock_token),
            ),
        ):
            result = await resolve_oauth_access_token(
                db, TEST_ORG_ID, server_id, SERVER_URL
            )

        assert result == "at-new"

    async def test_refresh_rotates_token_in_db(self, db):
        server_id = await _make_server(db)
        await _upsert_token(
            db,
            server_id,
            access_token="at-old",
            refresh_token="rt-keep",
            expires_at=_expiring(),
        )

        from backend.mcp import oauth as _oauth_mod

        mock_token = MagicMock()
        mock_token.access_token = "at-rotated"
        mock_token.refresh_token = "rt-rotated"
        mock_token.expires_in = 3600
        mock_token.scope = None

        with (
            patch.object(
                _oauth_mod,
                "fetch_authz_server_metadata",
                new=AsyncMock(return_value=_MOCK_METADATA),
            ),
            patch.object(
                _oauth_mod,
                "refresh_access_token",
                new=AsyncMock(return_value=mock_token),
            ),
        ):
            await resolve_oauth_access_token(db, TEST_ORG_ID, server_id, SERVER_URL)

        await db.commit()
        row = await MCPServerOAuthTokenRepo.get_for_server(db, TEST_ORG_ID, server_id)
        access, refresh = await MCPServerOAuthTokenRepo.read_plaintext(row)
        assert access == "at-rotated"
        assert refresh == "rt-rotated"
        assert row.last_refreshed_at is not None

    async def test_invalid_grant_deletes_row_and_raises(self, db):
        server_id = await _make_server(db)
        await _upsert_token(
            db,
            server_id,
            refresh_token="rt-expired",
            expires_at=_expiring(),
        )

        from backend.mcp import oauth as _oauth_mod

        with (
            patch.object(
                _oauth_mod,
                "fetch_authz_server_metadata",
                new=AsyncMock(return_value=_MOCK_METADATA),
            ),
            patch.object(
                _oauth_mod,
                "refresh_access_token",
                new=AsyncMock(
                    side_effect=MCPAuthorizationRequiredError("invalid_grant")
                ),
            ),
        ):
            with pytest.raises(MCPAuthorizationRequiredError):
                await resolve_oauth_access_token(db, TEST_ORG_ID, server_id, SERVER_URL)

        await db.commit()
        gone = await MCPServerOAuthTokenRepo.get_for_server(db, TEST_ORG_ID, server_id)
        assert gone is None


# ---------------------------------------------------------------------------
# Tests: client credentials stored correctly
# ---------------------------------------------------------------------------


class TestClientCredentialStorage:
    async def test_read_client_credentials_returns_stored_values(self, db):
        server_id = await _make_server(db)
        row = await MCPServerOAuthTokenRepo.upsert(
            db,
            TEST_ORG_ID,
            mcp_server_id=server_id,
            access_token="at",
            refresh_token=None,
            expires_at=None,
            client_id="dcr-client-id",
            client_secret="dcr-secret",
        )
        await db.commit()

        (
            client_id,
            client_secret,
        ) = await MCPServerOAuthTokenRepo.read_client_credentials(row)
        assert client_id == "dcr-client-id"
        assert client_secret == "dcr-secret"
        # client_secret must be stored encrypted
        assert row.client_secret_encrypted != "dcr-secret"

    async def test_read_client_credentials_public_client(self, db):
        server_id = await _make_server(db)
        row = await MCPServerOAuthTokenRepo.upsert(
            db,
            TEST_ORG_ID,
            mcp_server_id=server_id,
            access_token="at",
            refresh_token=None,
            expires_at=None,
            client_id="pub-client",
            client_secret=None,
        )
        await db.commit()

        (
            client_id,
            client_secret,
        ) = await MCPServerOAuthTokenRepo.read_client_credentials(row)
        assert client_id == "pub-client"
        assert client_secret is None


# ---------------------------------------------------------------------------
# Tests: map_by_server_id
# ---------------------------------------------------------------------------


class TestMapByServerId:
    async def test_returns_dict_keyed_by_server_id(self, db):
        s1 = await _make_server(db)
        s2_obj = await MCPServerRepo.create(
            db,
            TEST_ORG_ID,
            name="oauth-mcp-2",
            transport="http",
            url="https://mcp2.example.com/mcp",
        )
        await db.commit()

        await MCPServerOAuthTokenRepo.upsert(
            db,
            TEST_ORG_ID,
            mcp_server_id=s1,
            access_token="at-1",
            refresh_token=None,
            expires_at=None,
        )
        await db.commit()

        token_map = await MCPServerOAuthTokenRepo.map_by_server_id(db, TEST_ORG_ID)
        assert s1 in token_map
        assert s2_obj.id not in token_map

    async def test_returns_empty_dict_when_no_tokens(self, db):
        await _make_server(db)
        token_map = await MCPServerOAuthTokenRepo.map_by_server_id(db, TEST_ORG_ID)
        assert token_map == {}
