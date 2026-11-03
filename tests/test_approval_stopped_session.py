"""M1-105: an approval wait does not bring a stopped session back.

The dashboard's Stop marks the session stopped and expires its pending
approvals in one commit. A wait that then saw the expired request turned the
row into timed_out (or active, for an answer that raced the stop), which hid
the stop and, for a CLI run in another process, let the plan go on.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.approvals import ApprovalNotAsked
from backend.approvals.service import ApprovalService
from backend.db.bootstrap import initialize_sqlite_schema
from backend.db.models import ApprovalRequest, Organization, Session
from backend.db.repos import SessionRepo


@pytest.fixture
async def factory(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'a.db').as_posix()}"
    )
    await initialize_sqlite_schema(engine)
    made = async_sessionmaker(engine, expire_on_commit=False)
    yield made
    await engine.dispose()


async def _session(factory) -> tuple[uuid.UUID, uuid.UUID]:
    org_id = uuid.uuid4()
    async with factory() as db:
        db.add(Organization(id=org_id, name="Stop test", slug="stop-test"))
        await db.flush()
        session = await SessionRepo.create(db, org_id, tier=1)
        await db.commit()
        return org_id, session.id


async def _stop(factory, session_id: uuid.UUID, request_status: str) -> None:
    """The stop route's commit: the row stopped, the pending approval ended."""
    async with factory() as db:
        await db.execute(
            Session.__table__.update()
            .where(Session.id == session_id)
            .values(status="stopped")
        )
        await db.execute(
            ApprovalRequest.__table__.update()
            .where(ApprovalRequest.session_id == session_id)
            .values(status=request_status)
        )
        await db.commit()


@pytest.mark.parametrize("request_status", ["expired", "approved"])
async def test_a_stopped_session_stays_stopped_after_the_wait(factory, request_status):
    org_id, session_id = await _session(factory)
    announced: list[str] = []
    service = ApprovalService(
        factory,
        org_id=org_id,
        poll_interval_seconds=0.02,
        status_notifier=lambda sid, status: announced.append(status),
    )

    async def stop_once_asked():
        while True:
            async with factory() as db:
                pending = (await db.execute(select(ApprovalRequest))).scalars().all()
            if pending:
                break
            await asyncio.sleep(0.02)
        await _stop(factory, session_id, request_status)

    stopper = asyncio.ensure_future(stop_once_asked())
    resolution = await service.request_and_wait(
        session_id=session_id,
        action={"tool_name": "restart_pod", "tool_parameters": {}},
    )
    await stopper

    assert resolution.request.status == request_status
    async with factory() as db:
        row = await SessionRepo.get_by_id(db, org_id, session_id)
    assert row.status == "stopped"
    # Only the wait's own "awaiting_approval" was announced, not timed_out or
    # active for a session a person stopped.
    assert announced == ["awaiting_approval"]


async def test_a_running_session_still_times_out_on_expiry(factory):
    org_id, session_id = await _session(factory)
    service = ApprovalService(
        factory, org_id=org_id, timeout_seconds=0, poll_interval_seconds=0.02
    )
    resolution = await service.request_and_wait(
        session_id=session_id,
        action={"tool_name": "restart_pod", "tool_parameters": {}},
    )
    assert resolution.request.status == "expired"
    async with factory() as db:
        row = await SessionRepo.get_by_id(db, org_id, session_id)
    assert row.status == "timed_out"


async def test_a_session_stopped_before_the_request_is_never_asked(factory):
    # The Stop committed after the caller last read the row: the service's
    # own write finds it stopped, keeps no request and announces nothing.
    org_id, session_id = await _session(factory)
    async with factory() as db:
        await db.execute(
            Session.__table__.update()
            .where(Session.id == session_id)
            .values(status="stopped")
        )
        await db.commit()
    announced: list[str] = []
    service = ApprovalService(
        factory,
        org_id=org_id,
        poll_interval_seconds=0.02,
        status_notifier=lambda sid, status: announced.append(status),
    )

    with pytest.raises(ApprovalNotAsked) as exc_info:
        await service.request_and_wait(
            session_id=session_id,
            action={"tool_name": "restart_pod", "tool_parameters": {}},
        )

    assert str(exc_info.value) == "the session was stopped; the approval was not asked"
    async with factory() as db:
        assert (await db.execute(select(ApprovalRequest))).scalars().all() == []
        row = await SessionRepo.get_by_id(db, org_id, session_id)
    assert row.status == "stopped"
    assert announced == []


async def test_a_stop_that_missed_the_request_still_ends_the_wait(factory):
    # The Stop committed as the request was written, so it expired nothing:
    # the wait sees the stopped row, expires the request itself and returns
    # long before the approval would time out.
    org_id, session_id = await _session(factory)
    service = ApprovalService(
        factory, org_id=org_id, timeout_seconds=900, poll_interval_seconds=0.02
    )

    async def stop_row_only():
        while True:
            async with factory() as db:
                pending = (await db.execute(select(ApprovalRequest))).scalars().all()
            if pending:
                break
            await asyncio.sleep(0.02)
        async with factory() as db:
            await db.execute(
                Session.__table__.update()
                .where(Session.id == session_id)
                .values(status="stopped")
            )
            await db.commit()

    stopper = asyncio.ensure_future(stop_row_only())
    resolution = await asyncio.wait_for(
        service.request_and_wait(
            session_id=session_id,
            action={"tool_name": "restart_pod", "tool_parameters": {}},
        ),
        timeout=10,
    )
    await stopper

    assert resolution.request.status == "expired"
    assert resolution.block_reason == "the session was stopped during the approval wait"
    async with factory() as db:
        row = await SessionRepo.get_by_id(db, org_id, session_id)
    assert row.status == "stopped"


async def test_a_service_bound_to_a_tier_leaves_an_overridden_session_alone(factory):
    # The dashboard's Override moves the session to Tier 2 for the app to run.
    # A service that drives it at Tier 1 (the CLI's) ends its wait, writes
    # nothing over the row and asks nothing more.
    org_id, session_id = await _session(factory)
    service = ApprovalService(
        factory,
        org_id=org_id,
        timeout_seconds=900,
        poll_interval_seconds=0.02,
        session_tier=1,
    )

    async def override_once_asked():
        while True:
            async with factory() as db:
                pending = (await db.execute(select(ApprovalRequest))).scalars().all()
            if pending:
                break
            await asyncio.sleep(0.02)
        async with factory() as db:
            await db.execute(
                ApprovalRequest.__table__.update()
                .where(ApprovalRequest.session_id == session_id)
                .values(status="expired")
            )
            await db.execute(
                Session.__table__.update()
                .where(Session.id == session_id)
                .values(status="active", tier=2)
            )
            await db.commit()

    overrider = asyncio.ensure_future(override_once_asked())
    resolution = await asyncio.wait_for(
        service.request_and_wait(
            session_id=session_id,
            action={"tool_name": "restart_pod", "tool_parameters": {}},
        ),
        timeout=10,
    )
    await overrider

    assert resolution.request.status == "expired"
    assert resolution.block_reason == (
        "the session was overridden to Tier 2 during the approval wait"
    )
    async with factory() as db:
        row = await SessionRepo.get_by_id(db, org_id, session_id)
    assert (row.status, row.tier) == ("active", 2)

    with pytest.raises(ApprovalNotAsked) as exc_info:
        await service.request_and_wait(
            session_id=session_id,
            action={"tool_name": "restart_pod", "tool_parameters": {}},
        )
    assert str(exc_info.value) == (
        "the session was overridden to Tier 2; the approval was not asked"
    )
    async with factory() as db:
        requests = (await db.execute(select(ApprovalRequest))).scalars().all()
    assert [r.status for r in requests] == ["expired"]
