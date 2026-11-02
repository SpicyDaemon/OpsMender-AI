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
