"""M1-17: requests for people from other teams to join as responders.

The handling team's operators (and admins, and the owner) ask someone from
another team; that person gets an Inbox notice and an email naming who asked,
and only they accept (joining as a responder) or decline. A pending request
holds one of the 3 slots until it is answered, expires after 30 minutes or
the incident closes; the requester learns the outcome.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from backend.db.models import (
    InAppNotification,
    IncidentComment,
    IncidentResponder,
    IncidentResponderRequest,
)
from backend.paging import responders as _responders
from tests.test_ownership_lifecycle import (
    TEST_ORG_ID,
    World,
    _headers,
    _user,
    app as _app_fixture,
    client as _client_fixture,
    world as _world_fixture,
)
from tests.test_reassign_responders_part15 import _incident_on, _team

app = pytest.fixture(_app_fixture.__wrapped__)
client = pytest.fixture(_client_fixture.__wrapped__)
world = pytest.fixture(_world_fixture.__wrapped__)

NOTE = "Can you check the Data pipeline lag?"

# Platform handles the incident: level1 (its chain pages them) and level3 are
# its members. level2 is on Data.


async def _setup(w: World) -> uuid.UUID:
    platform, service, chain = await _team(
        w, "Platform", members=[w.level1, w.level3], levels=[w.level1]
    )
    await _team(w, "Data", members=[w.level2])
    return await _incident_on(w, service, chain)


async def _ask(w: World, incident_id, user_ids, name="lc-l3", message=NOTE):
    return await w.client.post(
        f"/incidents/{incident_id}/responder-requests",
        json={"user_ids": [str(u) for u in user_ids], "message": message},
        headers=await _headers(w.client, name),
    )


async def _answer(w: World, incident_id, request_id, name, verb):
    return await w.client.post(
        f"/incidents/{incident_id}/responder-requests/{request_id}/{verb}",
        headers=await _headers(w.client, name) if name != "admin" else w.admin,
    )


async def _state(w: World, incident_id) -> dict:
    async with w.app.state.session_factory() as db:
        requests = (
            await db.execute(
                select(
                    IncidentResponderRequest.user_id, IncidentResponderRequest.status
                )
                .where(IncidentResponderRequest.incident_id == incident_id)
                .order_by(IncidentResponderRequest.created_at)
            )
        ).all()
        responders = (
            await db.execute(
                select(IncidentResponder.user_id, IncidentResponder.added_by).where(
                    IncidentResponder.incident_id == incident_id
                )
            )
        ).all()
        notices = (
            await db.execute(
                select(
                    InAppNotification.user_id,
                    InAppNotification.event_type,
                    InAppNotification.title,
                ).where(InAppNotification.incident_id == incident_id)
            )
        ).all()
        comments = (
            await db.execute(
                select(IncidentComment.body).where(
                    IncidentComment.incident_id == incident_id
                )
            )
        ).scalars()
        return {
            "requests": [tuple(row) for row in requests],
            "responders": sorted(tuple(row) for row in responders),
            "notices": sorted(tuple(row) for row in notices),
            "comments": sorted(comments),
        }


async def test_a_teammate_asks_someone_from_another_team(world):
    incident_id = await _setup(world)
    resp = await _ask(world, incident_id, [world.level2])
    assert resp.status_code == 201, resp.text
    (item,) = resp.json()["items"]
    assert (item["username"], item["requested_by_username"], item["status"]) == (
        "lc-l2",
        "lc-l3",
        "pending",
    )
    state = await _state(world, incident_id)
    assert state["requests"] == [(world.level2, "pending")]
    assert state["responders"] == []
    assert (
        world.level2,
        "incident.responder_request",
        "lc-l3 asked you to join: orders db is slow",
    ) in state["notices"]
    assert f"Asked lc-l2 to join as a responder. Message: {NOTE}" in state["comments"]

    async with world.app.state.session_factory() as db:
        row = (
            await db.execute(
                select(IncidentResponderRequest).where(
                    IncidentResponderRequest.incident_id == incident_id
                )
            )
        ).scalar_one()
    assert row.expires_at - row.created_at == timedelta(minutes=30)


async def test_only_the_person_asked_can_accept(world):
    incident_id = await _setup(world)
    request_id = (await _ask(world, incident_id, [world.level2])).json()["items"][0][
        "id"
    ]
    for name in ("lc-l1", "admin"):
        refused = await _answer(world, incident_id, request_id, name, "accept")
        assert refused.status_code == 403, refused.text
        assert refused.json()["detail"] == "Only lc-l2 can answer this request."
    assert (await _state(world, incident_id))["requests"] == [(world.level2, "pending")]

    accepted = await _answer(world, incident_id, request_id, "lc-l2", "accept")
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "accepted"
    state = await _state(world, incident_id)
    assert state["requests"] == [(world.level2, "accepted")]
    assert state["responders"] == [(world.level2, world.level3)]
    assert (
        world.level3,
        "incident.responder_request_answered",
        "lc-l2 joined as a responder: orders db is slow",
    ) in state["notices"]
    assert "Accepted the request and joined the responders." in state["comments"]

    again = await _answer(world, incident_id, request_id, "lc-l2", "decline")
    assert again.status_code == 409
    assert again.json()["detail"] == "This request was already accepted."


async def test_a_declined_request_adds_nobody(world):
    incident_id = await _setup(world)
    request_id = (await _ask(world, incident_id, [world.level2])).json()["items"][0][
        "id"
    ]
    declined = await _answer(world, incident_id, request_id, "lc-l2", "decline")
    assert declined.status_code == 200, declined.text
    state = await _state(world, incident_id)
    assert state["requests"] == [(world.level2, "declined")]
    assert state["responders"] == []
    assert (
        world.level3,
        "incident.responder_request_answered",
        "lc-l2 declined to join: orders db is slow",
    ) in state["notices"]


async def test_a_pending_request_holds_a_slot(world):
    incident_id = await _setup(world)
    mates = []
    for name in ("rq-mate-1", "rq-mate-2", "rq-mate-3"):
        mates.append(await _user(world.app, name))
    async with world.app.state.session_factory() as db:
        from backend.db.repos import IncidentRepo, ServiceRepo, TeamRepo

        incident = await IncidentRepo.get_by_id(db, TEST_ORG_ID, incident_id)
        service = await ServiceRepo.get_by_id(db, TEST_ORG_ID, incident.service_id)
        for mate in mates:
            await TeamRepo.add_member(db, TEST_ORG_ID, service.team_id, user_id=mate)
        await db.commit()
    member = await _headers(world.client, "lc-l3")
    added = await world.client.post(
        f"/incidents/{incident_id}/responders",
        json={"user_ids": [str(mates[0]), str(mates[1])]},
        headers=member,
    )
    assert added.status_code == 201, added.text
    asked = await _ask(world, incident_id, [world.level2])
    assert asked.status_code == 201, asked.text

    full = await world.client.post(
        f"/incidents/{incident_id}/responders",
        json={"user_ids": [str(mates[2])]},
        headers=member,
    )
    assert full.status_code == 409, full.text
    assert "counting people asked who haven't answered yet" in full.json()["detail"]

    request_id = asked.json()["items"][0]["id"]
    assert (
        await _answer(world, incident_id, request_id, "lc-l2", "decline")
    ).status_code == 200
    added = await world.client.post(
        f"/incidents/{incident_id}/responders",
        json={"user_ids": [str(mates[2])]},
        headers=member,
    )
    assert added.status_code == 201, added.text


async def test_requests_expire_after_30_minutes(world):
    incident_id = await _setup(world)
    request_id = (await _ask(world, incident_id, [world.level2])).json()["items"][0][
        "id"
    ]
    async with world.app.state.session_factory() as db:
        row = await db.get(IncidentResponderRequest, uuid.UUID(request_id))
        expires_at = row.expires_at
        before = await _responders.expire_due_requests(
            db, at=expires_at - timedelta(seconds=1)
        )
        await db.commit()
    assert before == 0
    assert (await _state(world, incident_id))["requests"] == [(world.level2, "pending")]

    async with world.app.state.session_factory() as db:
        expired = await _responders.expire_due_requests(db, at=expires_at)
        await db.commit()
    assert expired == 1
    state = await _state(world, incident_id)
    assert state["requests"] == [(world.level2, "expired")]
    assert (
        world.level3,
        "incident.responder_request_answered",
        "lc-l2 didn't answer within 30 minutes: orders db is slow",
    ) in state["notices"]
    late = await _answer(world, incident_id, request_id, "lc-l2", "accept")
    assert late.status_code == 409
    assert late.json()["detail"] == "This request was already expired."
    assert state["responders"] == []


async def test_closing_the_incident_cancels_pending_requests(world):
    incident_id = await _setup(world)
    request_id = (await _ask(world, incident_id, [world.level2])).json()["items"][0][
        "id"
    ]
    resolved = await world.client.patch(
        f"/incidents/{incident_id}", json={"status": "resolved"}, headers=world.admin
    )
    assert resolved.status_code == 200, resolved.text
    assert (await _state(world, incident_id))["requests"] == [
        (world.level2, "cancelled")
    ]
    late = await _answer(world, incident_id, request_id, "lc-l2", "accept")
    assert late.status_code == 409


async def test_teammates_are_added_directly_not_asked(world):
    incident_id = await _setup(world)
    resp = await _ask(world, incident_id, [world.level1])
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == "Add lc-l1 directly; no request is needed."
    assert (await _state(world, incident_id))["requests"] == []


async def test_someone_outside_the_team_cannot_ask(world):
    incident_id = await _setup(world)
    resp = await _ask(world, incident_id, [world.level2], name="lc-l2")
    assert resp.status_code == 403, resp.text
    async with world.app.state.session_factory() as db:
        count = await db.scalar(
            select(func.count()).select_from(IncidentResponderRequest)
        )
    assert count == 0


async def test_the_request_email_names_who_asked(world, monkeypatch):
    sent: list[dict] = []

    class _Channel:
        async def send(self, *, recipient, subject, body):
            sent.append({"recipient": recipient, "subject": subject, "body": body})
            return type("Attempt", (), {"status": "sent", "error": None})()

    async def _settings(*_args, **_kwargs):
        return object()

    monkeypatch.setattr("backend.reports.email.resolve_email_settings", _settings)
    monkeypatch.setattr(
        "backend.reports.email.build_email_channel", lambda _settings: _Channel()
    )
    monkeypatch.setattr(
        world.app.state.config.people, "public_base_url", "https://ops.example.test"
    )
    incident_id = await _setup(world)
    # A forged host header never reaches the link.
    resp = await world.client.post(
        f"/incidents/{incident_id}/responder-requests",
        json={"user_ids": [str(world.level2)], "message": NOTE},
        headers={
            **await _headers(world.client, "lc-l3"),
            "X-Forwarded-Host": "phish.example.test",
        },
    )
    assert resp.status_code == 201, resp.text
    (mail,) = sent
    assert mail["recipient"] == "lc-l2@test.com"
    assert mail["subject"] == "lc-l3 asked you to help with orders db is slow"
    assert "lc-l3 asked you to help as a responder" in mail["body"]
    assert f"Message from lc-l3: {NOTE}" in mail["body"]
    assert (
        f"https://ops.example.test/dashboard/incidents/detail?id={incident_id}"
        in mail["body"]
    )
    assert "phish.example.test" not in mail["body"]


async def test_without_a_public_url_the_email_has_no_link(world, monkeypatch):
    sent: list[str] = []

    class _Channel:
        async def send(self, *, recipient, subject, body):
            sent.append(body)
            return type("Attempt", (), {"status": "sent", "error": None})()

    async def _settings(*_args, **_kwargs):
        return object()

    monkeypatch.setattr("backend.reports.email.resolve_email_settings", _settings)
    monkeypatch.setattr(
        "backend.reports.email.build_email_channel", lambda _settings: _Channel()
    )
    monkeypatch.setattr(world.app.state.config.people, "public_base_url", None)
    monkeypatch.delenv("OPSMENDER_PUBLIC_URL", raising=False)
    incident_id = await _setup(world)
    resp = await world.client.post(
        f"/incidents/{incident_id}/responder-requests",
        json={"user_ids": [str(world.level2)]},
        headers={
            **await _headers(world.client, "lc-l3"),
            "Host": "phish.example.test",
        },
    )
    assert resp.status_code == 201, resp.text
    (body,) = sent
    assert "Open the incident in OpsMender to accept or decline." in body
    assert "http" not in body and "phish.example.test" not in body


async def test_without_email_settings_the_request_still_stands(world):
    incident_id = await _setup(world)
    resp = await _ask(world, incident_id, [world.level2])
    assert resp.status_code == 201, resp.text
    assert (await _state(world, incident_id))["requests"] == [(world.level2, "pending")]


async def test_the_paging_panel_lists_pending_requests(world):
    incident_id = await _setup(world)
    await _ask(world, incident_id, [world.level2])
    panel = await world.client.get(
        f"/incidents/{incident_id}/paging",
        headers=await _headers(world.client, "lc-l2"),
    )
    assert panel.status_code == 200, panel.text
    (item,) = panel.json()["responder_requests"]
    assert (item["user_id"], item["requested_by_username"], item["status"]) == (
        str(world.level2),
        "lc-l3",
        "pending",
    )
