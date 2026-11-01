"""M1-124 (L-04): a card channel that cannot edit its post gets one follow-up
when an incident it showed as resolved is reopened, and one sanitized notice
when the incident moves to another team.

The live round reopened a resolved incident and moved one to another team;
the Telegram group kept its last card unchanged both times, because the
edit-only refresh posted a follow-up only on link-button channels and
Telegram cannot edit a message. Ownership changes and repeated refreshes
still post nothing, and the notice the old team's channel gets names no
service, team or responder.
"""

# The database fixture comes from the channel notification tests.
# ruff: noqa: F811

from __future__ import annotations

import pytest

from backend.bots import notifier
from backend.bots.incident_card import MOVED_TO_ANOTHER_TEAM
from backend.db.repos import (
    BotConnectorRepo,
    IncidentNotificationReceiptRepo,
    IncidentRepo,
    TeamRepo,
)
from tests.test_incident_channel_notify import TEST_ORG_ID, factory  # noqa: F401

PUBLIC = "https://ops.example.com"


@pytest.fixture
def sent(monkeypatch):
    rows: list[dict] = []

    async def fake_send(*, bot_token, chat_id, text, **kwargs):
        rows.append({"chat_id": chat_id, "text": text})
        return True, None

    monkeypatch.setattr("backend.bots.telegram.send_message", fake_send)
    return rows


async def _telegram(factory, *, chat="-100A", team_ids=None, name="telegram"):
    config = {"allowed_chat_ids": [chat]}
    if team_ids:
        config.update(team_scope="teams", team_ids=[str(t) for t in team_ids])
    async with factory() as db:
        connector = await BotConnectorRepo.create(
            db,
            TEST_ORG_ID,
            name=name,
            platform="telegram",
            config=config,
            credentials={"bot_token": "BOT"},
            allowed_capabilities=["notifications"],
            status="configured",
            is_enabled=True,
        )
        await db.commit()
        return connector.id


async def _team(factory, name):
    async with factory() as db:
        team = await TeamRepo.create(db, TEST_ORG_ID, name=name, slug=name.lower())
        await db.commit()
        return team.id


async def _incident(factory, **values):
    async with factory() as db:
        incident = await IncidentRepo.create(
            db, TEST_ORG_ID, title="Checkout 500s", description="x", severity="high"
        )
        for key, value in values.items():
            setattr(incident, key, value)
        await db.commit()
        return incident.id


async def _set(factory, incident_id, **values):
    async with factory() as db:
        incident = await IncidentRepo.get_by_id(db, TEST_ORG_ID, incident_id)
        for key, value in values.items():
            setattr(incident, key, value)
        await db.commit()


async def _event(factory, incident_id, event_type, **kwargs):
    await notifier.deliver_incident_event(
        factory,
        org_id=TEST_ORG_ID,
        incident_id=incident_id,
        event_type=event_type,
        base_url=PUBLIC,
        **kwargs,
    )


async def _receipts(factory, incident_id):
    async with factory() as db:
        rows = await IncidentNotificationReceiptRepo.list_for_incident(
            db, TEST_ORG_ID, incident_id
        )
    return [(r.lifecycle_event, r.rendered_status) for r in rows]


async def test_a_reopen_posts_one_follow_up_and_ownership_changes_post_nothing(
    factory, sent
):
    await _telegram(factory)
    incident_id = await _incident(factory)
    await _event(factory, incident_id, "incident.created")
    await _set(factory, incident_id, status="resolved")
    await _event(factory, incident_id, "incident.resolved")
    assert len(sent) == 2

    # An ownership refresh while the status stays the same posts nothing.
    await _event(factory, incident_id, "incident.updated", edit_only=True)
    assert len(sent) == 2

    # The reopen: the last card said resolved, so one follow-up says open.
    await _set(factory, incident_id, status="open")
    await _event(factory, incident_id, "incident.updated", edit_only=True)
    assert len(sent) == 3
    assert "Incident updated: Checkout 500s" in sent[2]["text"]
    assert "Status: `open`" in sent[2]["text"]

    # The same state again posts nothing more.
    await _event(factory, incident_id, "incident.updated", edit_only=True)
    assert len(sent) == 3
    assert await _receipts(factory, incident_id) == [
        ("incident.created", "open"),
        ("incident.resolved", "resolved"),
        ("incident.updated", "open"),
    ]


async def test_the_team_an_incident_left_gets_one_notice_without_the_new_facts(
    factory, sent
):
    payments = await _team(factory, "Payments")
    search = await _team(factory, "Search")
    await _telegram(factory, chat="-100PAY", team_ids=[payments], name="pay")
    await _telegram(factory, chat="-100SRCH", team_ids=[search], name="srch")
    incident_id = await _incident(factory, team_id=payments)
    await _event(factory, incident_id, "incident.created")
    assert [m["chat_id"] for m in sent] == ["-100PAY"]

    # Moved to Search without a status change: the Payments channel learns
    # only that it moved; the Search channel gets nothing from this refresh.
    await _set(factory, incident_id, team_id=search)
    await _event(
        factory,
        incident_id,
        "incident.updated",
        edit_only=True,
        previous_team_id=payments,
    )
    assert [m["chat_id"] for m in sent] == ["-100PAY", "-100PAY"]
    notice = sent[1]["text"]
    assert MOVED_TO_ANOTHER_TEAM in notice
    assert "Search" not in notice and "Payments" not in notice
    assert "Responder" not in notice
    assert "Open incident: " in notice

    # The same move refreshed again posts nothing more.
    await _event(
        factory,
        incident_id,
        "incident.updated",
        edit_only=True,
        previous_team_id=payments,
    )
    assert len(sent) == 2
    assert await _receipts(factory, incident_id) == [
        ("incident.created", "open"),
        ("incident.updated", notifier.MOVED_AWAY_STATUS),
    ]

    # Later events reach the Search channel only.
    await _set(factory, incident_id, status="resolved")
    await _event(factory, incident_id, "incident.resolved")
    assert [m["chat_id"] for m in sent] == ["-100PAY", "-100PAY", "-100SRCH"]
