"""Every incident source pages (X5): SLO incidents page, P3 notifies, new
services default to P1, low-severity alerts notify, and Maintenance Windows
recur and scope the same way at intake and at dispatch."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.config_loader import AppConfig
from backend.db.models import Incident, IncidentComment
from backend.db.repos import (
    IncidentChainStateRepo,
    IncidentRepo,
    MaintenanceWindowRepo,
    ServiceRepo,
    SLATargetRepo,
    SLORepo,
    UptimeSampleRepo,
)
from backend.ingest.adapters.generic import GenericAdapter
from backend.paging.dispatch import evaluate_maintenance_window
from backend.paging.maintenance import parse_rrule, window_active_at, window_matches
from backend.paging.priority import DEFAULT_MODE_FOR
from backend.paging.service import compute_priority_for_payload
from backend.sla.poller import SLAPoller
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

# A Sunday, 02:00-04:00 UTC.
SUNDAY = datetime(2026, 9, 6, 2, 0, tzinfo=timezone.utc)


async def _comments(app, incident_id) -> list[str]:
    async with app.state.session_factory() as db:
        rows = await db.execute(
            select(IncidentComment.body).where(
                IncidentComment.incident_id == incident_id
            )
        )
        return list(rows.scalars())


# ── S01-S02: SLO burn-rate incidents page ──────────────────────────────────


async def _breaching_slo(app, service_id: uuid.UUID | None) -> uuid.UUID:
    async with app.state.session_factory() as db:
        target = await SLATargetRepo.create(
            db,
            TEST_ORG_ID,
            name=f"t-{uuid.uuid4().hex[:6]}",
            kind="http",
            service_id=service_id,
        )
        slo = await SLORepo.create(
            db,
            TEST_ORG_ID,
            target_id=target.id,
            name="availability",
            objective_pct=99.0,
            window_seconds=3600,
            burn_alert_threshold=1.0,
        )
        for index in range(10):
            await UptimeSampleRepo.create(
                db, TEST_ORG_ID, target_id=target.id, up=index < 5
            )
        await db.commit()
        return slo.id


async def _slo_incidents(app, slo_id) -> list[Incident]:
    async with app.state.session_factory() as db:
        rows = await db.execute(
            select(Incident)
            .where(Incident.external_source == f"slo:{slo_id}")
            .order_by(Incident.created_at)
        )
        return list(rows.scalars())


def _poller(app) -> SLAPoller:
    poller = SLAPoller(app.state.session_factory, config=AppConfig.load())

    async def _no_session(*_):  # keep AI sessions out of this test
        return None

    poller._incident_created_callback = _no_session
    return poller


async def test_s01_slo_violation_pages_its_services_chain(
    app, client: AsyncClient, admin_headers
):
    service = await _create_paged_service(
        client, app, admin_headers, name="SloPaged", priority="P1"
    )
    slo_id = await _breaching_slo(app, uuid.UUID(service["id"]))

    await _poller(app)._check_slos(TEST_ORG_ID)

    [incident] = await _slo_incidents(app, slo_id)
    assert (incident.priority, incident.response_mode) == ("P1", "page")
    async with app.state.session_factory() as db:
        state = await IncidentChainStateRepo.get_for_incident(
            db, TEST_ORG_ID, incident.id
        )
    assert state is not None  # before X5 the chain never started


async def test_s01_slo_violation_without_a_chain_says_so(app):
    slo_id = await _breaching_slo(app, None)  # no service: severity high → P1

    await _poller(app)._check_slos(TEST_ORG_ID)

    [incident] = await _slo_incidents(app, slo_id)
    assert incident.priority == "P1"
    assert any("no responder was paged" in c for c in await _comments(app, incident.id))


async def test_s02_a_violation_after_resolve_opens_a_new_incident(
    app, client: AsyncClient, admin_headers
):
    service = await _create_paged_service(
        client, app, admin_headers, name="SloAgain", priority="P1"
    )
    slo_id = await _breaching_slo(app, uuid.UUID(service["id"]))
    poller = _poller(app)

    await poller._check_slos(TEST_ORG_ID)
    await poller._check_slos(TEST_ORG_ID)  # still open: no duplicate
    [first] = await _slo_incidents(app, slo_id)
    async with app.state.session_factory() as db:
        await IncidentRepo.update_status(db, TEST_ORG_ID, first.id, "resolved")
        await db.commit()

    await poller._check_slos(TEST_ORG_ID)

    first_again, second = await _slo_incidents(app, slo_id)
    assert first_again.id == first.id and first_again.status == "resolved"
    assert second.status == "open" and second.response_mode == "page"


# ── S04: P0/P1 page, P2/P3 notify; new services default to P1 ─────────────


def test_s04_p3_notifies_like_p2():
    assert DEFAULT_MODE_FOR == {
        "P0": "page",
        "P1": "page",
        "P2": "notify",
        "P3": "notify",
    }


async def test_s04_new_services_default_to_p1_and_existing_ones_keep_theirs(
    app, client: AsyncClient, admin_headers
):
    team = await client.post(
        "/teams", json={"name": "Defaults", "slug": "defaults"}, headers=admin_headers
    )
    created = await client.post(
        "/services",
        json={"team_id": team.json()["id"], "name": "Fresh", "slug": "fresh"},
        headers=admin_headers,
    )
    assert created.status_code == 201, created.text
    assert created.json()["priority"] == "P1"
    async with app.state.session_factory() as db:
        old = await ServiceRepo.create(
            db,
            TEST_ORG_ID,
            team_id=uuid.UUID(team.json()["id"]),
            name="Old",
            slug="old",
            priority="P2",
        )
        await db.commit()
    listed = await client.get("/services", headers=admin_headers)
    by_id = {item["id"]: item for item in listed.json()["items"]}
    assert by_id[str(old.id)]["priority"] == "P2"


# ── S06: low-severity alerts notify (D-5) ─────────────────────────────────


@pytest.mark.parametrize(
    "severity, mode",
    [("low", "notify"), ("info", "notify"), ("medium", "page"), (None, "page")],
)
async def test_s06_low_severity_notifies_on_a_paging_service(
    app, client: AsyncClient, admin_headers, severity, mode
):
    service = await _create_paged_service(
        client, app, admin_headers, name=f"Sev{severity}", priority="P1"
    )
    async with app.state.session_factory() as db:
        result = await compute_priority_for_payload(
            db,
            TEST_ORG_ID,
            {"severity": severity},
            service_id=uuid.UUID(service["id"]),
        )
    assert (result.priority, result.response_mode) == ("P1", mode)


async def test_s06_a_low_alert_at_intake_notifies_and_says_why(
    app, client: AsyncClient, admin_headers
):
    service = await _create_paged_service(
        client, app, admin_headers, name="LowIntake", priority="P0"
    )
    resp = await client.post(
        service["intake_url"],
        json={"title": "disk 80%", "severity": "info", "id": "low-1"},
    )
    assert resp.status_code == 200, resp.text
    incident_id = uuid.UUID(resp.json()["incident_id"])
    async with app.state.session_factory() as db:
        incident = await IncidentRepo.get_by_id(db, TEST_ORG_ID, incident_id)
        state = await IncidentChainStateRepo.get_for_incident(
            db, TEST_ORG_ID, incident_id
        )
    assert (incident.priority, incident.response_mode) == ("P0", "notify")
    assert state is None
    assert any(
        "notifies instead of paging" in c for c in await _comments(app, incident_id)
    )


def test_s06_generic_adapter_reads_info_as_low():
    adapter = GenericAdapter()
    for raw in ("info", "INFORMATIONAL"):
        assert adapter.parse({"title": "t", "severity": raw}).severity == "low"
    assert adapter.parse({"title": "t", "severity": "weird"}).severity == "medium"


# ── S07: recurring Maintenance Windows ─────────────────────────────────────


def _window(rrule=None, **kw):
    base = dict(
        starts_at=SUNDAY,
        ends_at=SUNDAY + timedelta(hours=2),
        rrule=rrule,
        scope_type="global",
        scope_id=None,
        target_ids=[],
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_s07_weekly_window_covers_each_repeat_not_the_gaps():
    window = _window("FREQ=WEEKLY;BYDAY=SU")
    next_sunday = SUNDAY + timedelta(days=7)
    assert window_active_at(window, SUNDAY + timedelta(hours=1))
    assert window_active_at(window, next_sunday + timedelta(hours=1, minutes=59))
    assert not window_active_at(window, next_sunday + timedelta(hours=2))
    assert not window_active_at(window, next_sunday + timedelta(days=1, hours=1))
    assert not window_active_at(window, SUNDAY - timedelta(days=7))


def test_s07_count_and_until_end_the_series():
    third_sunday = SUNDAY + timedelta(days=14, hours=1)
    assert not window_active_at(_window("FREQ=WEEKLY;COUNT=2"), third_sunday)
    assert window_active_at(_window("FREQ=WEEKLY;COUNT=3"), third_sunday)
    assert not window_active_at(
        _window("FREQ=WEEKLY;UNTIL=20260913T235959Z"), third_sunday
    )


def test_s07_one_off_and_unreadable_rules_keep_the_first_window_only():
    for rrule in (None, "", "FREQ=NONSENSE"):
        window = _window(rrule)
        assert window_active_at(window, SUNDAY + timedelta(hours=1))
        assert not window_active_at(window, SUNDAY + timedelta(days=7, hours=1))


def test_s07_rules_faster_than_hourly_are_rejected():
    with pytest.raises(ValueError):
        parse_rrule("FREQ=MINUTELY", SUNDAY)
    parse_rrule("FREQ=HOURLY;INTERVAL=6", SUNDAY)


async def test_s07_active_windows_include_repeats(app):
    async with app.state.session_factory() as db:
        window = await MaintenanceWindowRepo.create(
            db,
            TEST_ORG_ID,
            name="weekly",
            starts_at=SUNDAY,
            ends_at=SUNDAY + timedelta(hours=2),
            rrule="FREQ=WEEKLY",
        )
        await db.commit()
        in_repeat = SUNDAY + timedelta(days=21, hours=1)
        between = SUNDAY + timedelta(days=21, hours=3)
        assert [
            w.id
            for w in await MaintenanceWindowRepo.list_active_at(
                db, TEST_ORG_ID, in_repeat
            )
        ] == [window.id]
        assert (
            await MaintenanceWindowRepo.list_active_at(db, TEST_ORG_ID, between) == []
        )


async def test_s07_api_rejects_an_unreadable_rule(client: AsyncClient, admin_headers):
    body = {
        "name": "patching",
        "starts_at": SUNDAY.isoformat(),
        "ends_at": (SUNDAY + timedelta(hours=2)).isoformat(),
    }
    bad = await client.post(
        "/maintenance-windows",
        json={**body, "rrule": "FREQ=SOMETIMES"},
        headers=admin_headers,
    )
    fast = await client.post(
        "/maintenance-windows",
        json={**body, "rrule": "FREQ=MINUTELY"},
        headers=admin_headers,
    )
    good = await client.post(
        "/maintenance-windows",
        json={**body, "rrule": "FREQ=WEEKLY;BYDAY=SU"},
        headers=admin_headers,
    )
    assert (bad.status_code, fast.status_code, good.status_code) == (422, 422, 201)
    edited = await client.put(
        f"/maintenance-windows/{good.json()['id']}",
        json={"rrule": "FREQ=SOMETIMES"},
        headers=admin_headers,
    )
    assert edited.status_code == 422


# ── S08: one scope rule at intake and dispatch ─────────────────────────────


def test_s08_scopes():
    service, team, roster = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    other = uuid.uuid4()

    def matches(scope_type, target, **kw):
        window = _window(scope_type=scope_type, target_ids=[str(target)])
        return window_matches(window, service_id=service, team_id=team, **kw)

    assert matches("global", other)
    assert matches("service", service) and not matches("service", other)
    assert matches("team", team) and not matches("team", other)
    assert matches("roster", roster, roster_id=roster)
    assert not matches("roster", roster)  # intake: no Roster yet
    assert not matches("roster", roster, roster_id=other)


async def test_s08_team_and_roster_windows_hold_back_pages(
    app, client: AsyncClient, admin_headers
):
    service = await _create_paged_service(
        client, app, admin_headers, name="Scoped", priority="P1"
    )
    roster_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    async with app.state.session_factory() as db:
        incident = await IncidentRepo.create(
            db,
            TEST_ORG_ID,
            title="scoped",
            description="x",
            service_id=uuid.UUID(service["id"]),
        )
        roster_window = await MaintenanceWindowRepo.create(
            db,
            TEST_ORG_ID,
            name="roster",
            starts_at=now - timedelta(minutes=5),
            ends_at=now + timedelta(minutes=5),
            scope_type="roster",
            scope_id=roster_id,
            target_ids=[str(roster_id)],
        )
        await db.commit()
        assert (
            await evaluate_maintenance_window(
                db, TEST_ORG_ID, incident=incident, at=now
            )
            is None
        )
        found = await evaluate_maintenance_window(
            db, TEST_ORG_ID, incident=incident, at=now, roster_id=roster_id
        )
        assert found.id == roster_window.id

        team_window = await MaintenanceWindowRepo.create(
            db,
            TEST_ORG_ID,
            name="team",
            starts_at=now - timedelta(minutes=5),
            ends_at=now + timedelta(minutes=5),
            scope_type="team",
            scope_id=uuid.UUID(service["team_id"]),
            target_ids=[service["team_id"]],
        )
        await db.commit()
        found = await evaluate_maintenance_window(
            db, TEST_ORG_ID, incident=incident, at=now
        )
        assert found.id == team_window.id  # before X5, team scope was intake-only
