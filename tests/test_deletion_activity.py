"""M1-18: permanent deletion leaves Activity behind (EC-D02, D-031).

Each deleted incident gets one Activity entry (actor, incident id and title),
written in the same transaction as the deletion. The incident's earlier
session entries stay, detached from the deleted session rows.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from backend.db.models import AuditEntry, Incident, Session
from backend.db.repos import AuditEntryRepo, IncidentRepo, SessionRepo
from tests.test_ownership_lifecycle import (
    TEST_ORG_ID,
    _headers,
    app as _app_fixture,
    client as _client_fixture,
    world as _world_fixture,
)

app = pytest.fixture(_app_fixture.__wrapped__)
client = pytest.fixture(_client_fixture.__wrapped__)
world = pytest.fixture(_world_fixture.__wrapped__)


async def _incident_with_session(w, title: str) -> tuple[uuid.UUID, uuid.UUID]:
    """An incident with one AI session and two session Activity entries."""
    async with w.app.state.session_factory() as db:
        incident = await IncidentRepo.create(
            db, TEST_ORG_ID, title=title, description="Synthetic deletion check"
        )
        session = await SessionRepo.create(
            db, TEST_ORG_ID, tier=1, incident_id=incident.id, status="completed"
        )
        for entry_type in ("session_start", "session_end"):
            await AuditEntryRepo.create(
                db,
                TEST_ORG_ID,
                session_id=session.id,
                tier=1,
                entry_type=entry_type,
                result={"ok": True},
            )
        await db.commit()
        return incident.id, session.id


async def _rows(w, model, *criteria):
    async with w.app.state.session_factory() as db:
        return list((await db.execute(select(model).where(*criteria))).scalars())


async def _deletion_entries(w):
    return await _rows(w, AuditEntry, AuditEntry.entry_type == "incident_deleted")


async def test_single_delete_records_one_entry_and_keeps_session_activity(world):
    incident_id, session_id = await _incident_with_session(world, "Disk full on db-1")

    resp = await world.client.delete(f"/incidents/{incident_id}", headers=world.admin)

    assert resp.status_code == 204, resp.text
    assert await _rows(world, Incident, Incident.id == incident_id) == []
    assert await _rows(world, Session, Session.id == session_id) == []
    (entry,) = await _deletion_entries(world)
    assert entry.session_id is None and entry.tool_name == "delete_incident"
    assert entry.tool_parameters["incident_id"] == str(incident_id)
    assert entry.tool_parameters["title"] == "Disk full on db-1"
    assert entry.tool_parameters["actor_id"] == str(world.admin_id)
    kept = await _rows(
        world, AuditEntry, AuditEntry.entry_type.in_(("session_start", "session_end"))
    )
    assert len(kept) == 2 and all(row.session_id is None for row in kept)


async def test_bulk_delete_records_one_entry_per_incident(world):
    first, _ = await _incident_with_session(world, "Queue backlog")
    second, _ = await _incident_with_session(world, "Cache misses")

    resp = await world.client.post(
        "/incidents/bulk",
        json={"action": "delete", "incident_ids": [str(first), str(second)]},
        headers=world.admin,
    )

    assert resp.status_code == 200, resp.text
    assert await _rows(world, Incident, Incident.id.in_((first, second))) == []
    entries = await _deletion_entries(world)
    assert {e.tool_parameters["incident_id"] for e in entries} == {
        str(first),
        str(second),
    }
    assert {e.tool_name for e in entries} == {"bulk_delete_incidents"}
    kept = await _rows(
        world, AuditEntry, AuditEntry.entry_type.in_(("session_start", "session_end"))
    )
    assert len(kept) == 4 and all(row.session_id is None for row in kept)


async def test_operator_cannot_delete_and_nothing_is_recorded(world):
    incident_id, session_id = await _incident_with_session(world, "Operator attempt")

    resp = await world.client.delete(
        f"/incidents/{incident_id}", headers=await _headers(world.client, "lc-l1")
    )

    assert resp.status_code == 403
    assert len(await _rows(world, Incident, Incident.id == incident_id)) == 1
    assert await _deletion_entries(world) == []
    attached = await _rows(world, AuditEntry, AuditEntry.session_id == session_id)
    assert len(attached) == 2
