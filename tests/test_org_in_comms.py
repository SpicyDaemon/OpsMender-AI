"""Every comms surface names the org when given an org_name."""

from __future__ import annotations

import json
import uuid

from backend.bots.incident_card import build_incident_message
from backend.bots.notifier import _format_session_event
from backend.db.models import Incident
from backend.paging.slack_cards import build_page_card_blocks
from backend.paging.teams_cards import build_page_card_adaptive


def _incident() -> Incident:
    return Incident(
        id=uuid.uuid4(),
        org_id=uuid.uuid4(),
        title="DB pool exhausted",
        description="connections maxed",
        status="open",
        priority="P1",
        severity="high",
    )


def test_incident_channel_message_names_org():
    msg = build_incident_message(
        _incident(), event_type="incident.created", service_name="api", org_name="Acme"
    )
    assert "Org: Acme" in msg


def test_slack_page_card_names_org():
    blocks = build_page_card_blocks(_incident(), org_name="Acme")
    assert "Org: *Acme*" in json.dumps(blocks)


def test_teams_page_card_names_org():
    card = build_page_card_adaptive(_incident(), org_name="Acme")
    facts = card["body"][1]["facts"]
    assert {"title": "Org", "value": "Acme"} in facts


def test_session_post_names_org():
    text = _format_session_event(
        event_type="session.completed",
        session_id=uuid.uuid4(),
        session=None,
        incident=None,
        org_name="Acme",
    )
    assert "Org: `Acme`" in text


def test_omitted_when_no_org_name():
    # Builders stay backward-compatible: no org_name → no Org line.
    assert "Org:" not in build_incident_message(_incident(), service_name="api")
    assert "Org:" not in json.dumps(build_page_card_blocks(_incident()))
