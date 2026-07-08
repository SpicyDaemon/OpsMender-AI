"""Tests for the MCPServerOAuthToken model + repo (Sprint 42 step 1).

Covers the persistence layer only — the OAuth client (Step 3) lands in
a sibling test file once the discovery + PKCE + refresh code is in.

Encryption boundary: the repo wraps Fernet around plaintext at write
time and unwraps on read. These tests deliberately assert that
plaintext NEVER appears in the column itself (round-trip via the repo
methods is the only legal read path).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.db.models import (
    Base,
    Organization,
)
from backend.db.repos import (
    MCPServerOAuthTokenRepo,
    MCPServerRepo,
)


TEST_ORG_ID = uuid.UUID("00000000-0000-0000-0000-0000000000aa")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fernet_key(monkeypatch):
    """Pin a deterministic Fernet seed so encrypt/decrypt is stable.

    Without this the helper falls back to AppConfig.load() which reads
    .env and may differ between test runs.
    """

    monkeypatch.setenv("OPSMENDER_SECRET_KEY", "sprint-42-test-key")
    # Drop any cached Fernet so the new seed is picked up.
    from backend.auth import secrets as _secrets_mod
    if hasattr(_secrets_mod, "_fernet_cache"):
        _secrets_mod._fernet_cache = None


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)

    # SQLite needs `PRAGMA foreign_keys = ON` per-connection for cascade
    # deletes to fire. SQLAlchemy doesn't set it for aiosqlite by default.
    from sqlalchemy import event

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        session.add(Organization(id=TEST_ORG_ID, name="Test", slug="test"))
        await session.commit()

    async with factory() as session:
        yield session

    await engine.dispose()


async def _make_server(db) -> uuid.UUID:
    server = await MCPServerRepo.create(
        db,
        TEST_ORG_ID,
        name="test-mcp",
        transport="http",
        url="https://mcp.example.com/mcp",
    )
    await db.commit()
    return server.id


# ---------------------------------------------------------------------------
# Upsert / read
# ---------------------------------------------------------------------------


class TestUpsert:
    async def test_creates_new_row_with_encrypted_tokens(self, db):
        server_id = await _make_server(db)

        row = await MCPServerOAuthTokenRepo.upsert(
            db,
            TEST_ORG_ID,
            mcp_server_id=server_id,
            access_token="at-plain-1",
            refresh_token="rt-plain-1",
            expires_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            scopes=["openid", "profile"],
            issuer="https://auth.example.com",
        )
        await db.commit()

        assert row.id is not None
        assert row.org_id == TEST_ORG_ID
        assert row.mcp_server_id == server_id
        assert row.token_type == "Bearer"
        # Encrypted column NEVER carries plaintext.
        assert row.access_token_encrypted != "at-plain-1"
        assert row.refresh_token_encrypted != "rt-plain-1"
        # And the cipher is Fernet-shaped (URL-safe-base64 ASCII).
        assert all(c.isascii() for c in row.access_token_encrypted)
        assert row.scopes == ["openid", "profile"]
        assert row.issuer == "https://auth.example.com"
        assert row.last_refreshed_at is None

    async def test_read_plaintext_round_trips(self, db):
        server_id = await _make_server(db)
        row = await MCPServerOAuthTokenRepo.upsert(
            db,
            TEST_ORG_ID,
            mcp_server_id=server_id,
            access_token="at-roundtrip",
            refresh_token="rt-roundtrip",
            expires_at=None,
            scopes=None,
            issuer=None,
        )
        await db.commit()

        access, refresh = await MCPServerOAuthTokenRepo.read_plaintext(row)
        assert access == "at-roundtrip"
        assert refresh == "rt-roundtrip"

    async def test_replaces_existing_row_on_upsert(self, db):
        server_id = await _make_server(db)
        first = await MCPServerOAuthTokenRepo.upsert(
            db,
            TEST_ORG_ID,
            mcp_server_id=server_id,
            access_token="at-first",
            refresh_token="rt-first",
            expires_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        )
        await db.commit()

        second = await MCPServerOAuthTokenRepo.upsert(
            db,
            TEST_ORG_ID,
            mcp_server_id=server_id,
            access_token="at-second",
            refresh_token=None,  # AS chose not to issue a refresh this time
            expires_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        )
        await db.commit()

        # Same row id — replacement, not a new insert (UNIQUE on mcp_server_id).
        assert second.id == first.id

        access, refresh = await MCPServerOAuthTokenRepo.read_plaintext(second)
        assert access == "at-second"
        assert refresh is None
        assert second.expires_at == datetime(2026, 7, 1, tzinfo=timezone.utc)
        assert second.last_refreshed_at is None  # upsert resets

    async def test_no_refresh_token_is_supported(self, db):
        """Spec §6.4 says clients MUST NOT assume refresh tokens are issued."""

        server_id = await _make_server(db)
        row = await MCPServerOAuthTokenRepo.upsert(
            db,
            TEST_ORG_ID,
            mcp_server_id=server_id,
            access_token="at-no-refresh",
            refresh_token=None,
            expires_at=None,
        )
        await db.commit()

        assert row.refresh_token_encrypted is None
        access, refresh = await MCPServerOAuthTokenRepo.read_plaintext(row)
        assert access == "at-no-refresh"
        assert refresh is None


# ---------------------------------------------------------------------------
# Rotation
# ---------------------------------------------------------------------------


class TestRotate:
    async def test_rotate_persists_new_tokens_and_stamps_last_refreshed(self, db):
        server_id = await _make_server(db)
        await MCPServerOAuthTokenRepo.upsert(
            db,
            TEST_ORG_ID,
            mcp_server_id=server_id,
            access_token="at-1",
            refresh_token="rt-1",
            expires_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        )
        await db.commit()

        rotated = await MCPServerOAuthTokenRepo.rotate(
            db,
            TEST_ORG_ID,
            mcp_server_id=server_id,
            access_token="at-2",
            refresh_token="rt-2",
            expires_at=datetime(2026, 6, 2, tzinfo=timezone.utc),
        )
        await db.commit()

        assert rotated is not None
        assert rotated.last_refreshed_at is not None
        access, refresh = await MCPServerOAuthTokenRepo.read_plaintext(rotated)
        assert access == "at-2"
        assert refresh == "rt-2"
        assert rotated.expires_at == datetime(2026, 6, 2, tzinfo=timezone.utc)

    async def test_rotate_without_new_refresh_keeps_existing(self, db):
        """OAuth 2.1 §4.3.1: if the AS omits refresh_token from the response,
        keep using the prior one — it is still valid until the AS rotates."""

        server_id = await _make_server(db)
        await MCPServerOAuthTokenRepo.upsert(
            db,
            TEST_ORG_ID,
            mcp_server_id=server_id,
            access_token="at-1",
            refresh_token="rt-keep",
            expires_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
        )
        await db.commit()

        rotated = await MCPServerOAuthTokenRepo.rotate(
            db,
            TEST_ORG_ID,
            mcp_server_id=server_id,
            access_token="at-2",
            refresh_token=None,
            expires_at=datetime(2026, 6, 2, tzinfo=timezone.utc),
        )
        await db.commit()

        access, refresh = await MCPServerOAuthTokenRepo.read_plaintext(rotated)
        assert access == "at-2"
        assert refresh == "rt-keep"  # unchanged

    async def test_rotate_returns_none_when_no_existing_row(self, db):
        server_id = await _make_server(db)
        result = await MCPServerOAuthTokenRepo.rotate(
            db,
            TEST_ORG_ID,
            mcp_server_id=server_id,
            access_token="at-new",
            refresh_token="rt-new",
            expires_at=None,
        )
        assert result is None


# ---------------------------------------------------------------------------
# Sweep queries + delete
# ---------------------------------------------------------------------------


class TestSweepAndDelete:
    async def test_list_expiring_before_filters_to_expiring_rows(self, db):
        s1 = await _make_server(db)
        s2_obj = await MCPServerRepo.create(
            db,
            TEST_ORG_ID,
            name="server-2",
            transport="http",
            url="https://mcp2.example.com/mcp",
        )
        await db.commit()

        now = datetime.now(timezone.utc)
        await MCPServerOAuthTokenRepo.upsert(
            db, TEST_ORG_ID,
            mcp_server_id=s1,
            access_token="at-expiring",
            refresh_token=None,
            expires_at=now + timedelta(minutes=2),  # expires soon
        )
        await MCPServerOAuthTokenRepo.upsert(
            db, TEST_ORG_ID,
            mcp_server_id=s2_obj.id,
            access_token="at-fresh",
            refresh_token=None,
            expires_at=now + timedelta(hours=2),  # not expiring
        )
        await db.commit()

        cutoff = now + timedelta(minutes=5)
        rows = await MCPServerOAuthTokenRepo.list_expiring_before(
            db, TEST_ORG_ID, cutoff=cutoff
        )
        assert len(rows) == 1
        assert rows[0].mcp_server_id == s1

    async def test_list_expiring_before_excludes_rows_with_null_expiry(self, db):
        server_id = await _make_server(db)
        await MCPServerOAuthTokenRepo.upsert(
            db, TEST_ORG_ID,
            mcp_server_id=server_id,
            access_token="at-no-expiry",
            refresh_token="rt-no-expiry",
            expires_at=None,  # AS didn't include expires_in
        )
        await db.commit()

        far_future = datetime.now(timezone.utc) + timedelta(days=365)
        rows = await MCPServerOAuthTokenRepo.list_expiring_before(
            db, TEST_ORG_ID, cutoff=far_future
        )
        assert rows == []

    async def test_delete_removes_row(self, db):
        server_id = await _make_server(db)
        await MCPServerOAuthTokenRepo.upsert(
            db, TEST_ORG_ID,
            mcp_server_id=server_id,
            access_token="at",
            refresh_token="rt",
            expires_at=None,
        )
        await db.commit()

        deleted = await MCPServerOAuthTokenRepo.delete_for_server(
            db, TEST_ORG_ID, server_id
        )
        await db.commit()
        assert deleted is True

        gone = await MCPServerOAuthTokenRepo.get_for_server(
            db, TEST_ORG_ID, server_id
        )
        assert gone is None

    async def test_delete_returns_false_when_no_row(self, db):
        server_id = await _make_server(db)
        deleted = await MCPServerOAuthTokenRepo.delete_for_server(
            db, TEST_ORG_ID, server_id
        )
        assert deleted is False


# ---------------------------------------------------------------------------
# Cascade
# ---------------------------------------------------------------------------


class TestCascade:
    async def test_token_row_is_deleted_when_mcp_server_is_deleted(self, db):
        server_id = await _make_server(db)
        await MCPServerOAuthTokenRepo.upsert(
            db, TEST_ORG_ID,
            mcp_server_id=server_id,
            access_token="at",
            refresh_token="rt",
            expires_at=None,
        )
        await db.commit()

        # Sanity — row is there.
        before = await MCPServerOAuthTokenRepo.get_for_server(
            db, TEST_ORG_ID, server_id
        )
        assert before is not None

        await MCPServerRepo.delete(db, TEST_ORG_ID, server_id)
        await db.commit()

        # SQLite enforces ON DELETE CASCADE only when foreign keys are
        # enabled (PRAGMA foreign_keys = ON). SQLAlchemy enables them by
        # default for aiosqlite, so the cascade should fire.
        after = await MCPServerOAuthTokenRepo.get_for_server(
            db, TEST_ORG_ID, server_id
        )
        assert after is None
