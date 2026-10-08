"""M1-31 (R-06, O-15): during a Maintenance Window a recovery still closes the
incident it clears: its chain and AI session stop and nobody is paged. A new
alert in the window is still dropped, and a recovery with nothing open still
creates nothing."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from backend.db.models import (
    Incident,
    IncidentChainState,
    IncidentPage,
    IngestLog,
    Session,
)
from backend.db.repos import MaintenanceWindowRepo, SessionRepo
from tests.test_ingest import (
    TEST_ORG_ID,
    _create_paged_service,
    admin_headers as _admin_headers_fixture,
    app as _app_fixture,
    client as _client_fixture,
)

app = pytest.fixture(_app_fixture.__wrapped__)
client = pytest.fixture(_client_fixture.__wrapped__)
admin_headers = pytest.fixture(_admin_headers_fixture.__wrapped__)

SCOPES = ["service", "team", "global"]


async def _window(app, scope: str, service: dict) -> None:
    """An approved window covering ``service`` through ``scope``, active now."""
    now = datetime.now(timezone.utc)
    scope_id = {
        "service": uuid.UUID(service["id"]),
        "team": uuid.UUID(service["team_id"]),
        "global": None,
    }[scope]
    async with app.state.session_factory() as db:
        await MaintenanceWindowRepo.create(
            db,
            TEST_ORG_ID,
            name=f"{scope} window",
            starts_at=now - timedelta(minutes=5),
            ends_at=now + timedelta(hours=1),
            scope_type=scope,
            scope_id=scope_id,
            target_ids=["*"] if scope_id is None else [str(scope_id)],
        )
        await db.commit()


async def _fire(
    client: AsyncClient, service: dict, alert_id: str, status: str = "firing"
) -> dict:
    resp = await client.post(
        service["intake_url"],
        json={
            "title": f"disk {alert_id}",
            "severity": "high",
            "id": alert_id,
            "status": status,
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _count(app, model, *criteria) -> int:
    async with app.state.session_factory() as db:
        return await db.scalar(select(func.count()).select_from(model).where(*criteria))


async def _one(app, model, *criteria):
    async with app.state.session_factory() as db:
        return (await db.execute(select(model).where(*criteria))).scalar_one()


@pytest.mark.parametrize("scope", SCOPES)
async def test_a_recovery_closes_its_incident_during_a_window(
    app, client: AsyncClient, admin_headers, scope
):
    service = await _create_paged_service(
        client, app, admin_headers, name=f"Window{scope}", priority="P1"
    )
    opened = await _fire(client, service, "disk-1")
    assert opened["dedup_action"] == "created"
    incident_id = uuid.UUID(opened["incident_id"])
    async with app.state.session_factory() as db:
        session = await SessionRepo.create(
            db, TEST_ORG_ID, tier=1, incident_id=incident_id, status="active"
        )
        await db.commit()
    pages = await _count(app, IncidentPage, IncidentPage.incident_id == incident_id)
    await _window(app, scope, service)

    cleared = await _fire(client, service, "disk-1", status="resolved")

    assert (cleared["dedup_action"], cleared["incident_id"]) == (
        "updated",
        str(incident_id),
    )
    incident = await _one(app, Incident, Incident.id == incident_id)
    assert incident.status == "resolved" and incident.resolved_at is not None
    state = await _one(
        app, IncidentChainState, IncidentChainState.incident_id == incident_id
    )
    assert state.status == "cancelled" and state.next_step_due_at is None
    stopped = await _one(app, Session, Session.id == session.id)
    assert stopped.status == "stopped"
    # Nobody was paged for the recovery.
    assert (
        await _count(app, IncidentPage, IncidentPage.incident_id == incident_id)
        == pages
    )


@pytest.mark.parametrize("scope", SCOPES)
async def test_a_new_alert_in_the_window_is_still_dropped(
    app, client: AsyncClient, admin_headers, scope
):
    service = await _create_paged_service(
        client, app, admin_headers, name=f"Quiet{scope}", priority="P1"
    )
    await _window(app, scope, service)

    dropped = await _fire(client, service, "cpu-1")

    assert dropped["dedup_action"] == "skipped"
    assert dropped.get("incident_id") is None
    service_id = uuid.UUID(service["id"])
    assert await _count(app, Incident, Incident.service_id == service_id) == 0
    log = await _one(app, IngestLog, IngestLog.dedup_action == "skipped")
    assert log.error == f"Suppressed by maintenance window: {scope} window"


async def test_a_recovery_with_nothing_open_in_the_window_creates_nothing(
    app, client: AsyncClient, admin_headers
):
    service = await _create_paged_service(
        client, app, admin_headers, name="Nothing", priority="P1"
    )
    await _window(app, "service", service)

    cleared = await _fire(client, service, "net-1", status="resolved")

    assert cleared["dedup_action"] == "skipped"
    assert cleared.get("incident_id") is None
    service_id = uuid.UUID(service["id"])
    assert await _count(app, Incident, Incident.service_id == service_id) == 0
    log = await _one(app, IngestLog, IngestLog.dedup_action == "skipped")
    assert log.error == "Recovery without an active incident"
