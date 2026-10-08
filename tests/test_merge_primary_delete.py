"""M1-30 (R-05, O-13): deleting an incident deletes the incidents combined
into it, each with its own Activity entry, and an admin can list them first
for the delete confirmation. An alert whose incident was combined away and
left behind by an earlier delete opens a new incident instead of being
swallowed."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from backend.api.auth import hash_password
from backend.db.models import AuditEntry, Incident
from backend.db.repos import IncidentChainStateRepo, IncidentRepo, UserRepo
from tests.test_every_source_pages_part6 import (
    _breaching_slo,
    _poller,
    _slo_incidents,
)
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

PASSWORD = "securepass123"


async def _incident(app, title: str) -> uuid.UUID:
    async with app.state.session_factory() as db:
        incident = await IncidentRepo.create(
            db, TEST_ORG_ID, title=title, description="M1-30 check"
        )
        await db.commit()
        return incident.id


async def _combine(client: AsyncClient, headers, primary, *secondaries) -> None:
    resp = await client.post(
        f"/incidents/{primary}/combine",
        json={"secondary_ids": [str(secondary) for secondary in secondaries]},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text


async def _remaining(app, ids) -> set[uuid.UUID]:
    async with app.state.session_factory() as db:
        rows = await db.execute(select(Incident.id).where(Incident.id.in_(list(ids))))
        return set(rows.scalars())


async def _deletions(app) -> dict[str, AuditEntry]:
    """Deletion entries by incident id; fails on a second entry for one."""
    async with app.state.session_factory() as db:
        rows = await db.execute(
            select(AuditEntry).where(AuditEntry.entry_type == "incident_deleted")
        )
        entries = list(rows.scalars())
    ids = [entry.tool_parameters["incident_id"] for entry in entries]
    assert len(ids) == len(set(ids)), ids
    return dict(zip(ids, entries))


async def _login_as(app, client: AsyncClient, name: str, role: str) -> dict:
    async with app.state.session_factory() as db:
        user = await UserRepo.create(
            db,
            username=name,
            email=f"{name}@test.com",
            password_hash=hash_password(PASSWORD),
            role=role,
            primary_org_id=TEST_ORG_ID,
        )
        await UserRepo.add_to_organization(
            db, user_id=user.id, org_id=TEST_ORG_ID, role=role
        )
        await db.commit()
    resp = await client.post(
        "/auth/login", json={"username": name, "password": PASSWORD}
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _lookup(client: AsyncClient, headers, ids):
    return await client.post(
        "/incidents/merged-into",
        json={"incident_ids": [str(incident_id) for incident_id in ids]},
        headers=headers,
    )


# ── Deleting a primary ──────────────────────────────────────────────────────


async def test_deleting_a_primary_deletes_the_incidents_combined_into_it(
    app, client: AsyncClient, admin_headers
):
    primary = await _incident(app, "Checkout down")
    first = await _incident(app, "Checkout 500s")
    second = await _incident(app, "Checkout latency")
    await _combine(client, admin_headers, primary, first, second)

    resp = await client.delete(f"/incidents/{primary}", headers=admin_headers)

    assert resp.status_code == 204, resp.text
    assert await _remaining(app, (primary, first, second)) == set()
    entries = await _deletions(app)
    assert set(entries) == {str(primary), str(first), str(second)}
    assert {entry.tool_name for entry in entries.values()} == {"delete_incident"}
    assert "merged_into_incident_id" not in entries[str(primary)].tool_parameters
    for merged, title in ((first, "Checkout 500s"), (second, "Checkout latency")):
        parameters = entries[str(merged)].tool_parameters
        assert parameters["merged_into_incident_id"] == str(primary)
        assert parameters["title"] == title


async def test_incidents_combined_into_a_combined_incident_go_too(
    app, client: AsyncClient, admin_headers
):
    primary = await _incident(app, "Region outage")
    middle = await _incident(app, "Zone outage")
    leaf = await _incident(app, "Host down")
    unrelated = await _incident(app, "Unrelated")
    await _combine(client, admin_headers, middle, leaf)
    await _combine(client, admin_headers, primary, middle)

    resp = await client.delete(f"/incidents/{primary}", headers=admin_headers)

    assert resp.status_code == 204, resp.text
    assert await _remaining(app, (primary, middle, leaf, unrelated)) == {unrelated}
    entries = await _deletions(app)
    assert set(entries) == {str(primary), str(middle), str(leaf)}
    assert entries[str(middle)].tool_parameters["merged_into_incident_id"] == str(
        primary
    )
    assert entries[str(leaf)].tool_parameters["merged_into_incident_id"] == str(middle)


async def test_bulk_delete_takes_combined_incidents_once_each(
    app, client: AsyncClient, admin_headers
):
    primary = await _incident(app, "Queue backlog")
    merged = await _incident(app, "Queue consumer lag")
    picked = await _incident(app, "Queue depth alarm")
    other = await _incident(app, "Cache misses")
    await _combine(client, admin_headers, primary, merged, picked)

    # The selection names the primary and one of the incidents combined into it.
    resp = await client.post(
        "/incidents/bulk",
        json={
            "action": "delete",
            "incident_ids": [str(primary), str(picked), str(other)],
        },
        headers=admin_headers,
    )

    assert resp.status_code == 200, resp.text
    assert await _remaining(app, (primary, merged, picked, other)) == set()
    entries = await _deletions(app)
    assert set(entries) == {str(primary), str(merged), str(picked), str(other)}
    assert {entry.tool_name for entry in entries.values()} == {"bulk_delete_incidents"}
    # Only the one deleted because of its primary says so.
    assert entries[str(merged)].tool_parameters["merged_into_incident_id"] == str(
        primary
    )
    for selected in (primary, picked, other):
        assert "merged_into_incident_id" not in entries[str(selected)].tool_parameters


# ── Listing them for the confirmation ──────────────────────────────────────


async def test_lookup_lists_what_a_delete_would_take_and_changes_nothing(
    app, client: AsyncClient, admin_headers
):
    primary = await _incident(app, "DB primary down")
    middle = await _incident(app, "DB replica lag")
    leaf = await _incident(app, "DB connections")
    lonely = await _incident(app, "Lonely")
    await _combine(client, admin_headers, middle, leaf)
    await _combine(client, admin_headers, primary, middle)

    resp = await _lookup(client, admin_headers, (primary, lonely))

    assert resp.status_code == 200, resp.text
    assert {
        (item["id"], item["title"], item["merged_into_incident_id"])
        for item in resp.json()["items"]
    } == {
        (str(middle), "DB replica lag", str(primary)),
        (str(leaf), "DB connections", str(middle)),
    }
    alone = await _lookup(client, admin_headers, (lonely,))
    assert alone.json() == {"items": []}
    all_four = {primary, middle, leaf, lonely}
    assert await _remaining(app, all_four) == all_four
    assert await _deletions(app) == {}


@pytest.mark.parametrize("role", ["operator", "viewer"])
async def test_only_admins_can_look_up_what_a_delete_takes(
    app, client: AsyncClient, admin_headers, role
):
    primary = await _incident(app, "Primary")
    merged = await _incident(app, "Merged")
    await _combine(client, admin_headers, primary, merged)
    headers = await _login_as(app, client, f"m130-{role}", role)

    resp = await _lookup(client, headers, (primary,))

    assert resp.status_code == 403, resp.text


async def test_lookup_takes_one_to_two_hundred_ids_like_bulk_delete(
    client: AsyncClient, admin_headers
):
    unknown = [uuid.uuid4() for _ in range(201)]

    assert (await _lookup(client, admin_headers, ())).status_code == 422
    assert (await _lookup(client, admin_headers, unknown)).status_code == 422
    full = await _lookup(client, admin_headers, unknown[:200])
    assert full.status_code == 200, full.text
    assert full.json() == {"items": []}


# ── Alerts whose incident was combined away ────────────────────────────────


async def _fire(client: AsyncClient, service: dict, alert_id: str) -> dict:
    resp = await client.post(
        service["intake_url"],
        json={"title": f"alert {alert_id}", "severity": "high", "id": alert_id},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _paged(app, incident_id: str) -> bool:
    async with app.state.session_factory() as db:
        state = await IncidentChainStateRepo.get_for_incident(
            db, TEST_ORG_ID, uuid.UUID(incident_id)
        )
    return state is not None


async def test_an_alert_combined_away_pages_again_after_its_primary_is_deleted(
    app, client: AsyncClient, admin_headers
):
    service = await _create_paged_service(
        client, app, admin_headers, name="Combined", priority="P1"
    )
    first = await _fire(client, service, "disk-1")
    primary = await _incident(app, "Storage incident")
    await _combine(client, admin_headers, primary, first["incident_id"])
    resp = await client.delete(f"/incidents/{primary}", headers=admin_headers)
    assert resp.status_code == 204, resp.text

    again = await _fire(client, service, "disk-1")

    assert again["dedup_action"] == "created"
    assert again["incident_id"] != first["incident_id"]
    assert await _paged(app, again["incident_id"])


@pytest.mark.parametrize("pointer", ["cleared", "missing"])
async def test_an_orphaned_merged_incident_does_not_swallow_its_alert(
    app, client: AsyncClient, admin_headers, pointer
):
    service = await _create_paged_service(
        client, app, admin_headers, name=f"Orphan{pointer}", priority="P1"
    )
    first = await _fire(client, service, "cpu-1")
    # What a delete before M1-30 left behind: merged, with no primary.
    async with app.state.session_factory() as db:
        await db.execute(
            update(Incident)
            .where(Incident.id == uuid.UUID(first["incident_id"]))
            .values(
                status="merged",
                merged_into_incident_id=None if pointer == "cleared" else uuid.uuid4(),
            )
        )
        await db.commit()

    again = await _fire(client, service, "cpu-1")

    assert again["dedup_action"] == "created"
    assert again["incident_id"] != first["incident_id"]
    assert await _paged(app, again["incident_id"])


async def test_an_orphaned_merged_slo_incident_does_not_swallow_a_violation(
    app, client: AsyncClient, admin_headers
):
    service = await _create_paged_service(
        client, app, admin_headers, name="SloOrphan", priority="P1"
    )
    slo_id = await _breaching_slo(app, uuid.UUID(service["id"]))
    poller = _poller(app)
    await poller._check_slos(TEST_ORG_ID)
    [first] = await _slo_incidents(app, slo_id)
    async with app.state.session_factory() as db:
        await db.execute(
            update(Incident)
            .where(Incident.id == first.id)
            .values(status="merged", merged_into_incident_id=None)
        )
        await db.commit()

    await poller._check_slos(TEST_ORG_ID)

    orphan, second = await _slo_incidents(app, slo_id)
    assert orphan.id == first.id and orphan.status == "merged"
    assert second.status == "open" and second.response_mode == "page"
