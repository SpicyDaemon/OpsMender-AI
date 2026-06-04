"""Part 5 — a deactivated user is excluded from on-call resolution + paging.

The membership row is preserved (history), but ``list_members(active_only=True)``
— used by on-call resolution and the escalation pager — filters it out, so a
disabled user never resolves as on-call or gets paged.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.db.models import Base, Organization, Team, User
from backend.db.repos import RosterRepo, UserRepo

ORG_ID = uuid.UUID("00000000-0000-0000-0000-0000000000aa")


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(Organization(id=ORG_ID, name="Org", slug="org"))
        await session.commit()
    async with factory() as session:
        yield session
    await engine.dispose()


async def _user(db, username: str) -> User:
    u = await UserRepo.create(
        db,
        username=username,
        email=f"{username}@test.com",
        password_hash="x",
        role="operator",
        primary_org_id=ORG_ID,
    )
    await db.flush()
    return u


@pytest.mark.asyncio
async def test_deactivated_user_excluded_from_active_roster_members(db):
    team = Team(id=uuid.uuid4(), org_id=ORG_ID, name="T", slug="t")
    db.add(team)
    await db.flush()
    roster = await RosterRepo.create(
        db, ORG_ID, team_id=team.id, name="R", anchor_date=date(2026, 1, 1)
    )
    alice = await _user(db, "alice")
    bob = await _user(db, "bob")
    await RosterRepo.add_member(
        db, ORG_ID, roster_id=roster.id, user_id=alice.id, position_index=0
    )
    await RosterRepo.add_member(
        db, ORG_ID, roster_id=roster.id, user_id=bob.id, position_index=1
    )
    await db.commit()

    # Both visible to admin display.
    everyone = await RosterRepo.list_members(db, ORG_ID, roster.id)
    assert {m.user_id for m in everyone} == {alice.id, bob.id}

    # Deactivate alice.
    await UserRepo.update_fields(db, alice.id, is_active=False)
    await db.commit()

    # On-call / paging materialization excludes her; the row is preserved.
    active = await RosterRepo.list_members(db, ORG_ID, roster.id, active_only=True)
    assert {m.user_id for m in active} == {bob.id}
    still_there = await RosterRepo.list_members(db, ORG_ID, roster.id)
    assert {m.user_id for m in still_there} == {alice.id, bob.id}


@pytest.mark.asyncio
async def test_all_deactivated_leaves_no_active_on_call(db):
    team = Team(id=uuid.uuid4(), org_id=ORG_ID, name="T2", slug="t2")
    db.add(team)
    await db.flush()
    roster = await RosterRepo.create(
        db, ORG_ID, team_id=team.id, name="R2", anchor_date=date(2026, 1, 1)
    )
    solo = await _user(db, "solo")
    await RosterRepo.add_member(
        db, ORG_ID, roster_id=roster.id, user_id=solo.id, position_index=0
    )
    await db.commit()

    await UserRepo.update_fields(db, solo.id, is_active=False)
    await db.commit()

    active = await RosterRepo.list_members(db, ORG_ID, roster.id, active_only=True)
    assert active == []  # roster has no active on-call → resolver yields None
