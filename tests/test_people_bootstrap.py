"""Tests for the Sprint 56 bootstrap-admin + SMTP + token helpers."""

from __future__ import annotations


import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config_loader import PeopleConfig, SMTPConfig
from backend.db.models import Base, Organization, User
from backend.db.repos import UserRepo
from backend.people import smtp as smtp_helper
from backend.people import tokens
from backend.people.bootstrap import bootstrap_admin


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    f = async_sessionmaker(engine, expire_on_commit=False)
    yield f
    await engine.dispose()


# ----- Tokens -----


def test_mint_round_trip_matches_hash():
    raw, h = tokens.mint()
    assert isinstance(raw, str) and len(raw) >= 30
    assert tokens.hash_token(raw) == h


def test_two_mints_are_distinct():
    raw_a, hash_a = tokens.mint()
    raw_b, hash_b = tokens.mint()
    assert raw_a != raw_b
    assert hash_a != hash_b


def test_hash_token_is_pure():
    assert tokens.hash_token("hello") == tokens.hash_token("hello")


# ----- SMTP -----


def test_smtp_not_configured_returns_false():
    cfg = SMTPConfig()  # all defaults — host + from unset
    assert not smtp_helper.is_configured(cfg)
    sent, err = smtp_helper.send_email(cfg, to="a@b.com", subject="s", body="b")
    assert sent is False
    assert err == "SMTP not configured"


def test_smtp_configured_requires_both_host_and_from():
    assert not SMTPConfig(host="smtp.example.com").configured
    assert not SMTPConfig(from_address="x@example.com").configured
    assert SMTPConfig(host="smtp.example.com", from_address="x@example.com").configured


def test_smtp_send_swallows_smtp_exception():
    """SMTP send must never raise — failures degrade to (False, error)."""
    cfg = SMTPConfig(host="invalid.host.example", from_address="x@example.com")
    sent, err = smtp_helper.send_email(cfg, to="a@b.com", subject="s", body="b")
    assert sent is False
    assert err is not None


# ----- Bootstrap admin -----


async def test_bootstrap_noop_when_env_unset_in_production(factory, monkeypatch):
    # Production mode + no bootstrap env vars → no default admin is seeded.
    monkeypatch.setenv("OPSMENDER_ENVIRONMENT", "production")
    cfg = PeopleConfig()  # bootstrap_admin_email = None
    await bootstrap_admin(factory, cfg)
    async with factory() as db:
        assert await UserRepo.list_all(db) == []


async def test_bootstrap_dev_default_admin_when_env_unset(factory, monkeypatch):
    # Development mode + no bootstrap env vars → seed admin / admin123 so the
    # documented `docker compose up` dev flow logs in out of the box.
    monkeypatch.setenv("OPSMENDER_ENVIRONMENT", "development")
    cfg = PeopleConfig()  # bootstrap not configured
    await bootstrap_admin(factory, cfg)
    async with factory() as db:
        from backend.api.auth import verify_password

        users = list(await UserRepo.list_all(db))
        assert len(users) == 1
        admin = users[0]
        assert admin.username == "admin"
        assert admin.email == "admin@localhost"
        assert admin.role == "admin"
        assert verify_password("admin123", admin.password_hash)
    # Idempotent — a second startup does not create a duplicate.
    await bootstrap_admin(factory, cfg)
    async with factory() as db:
        assert len(list(await UserRepo.list_all(db))) == 1


async def test_bootstrap_creates_admin_and_default_org(factory):
    cfg = PeopleConfig(
        bootstrap_admin_email="admin@example.com",
        bootstrap_admin_password="hunter2-strong-enough",
    )
    await bootstrap_admin(factory, cfg)
    async with factory() as db:
        users = list(await UserRepo.list_all(db))
        assert len(users) == 1
        admin = users[0]
        assert admin.email == "admin@example.com"
        assert admin.role == "admin"
        assert admin.username == "admin"
        # Default org "Main" was created and the admin is bound to it
        from backend.db.repos import OrganizationRepo

        orgs = list(await OrganizationRepo.list_all(db))
        assert len(orgs) == 1
        assert orgs[0].slug == "main"
        assert admin.primary_org_id == orgs[0].id
        assert await UserRepo.is_member(db, admin.id, orgs[0].id)


async def test_bootstrap_noop_when_users_exist(factory):
    # Seed one user with no relation to bootstrap email
    from backend.api.auth import hash_password

    async with factory() as db:
        db.add(
            User(
                username="existing",
                email="existing@example.com",
                password_hash=hash_password("anything"),
                role="operator",
            )
        )
        await db.commit()
    cfg = PeopleConfig(
        bootstrap_admin_email="admin@example.com",
        bootstrap_admin_password="hunter2-strong-enough",
    )
    await bootstrap_admin(factory, cfg)
    async with factory() as db:
        users = list(await UserRepo.list_all(db))
        # Still only one user — bootstrap refused to add another admin
        assert len(users) == 1
        assert users[0].email == "existing@example.com"


async def test_bootstrap_reuses_existing_org(factory):
    # Pre-existing org named "Already" — bootstrap should bind admin to it
    # rather than creating a second "Main" org.
    async with factory() as db:
        db.add(Organization(name="Already", slug="already"))
        await db.commit()
    cfg = PeopleConfig(
        bootstrap_admin_email="admin@example.com",
        bootstrap_admin_password="hunter2-strong-enough",
    )
    await bootstrap_admin(factory, cfg)
    async with factory() as db:
        from backend.db.repos import OrganizationRepo

        orgs = list(await OrganizationRepo.list_all(db))
        assert len(orgs) == 1
        assert orgs[0].slug == "already"
        users = list(await UserRepo.list_all(db))
        assert users[0].primary_org_id == orgs[0].id


def test_username_from_email_sanitization():
    from backend.people.bootstrap import _username_from_email

    assert _username_from_email("admin@example.com") == "admin"
    assert _username_from_email("Foo.Bar+stuff@example.com") == "foo-bar-stuff"
    assert _username_from_email("@example.com") == "admin"
    assert _username_from_email("UPPER@example.com") == "upper"
