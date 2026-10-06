"""Responder slots and requests on real PostgreSQL connections (EC-P04).

Adds and requests to join share the limit of 3 under the incident row lock,
each person has at most one pending request, and an answer, expiry or close
decides a request once. One connection holds its transaction open while the
other waits, so the overlap is real. Run with PART4_PG_URL pointed at a
disposable database. The URL is not logged.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.db.models import (
    Base,
    IncidentComment,
    IncidentResponder,
    IncidentResponderRequest,
    Organization,
)
from backend.db.repos import IncidentRepo, ServiceRepo, TeamRepo, UserRepo
from backend.paging import responders as rs

pytestmark = pytest.mark.integration

PEOPLE = ("lead", "mate1", "mate2", "mate3", "mate4", "other1", "other2")


def _no_channels(_key):
    return None


@pytest.fixture
async def pg():
    url = os.environ.get("PART4_PG_URL")
    if not url:
        pytest.fail("PART4_PG_URL must target an isolated PostgreSQL database")
    engine = create_async_engine(url, pool_size=6)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    org_id = uuid.uuid4()
    tag = org_id.hex[:8]
    async with factory() as db:
        db.add(Organization(id=org_id, name="Responder slots", slug=f"slots-{tag}"))
        await db.flush()
        people = {}
        for name in PEOPLE:
            people[name] = await UserRepo.create(
                db,
                username=f"slots-{tag}-{name}",
                email=f"slots-{tag}-{name}@example.test",
                password_hash="x",
                role="operator",
                primary_org_id=org_id,
            )
        platform = await TeamRepo.create(
            db, org_id, name=f"Platform {tag}", slug=f"platform-{tag}"
        )
        data = await TeamRepo.create(db, org_id, name=f"Data {tag}", slug=f"data-{tag}")
        for name in ("lead", "mate1", "mate2", "mate3", "mate4"):
            await TeamRepo.add_member(db, org_id, platform.id, user_id=people[name].id)
        for name in ("other1", "other2"):
            await TeamRepo.add_member(db, org_id, data.id, user_id=people[name].id)
        service = await ServiceRepo.create(
            db,
            org_id,
            team_id=platform.id,
            name=f"Orders {tag}",
            slug=f"orders-{tag}",
            priority="P1",
        )
        incident = await IncidentRepo.create(
            db,
            org_id,
            title="orders db is slow",
            description="slots",
            priority="P1",
            response_mode="page",
            service_id=service.id,
        )
        await db.commit()
    yield factory, org_id, people, incident
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await engine.dispose()


async def _held(task: asyncio.Task) -> bool:
    """The second connection is still waiting on the first one's lock."""
    await asyncio.sleep(0.5)
    return not task.done()


async def _state(factory, incident_id) -> tuple[int, dict[str, int], int]:
    async with factory() as db:
        responders = await db.scalar(
            select(func.count())
            .select_from(IncidentResponder)
            .where(IncidentResponder.incident_id == incident_id)
        )
        rows = (
            await db.execute(
                select(IncidentResponderRequest.status, func.count())
                .where(IncidentResponderRequest.incident_id == incident_id)
                .group_by(IncidentResponderRequest.status)
            )
        ).all()
        expired_notes = await db.scalar(
            select(func.count())
            .select_from(IncidentComment)
            .where(
                IncidentComment.incident_id == incident_id,
                IncidentComment.body.like("%expired after 30 minutes%"),
            )
        )
    return responders, dict(rows), expired_notes


async def test_a_removal_frees_the_slot_for_an_add_waiting_on_it(pg):
    factory, org_id, people, incident = pg
    lead = people["lead"]
    async with factory() as db:
        await rs.add_responders(
            db,
            org_id,
            incident=incident,
            user_ids=[people[name].id for name in ("mate1", "mate2", "mate3")],
            actor=lead,
            channel_factory=_no_channels,
        )
        await db.commit()

    async with factory() as one, factory() as two:
        assert await rs.remove_responder(
            one, org_id, incident=incident, user_id=people["mate1"].id, actor=lead
        )
        task = asyncio.create_task(
            rs.add_responders(
                two,
                org_id,
                incident=incident,
                user_ids=[people["mate4"].id],
                actor=lead,
                channel_factory=_no_channels,
            )
        )
        assert await _held(task)
        await one.commit()
        await task
        await two.commit()

    async with factory() as db:
        rows = (
            await db.execute(
                select(IncidentResponder.user_id).where(
                    IncidentResponder.incident_id == incident.id
                )
            )
        ).scalars()
        assert set(rows) == {people[name].id for name in ("mate2", "mate3", "mate4")}


@pytest.mark.parametrize("first", ["request", "add"])
async def test_an_add_and_a_request_race_for_the_last_slot(pg, first):
    factory, org_id, people, incident = pg
    lead = people["lead"]
    async with factory() as db:
        await rs.add_responders(
            db,
            org_id,
            incident=incident,
            user_ids=[people["mate1"].id, people["mate2"].id],
            actor=lead,
            channel_factory=_no_channels,
        )
        await db.commit()

    async def add(db):
        return await rs.add_responders(
            db,
            org_id,
            incident=incident,
            user_ids=[people["mate3"].id],
            actor=lead,
            channel_factory=_no_channels,
        )

    async def ask(db):
        return await rs.request_responders(
            db, org_id, incident=incident, user_ids=[people["other1"].id], actor=lead
        )

    calls = {"add": add, "request": ask}
    second = "add" if first == "request" else "request"
    async with factory() as one, factory() as two:
        await calls[first](one)
        task = asyncio.create_task(calls[second](two))
        assert await _held(task)
        await one.commit()
        with pytest.raises(rs.ResponderError) as refused:
            await task
        await two.rollback()

    assert refused.value.status_code == 409
    responders, requests, _ = await _state(factory, incident.id)
    if first == "add":
        assert (responders, requests) == (3, {})
    else:
        assert (responders, requests) == (2, {"pending": 1})


async def test_two_requests_for_one_person_leave_one_pending(pg):
    factory, org_id, people, incident = pg
    lead, other = people["lead"], people["other1"]

    async def ask(db, actor):
        return await rs.request_responders(
            db, org_id, incident=incident, user_ids=[other.id], actor=actor
        )

    async with factory() as one, factory() as two:
        await ask(one, lead)
        task = asyncio.create_task(ask(two, people["mate1"]))
        assert await _held(task)
        await one.commit()
        with pytest.raises(rs.ResponderError) as refused:
            await task
        await two.rollback()
    assert refused.value.status_code == 409
    assert refused.value.detail == f"{other.username} was already asked."

    # The partial unique index holds even without the lock.
    async with factory() as db:
        db.add(
            IncidentResponderRequest(
                org_id=org_id,
                incident_id=incident.id,
                user_id=other.id,
                requested_by=lead.id,
                status="pending",
                expires_at=datetime.now(timezone.utc) + rs.REQUEST_TTL,
            )
        )
        with pytest.raises(IntegrityError):
            await db.flush()
        await db.rollback()
    assert (await _state(factory, incident.id))[1] == {"pending": 1}


@pytest.mark.parametrize("first", ["accept", "expire"])
async def test_an_answer_and_expiry_decide_a_request_once(pg, first):
    factory, org_id, people, incident = pg
    other = people["other1"]
    now = datetime.now(timezone.utc)
    async with factory() as db:
        [request] = await rs.request_responders(
            db,
            org_id,
            incident=incident,
            user_ids=[other.id],
            actor=people["lead"],
            at=now,
        )
        await db.commit()
    due = now + rs.REQUEST_TTL

    async def accept(db):
        return await rs.answer_request(
            db,
            org_id,
            incident_id=incident.id,
            request_id=request.id,
            user=other,
            accept=True,
            at=due - timedelta(seconds=1),
        )

    async def expire(db):
        return await rs.expire_due_requests(db, at=due + timedelta(seconds=1))

    async with factory() as one, factory() as two:
        if first == "accept":
            await accept(one)
            task = asyncio.create_task(expire(two))
            assert await _held(task)
            await one.commit()
            assert await task == 0
            await two.commit()
        else:
            assert await expire(one) == 1
            task = asyncio.create_task(accept(two))
            assert await _held(task)
            await one.commit()
            with pytest.raises(rs.ResponderError) as refused:
                await task
            await two.rollback()
            assert refused.value.status_code == 409

    responders, requests, expired_notes = await _state(factory, incident.id)
    if first == "accept":
        assert (responders, requests, expired_notes) == (1, {"accepted": 1}, 0)
    else:
        assert (responders, requests, expired_notes) == (0, {"expired": 1}, 1)


@pytest.mark.parametrize("first", ["accept", "resolve"])
async def test_resolving_and_accepting_decide_a_request_once(pg, first):
    factory, org_id, people, incident = pg
    other = people["other1"]
    async with factory() as db:
        [request] = await rs.request_responders(
            db, org_id, incident=incident, user_ids=[other.id], actor=people["lead"]
        )
        await db.commit()

    async def accept(db):
        return await rs.answer_request(
            db,
            org_id,
            incident_id=incident.id,
            request_id=request.id,
            user=other,
            accept=True,
        )

    async def resolve(db):
        await IncidentRepo.update_status(db, org_id, incident.id, "resolved")

    calls = {"accept": accept, "resolve": resolve}
    second = "resolve" if first == "accept" else "accept"
    async with factory() as one, factory() as two:
        await calls[first](one)
        task = asyncio.create_task(calls[second](two))
        assert await _held(task)
        await one.commit()
        if first == "accept":
            await task
            await two.commit()
        else:
            with pytest.raises(rs.ResponderError) as refused:
                await task
            await two.rollback()
            assert refused.value.status_code == 409

    responders, requests, _ = await _state(factory, incident.id)
    if first == "accept":
        assert (responders, requests) == (1, {"accepted": 1})
    else:
        assert (responders, requests) == (0, {"cancelled": 1})
    async with factory() as db:
        assert (
            await IncidentRepo.get_by_id(db, org_id, incident.id)
        ).status == "resolved"
