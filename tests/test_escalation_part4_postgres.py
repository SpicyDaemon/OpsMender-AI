"""Real two-connection PostgreSQL claim and mutator interleavings.

Run with PART4_PG_URL pointed at a disposable database. The URL is not logged.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.db.models import Base, IncidentPage, Organization
from backend.db.repos import (
    EscalationChainRepo,
    IncidentResponderRepo,
    EscalationStepRepo,
    IncidentPageRepo,
    IncidentRepo,
    TeamRepo,
    UserRepo,
)
from backend.paging import escalation as esc
from backend.paging import responders

pytestmark = pytest.mark.integration


@pytest.fixture
async def pg_fixture():
    url = os.environ.get("PART4_PG_URL")
    if not url:
        pytest.fail("PART4_PG_URL must target an isolated PostgreSQL database")
    engine = create_async_engine(url, pool_size=4)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    org_id = uuid.uuid4()
    async with factory() as db:
        db.add(
            Organization(id=org_id, name="Part 4 race", slug=f"race-{org_id.hex[:8]}")
        )
        users = []
        for index in range(3):
            user = await UserRepo.create(
                db,
                username=f"race-{org_id.hex[:8]}-{index}",
                email=f"race-{org_id.hex[:8]}-{index}@example.test",
                password_hash="x",
                role="operator",
                primary_org_id=org_id,
            )
            users.append(user.id)
        team = await TeamRepo.create(
            db, org_id, name="Race", slug=f"race-{org_id.hex[:8]}"
        )
        chain = await EscalationChainRepo.create(
            db, org_id, team_id=team.id, name="Race"
        )
        for index, user_id in enumerate(users):
            await EscalationStepRepo.create(
                db,
                org_id,
                chain_id=chain.id,
                step_index=index,
                target_type="user",
                target_id=user_id,
                timeout_seconds=20,
            )
        await db.commit()
    yield factory, org_id, chain.id, users
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def _incident(factory, org_id, chain_id, at):
    async with factory() as db:
        incident = await IncidentRepo.create(
            db, org_id, title="race", description="test"
        )
        await esc.start_chain(
            db, org_id, incident_id=incident.id, chain_id=chain_id, at=at
        )
        await db.commit()
        return incident.id


async def _markers(factory, incident_id):
    async with factory() as db:
        return (
            (
                await db.execute(
                    select(IncidentPage).where(
                        IncidentPage.incident_id == incident_id,
                        IncidentPage.channel == "recorded",
                    )
                )
            )
            .scalars()
            .all()
        )


@pytest.mark.asyncio
async def test_two_warmed_scheduler_connections_claim_once(pg_fixture, monkeypatch):
    factory, org_id, chain_id, users = pg_fixture
    now = datetime.now(timezone.utc)
    incident_id = await _incident(
        factory, org_id, chain_id, now - timedelta(seconds=25)
    )
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def fake_dispatch(db, org_id, *, incident, user, page, **_):
        calls.append((page.step_index, page.round, user.id))
        entered.set()
        await release.wait()
        await IncidentPageRepo.create(
            db,
            org_id,
            incident_id=incident.id,
            user_id=user.id,
            chain_id=page.chain_id,
            step_index=page.step_index,
            round=page.round,
            channel="email",
            delivery_status="sent",
        )
        return SimpleNamespace(attempts=[SimpleNamespace(status="sent")])

    monkeypatch.setattr(esc, "dispatch_page", fake_dispatch)

    async def worker(started):
        async with factory() as db:
            await db.execute(text("SELECT 1"))
            started.set()
            changed = await esc.tick_all_due(
                db,
                at=now,
                channel_factory=lambda *_: None,
            )
            await db.commit()
            return changed

    started_a, started_b = asyncio.Event(), asyncio.Event()
    task_a = asyncio.create_task(worker(started_a))
    await started_a.wait()
    await asyncio.wait_for(entered.wait(), timeout=10)
    task_b = asyncio.create_task(worker(started_b))
    await started_b.wait()
    try:
        assert await asyncio.wait_for(task_b, timeout=5) == 0
    finally:
        release.set()
    assert await asyncio.wait_for(task_a, timeout=10) == 1
    markers = await _markers(factory, incident_id)
    assert sorted((p.step_index, p.round, p.user_id) for p in markers) == [
        (0, 0, users[0]),
        (1, 0, users[1]),
    ]
    assert calls == [(1, 0, users[1])]


@pytest.mark.asyncio
async def test_scheduler_serializes_explicit_escalation_and_handoff(
    pg_fixture, monkeypatch
):
    factory, org_id, chain_id, users = pg_fixture
    now = datetime.now(timezone.utc)
    incident_id = await _incident(
        factory, org_id, chain_id, now - timedelta(seconds=25)
    )
    entered, release = asyncio.Event(), asyncio.Event()

    async def fake_dispatch(db, org_id, *, incident, user, page, **_):
        if page.step_index == 1:
            entered.set()
            await release.wait()
        return SimpleNamespace(attempts=[SimpleNamespace(status="sent")])

    monkeypatch.setattr(esc, "dispatch_page", fake_dispatch)

    async with factory() as a, factory() as b:
        await a.execute(text("SELECT 1"))
        await b.execute(text("SELECT 1"))

        async def scheduler():
            result = await esc.tick_all_due(a, at=now, channel_factory=lambda *_: None)
            await a.commit()
            return result

        task_a = asyncio.create_task(scheduler())
        await asyncio.wait_for(entered.wait(), timeout=10)
        task_b = asyncio.create_task(
            esc.escalate_now(
                b,
                org_id,
                incident_id=incident_id,
                at=now + timedelta(seconds=1),
                channel_factory=lambda *_: None,
            )
        )
        await asyncio.sleep(0.15)
        assert not task_b.done()
        release.set()
        assert await task_a == 1
        result = await asyncio.wait_for(task_b, timeout=10)
        await b.commit()
        assert result.step_index == 2

    markers = await _markers(factory, incident_id)
    assert sorted(p.step_index for p in markers) == [0, 1, 2]
    async with factory() as db:
        await esc.restart_chain_for_handoff(
            db,
            org_id,
            incident_id=incident_id,
            chain_id=chain_id,
            at=now + timedelta(seconds=2),
        )
        await db.commit()
    markers = await _markers(factory, incident_id)
    assert [(p.step_index, p.round) for p in markers if p.round == 1] == [(0, 1)]


@pytest.mark.asyncio
async def test_concurrent_responder_adds_never_pass_the_limit(pg_fixture, monkeypatch):
    """Two admins adding two responders each at once: one wins, one is refused."""
    factory, org_id, chain_id, users = pg_fixture
    incident_id = await _incident(factory, org_id, chain_id, datetime.now(timezone.utc))
    async with factory() as db:
        helpers = []
        for index in range(4):
            helper = await UserRepo.create(
                db,
                username=f"helper-{org_id.hex[:8]}-{index}",
                email=f"helper-{org_id.hex[:8]}-{index}@example.test",
                password_hash="x",
                role="operator",
                primary_org_id=org_id,
            )
            helpers.append(helper.id)
        await db.commit()

    first_read, go = asyncio.Event(), asyncio.Event()
    original = IncidentResponderRepo.list_for_incident

    async def paused_list(db, org_id, incident_id):
        rows = await original(db, org_id, incident_id)
        if not first_read.is_set():
            # The first add holds here after reading who is already on it.
            first_read.set()
            await go.wait()
        return rows

    monkeypatch.setattr(
        responders.IncidentResponderRepo, "list_for_incident", staticmethod(paused_list)
    )

    async def add(pair):
        async with factory() as db:
            incident = await IncidentRepo.get_by_id(db, org_id, incident_id)
            actor = await UserRepo.get_by_id(db, users[0])
            try:
                await responders.add_responders(
                    db, org_id, incident=incident, user_ids=pair, actor=actor
                )
                await db.commit()
                return "added"
            except responders.ResponderError as exc:
                await db.rollback()
                return exc.status_code

    first = asyncio.create_task(add(helpers[:2]))
    await asyncio.wait_for(first_read.wait(), timeout=10)
    second = asyncio.create_task(add(helpers[2:]))
    await asyncio.sleep(0.5)
    go.set()
    results = await asyncio.wait_for(asyncio.gather(first, second), timeout=30)

    async with factory() as db:
        rows = await original(db, org_id, incident_id)
    assert sorted(str(result) for result in results) == ["409", "added"]
    assert len(rows) == 2
