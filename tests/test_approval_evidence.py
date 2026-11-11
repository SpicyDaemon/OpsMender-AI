"""M1-166: an approval request of a later planning round keeps what it was
written from, and every place a request can be approved says so.

The gate's side (what is asked, and when) is in tests/test_plan_follow_up.py
and the injected-alert corpus. Here: the approval service stores the evidence
with the request, and the chat list and the CLI mark such a request.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.approvals.service import ApprovalService
from backend.bots.dispatcher import _format_approval_list
from backend.db.bootstrap import initialize_sqlite_schema
from backend.db.models import ApprovalRequest, Organization
from backend.db.repos import ApprovalRequestRepo, SessionRepo
from cli.opsmender import _approval_to_dict, _format_approval

EVIDENCE = {
    "source": "tool_results",
    "round": 2,
    "calls": [
        {
            "tool_name": "get_service_health",
            "tool_parameters": {"service": "productpage"},
            "outcome": "ok",
            "round": 1,
        }
    ],
    "calls_not_shown": 0,
    "addressed": [],
    "rejected": [],
}
ACTION = {"tool_name": "restart_deployment", "tool_parameters": {"deployment": "x"}}


@pytest.fixture
async def factory(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'a.db').as_posix()}"
    )
    await initialize_sqlite_schema(engine)
    made = async_sessionmaker(engine, expire_on_commit=False)
    yield made
    await engine.dispose()


@pytest.mark.parametrize("evidence", [EVIDENCE, None])
async def test_the_service_stores_the_evidence_with_the_request(
    factory, evidence, monkeypatch
):
    notified: list[dict] = []
    published: list[dict] = []

    async def emit(db, org, users, **fields):
        notified.append(fields)

    async def publish(session_id, event):
        published.append(event)

    monkeypatch.setattr("backend.approvals.service.emit_to_users", emit)
    org_id = uuid.uuid4()
    async with factory() as db:
        db.add(Organization(id=org_id, name="Evidence", slug="evidence"))
        await db.flush()
        session = await SessionRepo.create(db, org_id, tier=1)
        await db.commit()
    service = ApprovalService(
        factory, org_id=org_id, poll_interval_seconds=0.02, publisher=publish
    )

    async def approve_once_asked():
        while True:
            async with factory() as db:
                pending = (await db.execute(select(ApprovalRequest))).scalars().all()
                if pending:
                    await ApprovalRequestRepo.resolve(
                        db, org_id, pending[0].id, status="approved"
                    )
                    await db.commit()
                    return
            await asyncio.sleep(0.02)

    asked = {"evidence": evidence} if evidence else {}
    resolution, _ = await asyncio.gather(
        service.request_and_wait(
            session_id=session.id, action=ACTION, justification="why", **asked
        ),
        approve_once_asked(),
    )

    assert resolution.request.status == "approved"
    async with factory() as db:
        (stored,) = (await db.execute(select(ApprovalRequest))).scalars().all()
        (is_null,) = (
            await db.execute(text("SELECT evidence IS NULL FROM approval_requests"))
        ).one()
    assert stored.evidence == evidence
    # A first plan's request holds SQL NULL, as every older row does.
    assert bool(is_null) == (evidence is None)
    # What runs is the action as the session planned it, nothing more.
    assert stored.action == ACTION
    # The stream event and the Inbox notification carry the mark too.
    requested = next(e for e in published if e["type"] == "approval_requested")
    assert requested["data"]["evidence"] == evidence
    (notice,) = notified
    assert notice["body"] == (
        "Written after reading tool results. why" if evidence else "why"
    )


def _request(evidence):
    return SimpleNamespace(
        id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        session_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        action=ACTION,
        justification="why",
        evidence=evidence,
        status="pending",
        requested_at=datetime(2026, 11, 11, tzinfo=timezone.utc),
        resolved_at=None,
        resolved_by=None,
        expires_at=datetime(2026, 11, 11, 0, 15, tzinfo=timezone.utc),
    )


def test_the_chat_list_marks_a_request_written_from_tool_results():
    marked, plain = _format_approval_list([_request(EVIDENCE), _request(None)]).split(
        "\n"
    )[1:]
    note = " (written after reading tool results; see the calls in OpsMender)"
    assert marked.endswith(f"action `restart_deployment`{note}")
    assert plain.endswith("action `restart_deployment`")


def test_the_cli_marks_a_request_written_from_tool_results():
    assert _format_approval(_request(EVIDENCE)).endswith(
        "tool=restart_deployment  from=tool_results  calls=1"
    )
    assert _format_approval(_request(None)).endswith("tool=restart_deployment")
    assert _approval_to_dict(_request(EVIDENCE))["evidence"] == EVIDENCE
    assert _approval_to_dict(_request(None))["evidence"] is None
