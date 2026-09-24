"""Regression cases for intake recovery and cross-service collision handling."""

from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from backend.db.models import InAppNotification, IngestLog
from backend.db.repos import (
    IncidentPageRepo,
    IncidentRepo,
    IngestLogRepo,
    IngestTokenRepo,
    EscalationStepRepo,
    ServiceEscalationChainRepo,
    UserRepo,
)
from backend.ingest.service import generate_token, hash_token
from tests.test_ingest import (
    TEST_ORG_ID,
    _create_paged_service,
    _create_token,
    admin_headers as _admin_headers_fixture,
    app as _app_fixture,
    client as _client_fixture,
    viewer_headers as _viewer_headers_fixture,
)

app = pytest.fixture(_app_fixture.__wrapped__)
client = pytest.fixture(_client_fixture.__wrapped__)
admin_headers = pytest.fixture(_admin_headers_fixture.__wrapped__)
viewer_headers = pytest.fixture(_viewer_headers_fixture.__wrapped__)


def _sns(state: str, message_id: str) -> dict:
    return {
        "Type": "Notification",
        "MessageId": message_id,
        "Message": json.dumps(
            {
                "AlarmName": "qa-cpu",
                "AWSAccountId": "000000000000",
                "Region": "us-east-1",
                "NewStateValue": state,
                "NewStateReason": "Synthetic threshold",
            }
        ),
    }


def test_nested_json_mapping_and_status_are_canonical():
    from backend.ingest.adapters.generic import GenericAdapter
    from backend.ingest.adapters.universal import UniversalAdapter

    payload = {
        "data": json.dumps(
            {
                "message": json.dumps(
                    {
                        "title": "Nested alarm",
                        "id": "nested-1",
                        "status": "acknowledged",
                    }
                )
            }
        )
    }
    parsed = UniversalAdapter(
        field_mapping={
            "title": "data.message.title",
            "external_id": "data.message.id",
            "status": "data.message.status",
        }
    ).parse(payload)
    assert parsed.title == "Nested alarm"
    assert parsed.external_id == "nested-1"
    assert parsed.status == "open"
    heuristic = UniversalAdapter().parse(payload)
    assert heuristic.title == "Nested alarm"
    assert heuristic.external_id == "nested-1"
    assert payload["data"].startswith("{")
    assert (
        GenericAdapter().parse({"title": "x", "status": "acknowledged"}).status
        == "open"
    )
    assert UniversalAdapter().parse({"title": {"nested": "x"}}).needs_llm


def test_collision_display_text_cannot_expand_chat_mentions():
    from backend.ingest.collision import _safe

    assert _safe("<@everyone>\n forged log") == "‹＠everyone› forged log"


def test_nested_envelope_decode_is_bounded_without_mutating_audit_input():
    from backend.ingest.adapters.universal import UniversalAdapter, normalize_payload

    original = {
        "data": json.dumps(
            {"message": json.dumps({"payload": json.dumps({"title": "Too deep"})})}
        )
    }
    normalized = normalize_payload(original)
    assert isinstance(original["data"], str)
    assert isinstance(normalized["data"]["message"]["payload"], str)
    assert UniversalAdapter().parse(original).needs_llm
    with pytest.raises(ValueError, match="exceeds the supported size"):
        normalize_payload({"data": "{" + " " * 65536 + "}"})


async def test_nested_sample_paths_hit_normalized_shape_cache(
    client: AsyncClient, admin_headers
):
    def sample(title: str, external_id: str) -> dict:
        return {
            "data": json.dumps(
                {"message": json.dumps({"title": title, "id": external_id})}
            )
        }

    created = await client.post(
        "/ingest-tokens",
        json={
            "name": "nested-prewarm",
            "provider": "auto",
            "sample_payload": sample("First", "first"),
        },
        headers=admin_headers,
    )
    assert created.status_code == 201
    token = created.json()
    learned = await client.post(
        f"/ingest-tokens/{token['id']}/learn-shape",
        json={"payload": sample("Second", "second")},
        headers=admin_headers,
    )
    assert learned.status_code == 200
    assert learned.json()["cache_hit"] is True
    assert learned.json()["paths"]["title"] == "data.message.title"
    assert learned.json()["preview"]["title"] == "Second"
    ingested = await client.post(
        "/incidents/ingest",
        headers={"X-OpsMender-Token": token["token"]},
        json=sample("Third", "third"),
    )
    assert ingested.status_code == 200
    assert ingested.json()["dedup_action"] == "created"


@pytest.mark.parametrize("message", ["{bad", "[]", "null", "42"])
async def test_invalid_sns_message_never_creates_incident(
    client: AsyncClient, app, message
):
    raw, token = await _create_token(
        app, provider="auto", name=f"bad-{uuid.uuid4().hex[:6]}"
    )
    response = await client.post(
        "/incidents/ingest",
        headers={"X-OpsMender-Token": raw},
        json={"Type": "Notification", "Message": message},
    )
    assert response.status_code == 422
    assert response.json()["incident_id"] is None
    async with app.state.session_factory() as db:
        rows = (
            (
                await db.execute(
                    select(IngestLog).where(IngestLog.ingest_token_id == token.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].incident_id is None


@pytest.mark.parametrize(
    "message",
    [{"AlarmName": "incomplete"}, {"NewStateValue": "ALARM"}],
)
async def test_partial_sns_alarm_is_rejected(client: AsyncClient, app, message):
    raw, _ = await _create_token(
        app, provider="auto", name=f"partial-{uuid.uuid4().hex[:6]}"
    )
    response = await client.post(
        "/incidents/ingest",
        headers={"X-OpsMender-Token": raw},
        json={"Type": "Notification", "Message": message},
    )
    assert response.status_code == 422
    assert response.json()["incident_id"] is None


@pytest.mark.parametrize("grouping", ["on", "off"])
async def test_sns_recovery_then_refire_creates_new_incident(
    client: AsyncClient, app, admin_headers, grouping
):
    service = await _create_paged_service(
        client, app, admin_headers, name=f"SNS{grouping}", alert_grouping=grouping
    )
    raw = generate_token()
    async with app.state.session_factory() as db:
        await IngestTokenRepo.create(
            db,
            TEST_ORG_ID,
            name=f"sns-{grouping}",
            provider="auto",
            token_hash=hash_token(raw),
            service_id=uuid.UUID(service["id"]),
        )
        await db.commit()
    headers = {"X-OpsMender-Token": raw}
    first = await client.post(
        "/incidents/ingest", headers=headers, json=_sns("ALARM", "1")
    )
    duplicate = await client.post(
        "/incidents/ingest", headers=headers, json=_sns("ALARM", "2")
    )
    clear = await client.post(
        "/incidents/ingest", headers=headers, json=_sns("OK", "3")
    )
    refire = await client.post(
        "/incidents/ingest", headers=headers, json=_sns("ALARM", "4")
    )
    assert [r.status_code for r in (first, duplicate, clear, refire)] == [200] * 4
    assert first.json()["dedup_action"] == "created"
    assert duplicate.json()["incident_id"] == first.json()["incident_id"]
    assert clear.json()["dedup_action"] == "updated"
    assert refire.json()["dedup_action"] == "created"
    assert refire.json()["incident_id"] != first.json()["incident_id"]
    async with app.state.session_factory() as db:
        prior = await IncidentRepo.get_by_id(
            db, TEST_ORG_ID, uuid.UUID(first.json()["incident_id"])
        )
        new = await IncidentRepo.get_by_id(
            db, TEST_ORG_ID, uuid.UUID(refire.json()["incident_id"])
        )
        assert prior.status == "resolved"
        assert new.status == "open"
        assert new.title == "[CloudWatch] qa-cpu — ALARM"
        assert new.external_id == prior.external_id == "000000000000:us-east-1:qa-cpu"
        assert await IncidentPageRepo.list_for_incident(db, TEST_ORG_ID, new.id)


async def test_orphan_recovery_logs_without_incident(client: AsyncClient, app):
    raw, token = await _create_token(app, provider="auto", name="orphan")
    result = await client.post(
        "/incidents/ingest", headers={"X-OpsMender-Token": raw}, json=_sns("OK", "1")
    )
    assert result.status_code == 200
    assert result.json()["dedup_action"] == "skipped"
    assert result.json()["incident_id"] is None
    async with app.state.session_factory() as db:
        logs = (
            (
                await db.execute(
                    select(IngestLog).where(IngestLog.ingest_token_id == token.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(logs) == 1
        assert logs[0].error == "Recovery without an active incident"


async def test_recovery_without_fingerprint_is_skipped(client: AsyncClient, app):
    raw, token = await _create_token(app, provider="generic", name="no-fingerprint")
    response = await client.post(
        "/incidents/ingest",
        headers={"X-OpsMender-Token": raw},
        json={"title": "Recovered", "status": "resolved"},
    )
    assert response.status_code == 200
    assert response.json()["dedup_action"] == "skipped"
    assert response.json()["incident_id"] is None
    async with app.state.session_factory() as db:
        logs = (
            (
                await db.execute(
                    select(IngestLog).where(IngestLog.ingest_token_id == token.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(logs) == 1
        assert logs[0].incident_id is None


async def test_sns_fingerprint_stays_separate_across_auto_tokens(
    client: AsyncClient, app
):
    first_token, _ = await _create_token(app, provider="auto", name="first-auto")
    second_token, _ = await _create_token(app, provider="auto", name="second-auto")
    payload = _sns("ALARM", "delivery-1")
    first = await client.post(
        "/incidents/ingest", headers={"X-OpsMender-Token": first_token}, json=payload
    )
    duplicate = await client.post(
        "/incidents/ingest", headers={"X-OpsMender-Token": first_token}, json=payload
    )
    second = await client.post(
        "/incidents/ingest", headers={"X-OpsMender-Token": second_token}, json=payload
    )
    assert [r.status_code for r in (first, duplicate, second)] == [200] * 3
    assert duplicate.json()["incident_id"] == first.json()["incident_id"]
    assert duplicate.json()["dedup_action"] == "skipped"
    assert second.json()["dedup_action"] == "created"
    assert second.json()["incident_id"] != first.json()["incident_id"]


async def test_service_intake_url_uses_its_auto_token_with_other_bound_tokens(
    client: AsyncClient, app, admin_headers
):
    service = await _create_paged_service(
        client, app, admin_headers, name="ServiceURL", alert_grouping="off"
    )
    async with app.state.session_factory() as db:
        await IngestTokenRepo.create(
            db,
            TEST_ORG_ID,
            name="service-url-generic",
            provider="generic",
            token_hash=hash_token(generate_token()),
            service_id=uuid.UUID(service["id"]),
        )
        await db.commit()
    first = await client.post(service["intake_url"], json=_sns("ALARM", "delivery-1"))
    duplicate = await client.post(
        service["intake_url"], json=_sns("ALARM", "delivery-2")
    )
    assert first.status_code == duplicate.status_code == 200
    assert first.json()["dedup_action"] == "created"
    assert duplicate.json()["incident_id"] == first.json()["incident_id"]
    assert duplicate.json()["dedup_action"] == "skipped"


async def test_same_service_refire_keeps_in_progress_without_collision(
    client: AsyncClient, app, admin_headers
):
    service = await _create_paged_service(client, app, admin_headers, name="SameSvc")
    raw = generate_token()
    async with app.state.session_factory() as db:
        await IngestTokenRepo.create(
            db,
            TEST_ORG_ID,
            name="same-service",
            provider="generic",
            token_hash=hash_token(raw),
            service_id=uuid.UUID(service["id"]),
        )
        await db.commit()
    payload = {"title": "Same service alarm", "id": "same-service-1"}
    first = await client.post(
        "/incidents/ingest", headers={"X-OpsMender-Token": raw}, json=payload
    )
    incident_id = uuid.UUID(first.json()["incident_id"])
    async with app.state.session_factory() as db:
        await IncidentRepo.update_status(db, TEST_ORG_ID, incident_id, "in_progress")
        await db.commit()
    repeated = await client.post(
        "/incidents/ingest", headers={"X-OpsMender-Token": raw}, json=payload
    )
    assert repeated.status_code == 200
    assert repeated.json()["incident_id"] == str(incident_id)
    assert repeated.json()["dedup_action"] in {"skipped", "updated"}
    async with app.state.session_factory() as db:
        incident = await IncidentRepo.get_by_id(db, TEST_ORG_ID, incident_id)
        assert incident.status == "in_progress"
        assert incident.service_id == uuid.UUID(service["id"])
        inbox = (
            (
                await db.execute(
                    select(InAppNotification).where(
                        InAppNotification.incident_id == incident_id,
                        InAppNotification.event_type == "incident.collision",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert inbox == []


@pytest.mark.parametrize("delivery_failure", [False, True])
async def test_collision_notifies_losing_service_once(
    client: AsyncClient,
    app,
    admin_headers,
    viewer_headers,
    monkeypatch,
    delivery_failure,
):
    owner = await _create_paged_service(client, app, admin_headers, name="Owner")
    loser = await _create_paged_service(client, app, admin_headers, name="Loser")
    raw_owner, raw_loser = generate_token(), generate_token()
    async with app.state.session_factory() as db:
        for name, raw, service in (
            ("owner", raw_owner, owner),
            ("loser", raw_loser, loser),
        ):
            await IngestTokenRepo.create(
                db,
                TEST_ORG_ID,
                name=name,
                provider="generic",
                token_hash=hash_token(raw),
                service_id=uuid.UUID(service["id"]),
            )
        await db.commit()
    deliveries = []

    async def fake_delivery(*args, **kwargs):
        deliveries.append(kwargs)
        if delivery_failure:
            raise RuntimeError("local test sink failed")

    monkeypatch.setattr("backend.bots.notifier.deliver_incident_text", fake_delivery)
    payload = {"title": "same alarm", "id": "shared-1", "severity": "high"}
    first = await client.post(
        "/incidents/ingest", headers={"X-OpsMender-Token": raw_owner}, json=payload
    )
    incident_id = uuid.UUID(first.json()["incident_id"])
    async with app.state.session_factory() as db:
        pages_before = len(
            await IncidentPageRepo.list_for_incident(db, TEST_ORG_ID, incident_id)
        )
    second = await client.post(
        "/incidents/ingest", headers={"X-OpsMender-Token": raw_loser}, json=payload
    )
    third = await client.post(
        "/incidents/ingest", headers={"X-OpsMender-Token": raw_loser}, json=payload
    )
    same_service = await client.post(
        "/incidents/ingest", headers={"X-OpsMender-Token": raw_owner}, json=payload
    )
    await asyncio.gather(*list(app.state.background_tasks))
    assert (
        first.status_code
        == second.status_code
        == third.status_code
        == same_service.status_code
        == 200
    )
    assert (
        second.json()["incident_id"] == third.json()["incident_id"] == str(incident_id)
    )
    assert second.json()["dedup_action"] == third.json()["dedup_action"] == "skipped"
    assert len(deliveries) == 1
    assert deliveries[0]["team_id"] == uuid.UUID(loser["team_id"])
    assert deliveries[0]["preserve_team"] is True
    assert deliveries[0]["respond_only"] is True
    assert deliveries[0]["strict_team_scope"] is True
    assert deliveries[0]["informational_only"] is True
    viewer_inbox = await client.get("/notifications", headers=viewer_headers)
    assert viewer_inbox.status_code == 200
    assert all(
        item["event_type"] != "incident.collision"
        for item in viewer_inbox.json()["items"]
    )
    async with app.state.session_factory() as db:
        incident = await IncidentRepo.get_by_id(db, TEST_ORG_ID, incident_id)
        assert incident.service_id == uuid.UUID(owner["id"])
        assert (
            len(await IncidentPageRepo.list_for_incident(db, TEST_ORG_ID, incident_id))
            == pages_before
        )
        inbox = (
            (
                await db.execute(
                    select(InAppNotification).where(
                        InAppNotification.incident_id == incident_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(inbox) == 1
        assert inbox[0].link == f"/dashboard/incidents/detail?id={incident_id}"
        logs = (
            (
                await db.execute(
                    select(IngestLog).where(IngestLog.incident_id == incident_id)
                )
            )
            .scalars()
            .all()
        )
        assert len(logs) == 4
        assert (
            sum(
                bool(row.error and row.error.startswith("collision:v1:"))
                for row in logs
            )
            == 2
        )


async def test_collision_delivery_uses_losing_respond_lane(
    client: AsyncClient, app, admin_headers, monkeypatch
):
    from backend.bots import notifier

    owner = await _create_paged_service(client, app, admin_headers, name="RoutedOwner")
    loser = await _create_paged_service(client, app, admin_headers, name="RoutedLoser")
    raw = generate_token()
    async with app.state.session_factory() as db:
        await IngestTokenRepo.create(
            db,
            TEST_ORG_ID,
            name="route-owner",
            provider="generic",
            token_hash=hash_token(raw),
            service_id=uuid.UUID(owner["id"]),
        )
        await db.commit()
    response = await client.post(
        "/incidents/ingest",
        headers={"X-OpsMender-Token": raw},
        json={"title": "Route test", "id": "route-1"},
    )
    incident_id = uuid.UUID(response.json()["incident_id"])
    connectors = [
        SimpleNamespace(
            platform="slack",
            lanes={"respond"},
            config={"team_scope": "teams", "team_ids": [owner["team_id"]]},
        ),
        SimpleNamespace(
            platform="slack",
            lanes={"track"},
            config={"team_scope": "teams", "team_ids": [loser["team_id"]]},
        ),
        SimpleNamespace(platform="slack", lanes={"respond"}, config={}),
        SimpleNamespace(
            platform="slack",
            lanes={"respond"},
            config={"team_scope": "teams", "team_ids": [loser["team_id"]]},
        ),
    ]

    async def fake_list(*args, **kwargs):
        return connectors

    delivered = []

    async def fake_deliver(*args, **kwargs):
        delivered.append(kwargs)

    monkeypatch.setattr(notifier.BotConnectorRepo, "list_all", fake_list)
    monkeypatch.setattr(notifier, "_has_capability", lambda *args: True)
    monkeypatch.setattr(notifier, "_has_lane", lambda c, lane: lane in c.lanes)
    monkeypatch.setattr(notifier, "_allowed_chat_ids", lambda c: ["local-chat"])
    monkeypatch.setattr(notifier, "get_adapter", lambda *args: object())
    monkeypatch.setattr(notifier, "_deliver", fake_deliver)
    await notifier.deliver_incident_text(
        app.state.session_factory,
        org_id=TEST_ORG_ID,
        text="Alert absorbed",
        event_type="incident.collision",
        team_id=uuid.UUID(loser["team_id"]),
        incident_id=incident_id,
        preserve_team=True,
        respond_only=True,
        strict_team_scope=True,
        informational_only=True,
    )
    assert len(delivered) == 1
    assert delivered[0]["connector"] is connectors[3]
    assert delivered[0]["delivery_lane"] == "respond"
    assert delivered[0]["lifecycle_event"] is None
    assert delivered[0]["incident"] is None


async def test_collision_with_unassigned_owner_names_both_sides(
    client: AsyncClient, app, admin_headers, monkeypatch
):
    loser = await _create_paged_service(client, app, admin_headers, name="Receiver")
    owner_token, _ = await _create_token(
        app, provider="generic", name="unassigned-owner"
    )
    loser_token = generate_token()
    async with app.state.session_factory() as db:
        await IngestTokenRepo.create(
            db,
            TEST_ORG_ID,
            name="receiver-token",
            provider="generic",
            token_hash=hash_token(loser_token),
            service_id=uuid.UUID(loser["id"]),
        )
        await db.commit()
    delivered = []

    async def fake_delivery(*args, **kwargs):
        delivered.append(kwargs)

    monkeypatch.setattr("backend.bots.notifier.deliver_incident_text", fake_delivery)
    payload = {"title": "Unassigned alarm", "id": "unassigned-1"}
    created = await client.post(
        "/incidents/ingest",
        headers={"X-OpsMender-Token": owner_token},
        json=payload,
    )
    folded = await client.post(
        "/incidents/ingest",
        headers={"X-OpsMender-Token": loser_token},
        json=payload,
    )
    await asyncio.gather(*list(app.state.background_tasks))
    assert folded.status_code == 200
    assert folded.json()["incident_id"] == created.json()["incident_id"]
    assert len(delivered) == 1
    assert "Unassigned service" in delivered[0]["text"]
    assert "Receiver" in delivered[0]["text"]
    async with app.state.session_factory() as db:
        incident = await IncidentRepo.get_by_id(
            db, TEST_ORG_ID, uuid.UUID(created.json()["incident_id"])
        )
        assert incident.service_id is None


async def test_collision_rollback_sends_nothing_and_persists_no_marker(
    client: AsyncClient, app, admin_headers, monkeypatch
):
    owner = await _create_paged_service(
        client, app, admin_headers, name="RollbackOwner"
    )
    loser = await _create_paged_service(
        client, app, admin_headers, name="RollbackLoser"
    )
    raw_owner, raw_loser = generate_token(), generate_token()
    async with app.state.session_factory() as db:
        for name, raw, service in (
            ("rollback-owner", raw_owner, owner),
            ("rollback-loser", raw_loser, loser),
        ):
            await IngestTokenRepo.create(
                db,
                TEST_ORG_ID,
                name=name,
                provider="generic",
                token_hash=hash_token(raw),
                service_id=uuid.UUID(service["id"]),
            )
        await db.commit()
    payload = {"title": "Rollback alarm", "id": "rollback-1"}
    created = await client.post(
        "/incidents/ingest", headers={"X-OpsMender-Token": raw_owner}, json=payload
    )
    incident_id = uuid.UUID(created.json()["incident_id"])
    delivered = []

    async def fake_delivery(*args, **kwargs):
        delivered.append(kwargs)

    monkeypatch.setattr("backend.bots.notifier.deliver_incident_text", fake_delivery)
    original_create = IngestLogRepo.create

    async def fail_after_notice(*args, **kwargs):
        if str(kwargs.get("error") or "").startswith("collision:v1:"):
            raise RuntimeError("synthetic ingest log failure")
        return await original_create(*args, **kwargs)

    with monkeypatch.context() as patcher:
        patcher.setattr(IngestLogRepo, "create", fail_after_notice)
        with pytest.raises(RuntimeError, match="synthetic ingest log failure"):
            await client.post(
                "/incidents/ingest",
                headers={"X-OpsMender-Token": raw_loser},
                json=payload,
            )
    assert delivered == []
    async with app.state.session_factory() as db:
        logs = await IngestLogRepo.list_for_incident(db, TEST_ORG_ID, incident_id)
        assert not any(
            row.error and row.error.startswith("collision:v1:") for row in logs
        )
        inbox = (
            (
                await db.execute(
                    select(InAppNotification).where(
                        InAppNotification.incident_id == incident_id,
                        InAppNotification.event_type == "incident.collision",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert inbox == []


async def test_collision_without_losing_chain_is_logged_without_page(
    client: AsyncClient, app, admin_headers, monkeypatch
):
    owner = await _create_paged_service(client, app, admin_headers, name="NoChainOwner")
    loser = await _create_paged_service(client, app, admin_headers, name="NoChainLoser")
    raw_owner, raw_loser = generate_token(), generate_token()
    async with app.state.session_factory() as db:
        links = await ServiceEscalationChainRepo.list_for_service(
            db, TEST_ORG_ID, uuid.UUID(loser["id"])
        )
        assert len(links) == 1
        assert await ServiceEscalationChainRepo.unlink(
            db,
            TEST_ORG_ID,
            service_id=uuid.UUID(loser["id"]),
            chain_id=links[0].chain_id,
        )
        for name, raw, service in (
            ("no-chain-owner", raw_owner, owner),
            ("no-chain-loser", raw_loser, loser),
        ):
            await IngestTokenRepo.create(
                db,
                TEST_ORG_ID,
                name=name,
                provider="generic",
                token_hash=hash_token(raw),
                service_id=uuid.UUID(service["id"]),
            )
        await db.commit()
    deliveries = []

    async def fake_delivery(*args, **kwargs):
        deliveries.append(kwargs)

    monkeypatch.setattr("backend.bots.notifier.deliver_incident_text", fake_delivery)
    payload = {"title": "no chain alarm", "id": "no-chain-1"}
    created = await client.post(
        "/incidents/ingest", headers={"X-OpsMender-Token": raw_owner}, json=payload
    )
    absorbed = await client.post(
        "/incidents/ingest", headers={"X-OpsMender-Token": raw_loser}, json=payload
    )
    assert absorbed.status_code == 200
    assert absorbed.json()["dedup_action"] == "skipped"
    assert absorbed.json()["incident_id"] == created.json()["incident_id"]
    assert deliveries == []
    async with app.state.session_factory() as db:
        incident_id = uuid.UUID(created.json()["incident_id"])
        logs = await IngestLogRepo.list_for_incident(db, TEST_ORG_ID, incident_id)
        assert any(row.error and "NoChainLoser" in row.error for row in logs)
        inbox = (
            (
                await db.execute(
                    select(InAppNotification).where(
                        InAppNotification.incident_id == incident_id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert inbox == []


async def test_collision_deduplicates_repeated_responder_across_chain_steps(
    client: AsyncClient, app, admin_headers, monkeypatch
):
    owner = await _create_paged_service(client, app, admin_headers, name="MultiOwner")
    loser = await _create_paged_service(client, app, admin_headers, name="MultiLoser")
    raw_owner, raw_loser = generate_token(), generate_token()
    async with app.state.session_factory() as db:
        links = await ServiceEscalationChainRepo.list_for_service(
            db, TEST_ORG_ID, uuid.UUID(loser["id"])
        )
        steps = await EscalationStepRepo.list_for_chain(
            db, TEST_ORG_ID, links[0].chain_id
        )
        target_id = steps[0].target_id
        for name, raw, service in (
            ("multi-owner", raw_owner, owner),
            ("multi-loser", raw_loser, loser),
        ):
            await IngestTokenRepo.create(
                db,
                TEST_ORG_ID,
                name=name,
                provider="generic",
                token_hash=hash_token(raw),
                service_id=uuid.UUID(service["id"]),
            )
        await db.commit()
    added = await client.post(
        f"/escalation-chains/{links[0].chain_id}/steps",
        json={
            "step_index": 1,
            "target_type": "user",
            "target_id": str(target_id),
            "timeout_seconds": 300,
        },
        headers=admin_headers,
    )
    assert added.status_code == 201

    async def fake_delivery(*args, **kwargs):
        pass

    monkeypatch.setattr("backend.bots.notifier.deliver_incident_text", fake_delivery)
    payload = {"title": "Multi target", "id": "multi-target-1"}
    first = await client.post(
        "/incidents/ingest", headers={"X-OpsMender-Token": raw_owner}, json=payload
    )
    folded = await client.post(
        "/incidents/ingest", headers={"X-OpsMender-Token": raw_loser}, json=payload
    )
    assert folded.status_code == 200
    incident_id = uuid.UUID(first.json()["incident_id"])
    async with app.state.session_factory() as db:
        inbox = (
            (
                await db.execute(
                    select(InAppNotification).where(
                        InAppNotification.incident_id == incident_id,
                        InAppNotification.event_type == "incident.collision",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(inbox) == 1
        assert inbox[0].user_id == target_id


async def test_collision_with_inactive_responder_warns_without_inbox(
    client: AsyncClient, app, admin_headers, monkeypatch, caplog
):
    owner = await _create_paged_service(
        client, app, admin_headers, name="InactiveOwner"
    )
    loser = await _create_paged_service(
        client, app, admin_headers, name="InactiveLoser"
    )
    raw_owner, raw_loser = generate_token(), generate_token()
    async with app.state.session_factory() as db:
        links = await ServiceEscalationChainRepo.list_for_service(
            db, TEST_ORG_ID, uuid.UUID(loser["id"])
        )
        steps = await EscalationStepRepo.list_for_chain(
            db, TEST_ORG_ID, links[0].chain_id
        )
        user = await UserRepo.get_by_id(db, steps[0].target_id)
        user.is_active = False
        for name, raw, service in (
            ("inactive-owner", raw_owner, owner),
            ("inactive-loser", raw_loser, loser),
        ):
            await IngestTokenRepo.create(
                db,
                TEST_ORG_ID,
                name=name,
                provider="generic",
                token_hash=hash_token(raw),
                service_id=uuid.UUID(service["id"]),
            )
        await db.commit()

    async def fake_delivery(*args, **kwargs):
        pass

    monkeypatch.setattr("backend.bots.notifier.deliver_incident_text", fake_delivery)
    payload = {"title": "Inactive target", "id": "inactive-target-1"}
    first = await client.post(
        "/incidents/ingest", headers={"X-OpsMender-Token": raw_owner}, json=payload
    )
    folded = await client.post(
        "/incidents/ingest", headers={"X-OpsMender-Token": raw_loser}, json=payload
    )
    assert folded.status_code == 200
    assert "no active responders" in caplog.text
    incident_id = uuid.UUID(first.json()["incident_id"])
    async with app.state.session_factory() as db:
        inbox = (
            (
                await db.execute(
                    select(InAppNotification).where(
                        InAppNotification.incident_id == incident_id,
                        InAppNotification.event_type == "incident.collision",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert inbox == []


# ── Review follow-ups ───────────────────────────────────────────────────────


async def _provider_tokens(app, *services: dict) -> list[str]:
    raws = []
    async with app.state.session_factory() as db:
        for service in services:
            raw = generate_token()
            await IngestTokenRepo.create(
                db,
                TEST_ORG_ID,
                name=f"provider-{uuid.uuid4().hex[:8]}",
                provider="generic",
                token_hash=hash_token(raw),
                service_id=uuid.UUID(service["id"]),
            )
            raws.append(raw)
        await db.commit()
    return raws


async def _first_target(app, service: dict):
    async with app.state.session_factory() as db:
        link = (
            await ServiceEscalationChainRepo.list_for_service(
                db, TEST_ORG_ID, uuid.UUID(service["id"])
            )
        )[0]
        steps = await EscalationStepRepo.list_for_chain(db, TEST_ORG_ID, link.chain_id)
        return link.chain_id, steps[0].target_id


async def _unlink_all(app, service: dict) -> None:
    async with app.state.session_factory() as db:
        for link in await ServiceEscalationChainRepo.list_for_service(
            db, TEST_ORG_ID, uuid.UUID(service["id"])
        ):
            await ServiceEscalationChainRepo.unlink(
                db,
                TEST_ORG_ID,
                service_id=uuid.UUID(service["id"]),
                chain_id=link.chain_id,
            )
        await db.commit()


async def _collision_inbox(app, incident_id: uuid.UUID) -> list:
    async with app.state.session_factory() as db:
        return list(
            (
                await db.execute(
                    select(InAppNotification).where(
                        InAppNotification.incident_id == incident_id,
                        InAppNotification.event_type == "incident.collision",
                    )
                )
            )
            .scalars()
            .all()
        )


async def _fold(client, raw_owner: str, raw_loser: str, payload: dict):
    created = await client.post(
        "/incidents/ingest", headers={"X-OpsMender-Token": raw_owner}, json=payload
    )
    folded = await client.post(
        "/incidents/ingest", headers={"X-OpsMender-Token": raw_loser}, json=payload
    )
    assert created.status_code == folded.status_code == 200
    assert folded.json()["incident_id"] == created.json()["incident_id"]
    assert folded.json()["dedup_action"] == "skipped"
    return uuid.UUID(created.json()["incident_id"])


async def _noop_delivery(*args, **kwargs):
    return None


async def test_collision_selects_receiving_chain_by_receiving_service_priority(
    client: AsyncClient, app, admin_headers, monkeypatch
):
    # The owner is P2, so its incident is P2. The receiving service is P1 and
    # its only chain applies to P1: that is the chain it would have paged.
    owner = await _create_paged_service(
        client, app, admin_headers, name="PrioOwner", priority="P2"
    )
    loser = await _create_paged_service(
        client, app, admin_headers, name="PrioLoser", priority="P1"
    )
    chain_id, target = await _first_target(app, loser)
    await _unlink_all(app, loser)
    relinked = await client.post(
        f"/services/{loser['id']}/escalation-chains",
        json={"chain_id": str(chain_id), "applies_when": {"priorities": ["P1"]}},
        headers=admin_headers,
    )
    assert relinked.status_code == 201, relinked.text
    raw_owner, raw_loser = await _provider_tokens(app, owner, loser)
    monkeypatch.setattr("backend.bots.notifier.deliver_incident_text", _noop_delivery)

    incident_id = await _fold(
        client, raw_owner, raw_loser, {"title": "Priority split", "id": "prio-1"}
    )

    async with app.state.session_factory() as db:
        incident = await IncidentRepo.get_by_id(db, TEST_ORG_ID, incident_id)
        assert incident.priority == "P2"
    inbox = await _collision_inbox(app, incident_id)
    assert [row.user_id for row in inbox] == [target]


async def test_collision_notifies_shared_chain_owned_by_another_team(
    client: AsyncClient, app, admin_headers, monkeypatch
):
    owner = await _create_paged_service(client, app, admin_headers, name="ShareOwner")
    loser = await _create_paged_service(client, app, admin_headers, name="ShareLoser")
    platform = await _create_paged_service(
        client, app, admin_headers, name="SharePlatform"
    )
    platform_chain, platform_target = await _first_target(app, platform)
    await _unlink_all(app, loser)
    linked = await client.post(
        f"/services/{loser['id']}/escalation-chains",
        json={"chain_id": str(platform_chain)},
        headers=admin_headers,
    )
    assert linked.status_code == 201, linked.text
    raw_owner, raw_loser = await _provider_tokens(app, owner, loser)
    deliveries = []

    async def fake_delivery(*args, **kwargs):
        deliveries.append(kwargs)

    monkeypatch.setattr("backend.bots.notifier.deliver_incident_text", fake_delivery)

    incident_id = await _fold(
        client, raw_owner, raw_loser, {"title": "Shared chain", "id": "shared-chain-1"}
    )
    await asyncio.gather(*list(app.state.background_tasks))

    inbox = await _collision_inbox(app, incident_id)
    assert [row.user_id for row in inbox] == [platform_target]
    # The channel notice still goes to the receiving service's own team.
    assert len(deliveries) == 1
    assert deliveries[0]["team_id"] == uuid.UUID(loser["team_id"])


async def test_collision_inbox_respects_muted_incident_category(
    client: AsyncClient, app, admin_headers, monkeypatch
):
    from backend.db.repos import UserNotificationPrefRepo

    owner = await _create_paged_service(client, app, admin_headers, name="MuteOwner")
    loser = await _create_paged_service(client, app, admin_headers, name="MuteLoser")
    _, target = await _first_target(app, loser)
    async with app.state.session_factory() as db:
        await UserNotificationPrefRepo.upsert(
            db,
            TEST_ORG_ID,
            target,
            routing={"in_app": {"muted_categories": ["incident"]}},
        )
        await db.commit()
    raw_owner, raw_loser = await _provider_tokens(app, owner, loser)
    deliveries = []

    async def fake_delivery(*args, **kwargs):
        deliveries.append(kwargs)

    monkeypatch.setattr("backend.bots.notifier.deliver_incident_text", fake_delivery)

    incident_id = await _fold(
        client, raw_owner, raw_loser, {"title": "Muted", "id": "muted-1"}
    )
    await asyncio.gather(*list(app.state.background_tasks))

    assert await _collision_inbox(app, incident_id) == []
    # The mute is personal: the durable marker and the team notice still happen.
    assert len(deliveries) == 1
    async with app.state.session_factory() as db:
        logs = await IngestLogRepo.list_for_incident(db, TEST_ORG_ID, incident_id)
        assert any(row.error and row.error.startswith("collision:v1:") for row in logs)


async def test_timeline_shows_skipped_notes_without_error_status(
    client: AsyncClient, app, admin_headers, monkeypatch
):
    owner = await _create_paged_service(client, app, admin_headers, name="TlOwner")
    loser = await _create_paged_service(client, app, admin_headers, name="TlLoser")
    raw_owner, raw_loser = await _provider_tokens(app, owner, loser)
    monkeypatch.setattr("backend.bots.notifier.deliver_incident_text", _noop_delivery)

    incident_id = await _fold(
        client, raw_owner, raw_loser, {"title": "Timeline", "id": "timeline-1"}
    )

    timeline = await client.get(
        f"/incidents/{incident_id}/timeline", headers=admin_headers
    )
    assert timeline.status_code == 200, timeline.text
    evidence = [
        item
        for item in timeline.json()["items"]
        if item["event_type"] == "alert_evidence"
    ]
    assert len(evidence) == 2
    assert all(item["status"] != "error" for item in evidence)
    notes = [item for item in evidence if item["status"] == "skipped"]
    assert len(notes) == 1
    assert "TlLoser" in notes[0]["body"]
    assert "TlOwner" in notes[0]["body"]
    assert "collision:v1:" not in notes[0]["body"]


async def test_plain_text_sns_notification_still_opens_incident(
    client: AsyncClient, app
):
    raw, _ = await _create_token(
        app, provider="auto", name=f"plain-sns-{uuid.uuid4().hex[:6]}"
    )
    for subject, expected in (("Disk alert", "Disk alert"), (None, "Disk full")):
        response = await client.post(
            "/incidents/ingest",
            headers={"X-OpsMender-Token": raw},
            json={
                "Type": "Notification",
                "MessageId": uuid.uuid4().hex,
                "Subject": subject,
                "Message": "Disk full",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["dedup_action"] == "created"
        async with app.state.session_factory() as db:
            incident = await IncidentRepo.get_by_id(
                db, TEST_ORG_ID, uuid.UUID(response.json()["incident_id"])
            )
            assert incident.title == expected
            assert incident.status == "open"


async def test_excessive_nesting_is_rejected_with_422_not_500(client: AsyncClient, app):
    raw, token = await _create_token(
        app, provider="auto", name=f"deep-{uuid.uuid4().hex[:6]}"
    )

    def nested(depth: int) -> dict:
        root: dict = {}
        current = root
        for _ in range(depth):
            current["x"] = {}
            current = current["x"]
        return root

    deep_string = '{"a":' + "[" * 5000 + "]" * 5000 + "}"
    in_string = await client.post(
        "/incidents/ingest",
        headers={"X-OpsMender-Token": raw},
        json={"title": "Deep string", "data": deep_string},
    )
    outer = await client.post(
        "/incidents/ingest",
        headers={"X-OpsMender-Token": raw},
        json={"title": "Deep body", "details": nested(100)},
    )
    fine = await client.post(
        "/incidents/ingest",
        headers={"X-OpsMender-Token": raw},
        json={"title": "Normal depth", "details": nested(40)},
    )
    assert in_string.status_code == 422
    assert "nesting" in in_string.json()["error"]
    assert outer.status_code == 422
    assert "nesting" in outer.json()["error"]
    assert fine.status_code == 200
    assert fine.json()["dedup_action"] == "created"
    async with app.state.session_factory() as db:
        rows = (
            (
                await db.execute(
                    select(IngestLog).where(IngestLog.ingest_token_id == token.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 3
        assert sum(bool(row.error and "nesting" in row.error) for row in rows) == 2


async def test_unparseable_token_sample_returns_422(client: AsyncClient, admin_headers):
    bad_sample = {"Type": "Notification", "Message": "[]"}
    name = f"bad-sample-{uuid.uuid4().hex[:6]}"
    created = await client.post(
        "/ingest-tokens",
        json={"name": name, "provider": "auto", "sample_payload": bad_sample},
        headers=admin_headers,
    )
    assert created.status_code == 422, created.text
    tokens = await client.get("/ingest-tokens", headers=admin_headers)
    assert all(item["name"] != name for item in tokens.json()["items"])

    target = await client.post(
        "/ingest-tokens",
        json={"name": f"learn-{uuid.uuid4().hex[:6]}", "provider": "auto"},
        headers=admin_headers,
    )
    assert target.status_code == 201, target.text
    learned = await client.post(
        f"/ingest-tokens/{target.json()['id']}/learn-shape",
        json={"payload": bad_sample},
        headers=admin_headers,
    )
    assert learned.status_code == 422, learned.text
