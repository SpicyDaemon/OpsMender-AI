"""M1-108: a session that cannot act says so.

The transcript line that a session runs advisory-only was written only when
the incident's service existed and none of its MCP servers or connectors was
usable; a session whose incident has no service said nothing. It is now
written whenever no tool source is attached.
"""

from __future__ import annotations

# ruff: noqa: F811

from types import SimpleNamespace

from backend.api import session_runner
from backend.db.repos import (
    IncidentRepo,
    ServiceRepo,
    SessionMessageRepo,
    SessionRepo,
    TeamRepo,
)
from tests.test_tool_source_overlap import TEST_ORG_ID, app  # noqa: F401


class _FakeGraph:
    async def ainvoke(self, state, *args, **kwargs):
        return {"status": "completed", "summary": "ok"}


async def _run(app, monkeypatch, session_id):
    published: list[tuple[str, dict]] = []

    async def _capture(sid, message):
        published.append((message.type, dict(message.data)))

    async def _no_llm(factory, session):
        return SimpleNamespace()

    monkeypatch.setattr(session_runner, "publish", _capture)
    monkeypatch.setattr(session_runner, "_resolve_llm", _no_llm)
    monkeypatch.setattr(session_runner, "build_graph", lambda **kwargs: _FakeGraph())
    app.state.workflow_start_delay_seconds = 0

    await session_runner._run_session_workflow_inner(app, session_id=session_id)

    async with app.state.session_factory() as db:
        messages = await SessionMessageRepo.list_by_session(db, TEST_ORG_ID, session_id)
        stored = await SessionRepo.get_by_id(db, TEST_ORG_ID, session_id)
    assert stored.status == "completed", stored.status
    return published, [m for m in messages if m.node_context == "tool_availability"]


async def test_a_session_whose_incident_has_no_service_says_it_runs_advisory_only(
    app, monkeypatch
):
    async with app.state.session_factory() as db:
        incident = await IncidentRepo.create(
            db, TEST_ORG_ID, title="Orphan 500s", description="no service"
        )
        session = await SessionRepo.create(
            db, TEST_ORG_ID, tier=2, incident_id=incident.id
        )
        await db.commit()
        session_id = session.id

    published, lines = await _run(app, monkeypatch, session_id)

    [line] = lines
    assert line.role == "assistant"
    assert "no service" in line.content and "advisory-only" in line.content
    # The dashboard hears it as an assistant line, like the service case.
    assert any(
        event_type == "chat_message_assistant"
        and data.get("node_context") == "tool_availability"
        for event_type, data in published
    )


async def test_a_service_without_tool_sources_still_says_so(app, monkeypatch):
    async with app.state.session_factory() as db:
        team = await TeamRepo.create(
            db, TEST_ORG_ID, name="Team none", slug="team-none"
        )
        service = await ServiceRepo.create(
            db,
            TEST_ORG_ID,
            team_id=team.id,
            name="svc-none",
            slug="svc-none",
            mcp_server_ids=[],
            allowed_integration_connector_ids=[],
        )
        incident = await IncidentRepo.create(
            db, TEST_ORG_ID, title="500s", description="d", service_id=service.id
        )
        session = await SessionRepo.create(
            db, TEST_ORG_ID, tier=2, incident_id=incident.id
        )
        await db.commit()
        session_id = session.id

    _published, lines = await _run(app, monkeypatch, session_id)

    [line] = lines
    assert "for this service" in line.content and "advisory-only" in line.content
