"""Tests for the Teams Adaptive Card builders (Sprint 37 step 3)."""

from __future__ import annotations

import uuid


from backend.db.models import Incident
from backend.paging.teams_cards import (
    ACTION_ACK,
    ACTION_ESCALATE,
    ACTION_RESOLVE,
    ACTION_START_AI_SESSION,
    ACTION_TAKE,
    ADAPTIVE_CARD_CONTENT_TYPE,
    ADAPTIVE_CARD_VERSION,
    build_graph_chat_message,
    build_page_card_adaptive,
    build_page_card_text,
    parse_incident_id_from_action,
    wrap_card_as_attachment,
)


TEST_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000aaa")


def _incident(**overrides) -> Incident:
    base = dict(
        id=uuid.uuid4(),
        org_id=TEST_ORG_ID,
        title="db on fire",
        description="latency p99 spiked",
        priority="P0",
        status="open",
        severity="high",
    )
    base.update(overrides)
    return Incident(**base)


class TestAdaptiveCardBuilder:
    def test_text_fallback_matches_slack_format(self):
        inc = _incident(title="boom")
        assert build_page_card_text(inc) == "[P0] OpsMender page: boom"

    def test_card_defaults_to_safe_link_fallback(self):
        card = build_page_card_adaptive(_incident())
        assert card["type"] == "AdaptiveCard"
        assert card["version"] == ADAPTIVE_CARD_VERSION
        assert card["actions"] == []

    def test_card_carries_four_verified_submit_actions(self):
        card = build_page_card_adaptive(
            _incident(),
            include_native_actions=True,
        )
        submits = [a for a in card["actions"] if a["type"] == "Action.Submit"]
        actions = {a["data"]["action"] for a in submits}
        assert actions == {
            ACTION_ACK,
            ACTION_RESOLVE,
            ACTION_ESCALATE,
            ACTION_START_AI_SESSION,
        }
        # No View button when base_url is unset.
        assert all(a["type"] != "Action.OpenUrl" for a in card["actions"])

    def test_view_button_added_when_base_url_set(self):
        card = build_page_card_adaptive(_incident(), base_url="https://ops.example.com")
        open_urls = [a for a in card["actions"] if a["type"] == "Action.OpenUrl"]
        assert len(open_urls) == 1
        assert open_urls[0]["url"].endswith("&from=teams")
        assert "/dashboard/incidents/detail" in open_urls[0]["url"]

    def test_incident_id_rides_in_action_data(self):
        inc = _incident()
        card = build_page_card_adaptive(inc, include_native_actions=True)
        for action in card["actions"]:
            if action["type"] == "Action.Submit":
                assert action["data"]["incident_id"] == str(inc.id)

    def test_facts_include_priority_and_status(self):
        card = build_page_card_adaptive(
            _incident(priority="P1", status="investigating", severity="high")
        )
        facts = next(b for b in card["body"] if b["type"] == "FactSet")["facts"]
        kv = {f["title"]: f["value"] for f in facts}
        assert kv["Priority"] == "P1"
        assert kv["Status"] == "Investigating"
        assert kv["Severity"] == "high"

    @staticmethod
    def _facts(inc, *, acknowledged: bool):
        from backend.bots.incident_card import incident_card_facts

        return incident_card_facts(
            inc,
            responder={
                "responder_state": "assigned" if acknowledged else "awaiting",
                "responder_display_name": "Sam Ops",
                "acknowledged_by_display_name": "Sam Ops" if acknowledged else None,
                "escalation_level": 0,
            },
            service_name="Checkout API",
            team_name="Payments",
            org_name=None,
        )

    def test_factset_names_responder_and_acknowledger(self):
        inc = _incident()
        msg = build_graph_chat_message(
            inc,
            include_native_actions=True,
            facts=self._facts(inc, acknowledged=True),
            acknowledged_by="Sam Ops",
        )
        card = msg["attachments"][0]["content"]
        facts = next(b for b in card["body"] if b["type"] == "FactSet")["facts"]
        assert [f["title"] for f in facts] == [
            "Priority",
            "Status",
            "Severity",
            "Service",
            "Team",
            "Responder",
            "Acknowledged by",
            "Incident",
        ]
        kv = {f["title"]: f["value"] for f in facts}
        assert kv["Responder"] == "Assigned to Sam Ops"
        assert kv["Acknowledged by"] == "Sam Ops"
        assert kv["Service"] == "Checkout API"
        assert kv["Team"] == "Payments"

    def test_acknowledge_action_absent_once_acknowledged(self):
        inc = _incident()
        before = build_page_card_adaptive(
            inc,
            include_native_actions=True,
            facts=self._facts(inc, acknowledged=False),
        )
        after = build_page_card_adaptive(
            inc,
            include_native_actions=True,
            facts=self._facts(inc, acknowledged=True),
            acknowledged_by="Sam Ops",
        )
        assert [a["data"]["action"] for a in before["actions"]] == [
            ACTION_ACK,
            ACTION_RESOLVE,
            ACTION_ESCALATE,
            ACTION_START_AI_SESSION,
        ]
        assert [a["data"]["action"] for a in after["actions"]] == [
            ACTION_RESOLVE,
            ACTION_ESCALATE,
            ACTION_START_AI_SESSION,
        ]
        resolved = build_page_card_adaptive(
            _incident(status="resolved"),
            include_native_actions=True,
            acknowledged_by="Sam Ops",
        )
        assert resolved["actions"] == []

    def test_fact_values_cannot_become_links(self):
        # Names are self-service: one set to a Markdown link shows as typed.
        card = build_page_card_adaptive(
            _incident(),
            facts=[
                ("Responder", "Assigned to [x](https://e.example) Ops"),
                ("Acknowledged by", "**[x](https://e.example)** _Ops_"),
            ],
            acknowledged_by="[x](https://e.example) Ops",
        )
        facts = next(b for b in card["body"] if b["type"] == "FactSet")["facts"]
        kv = {f["title"]: f["value"] for f in facts}
        assert kv["Responder"] == r"Assigned to \[x\]\(https://e.example\) Ops"
        assert kv["Acknowledged by"] == r"\*\*\[x\]\(https://e.example\)\*\* \_Ops\_"

    def test_description_snippet_truncated(self):
        long = "x" * 1000
        card = build_page_card_adaptive(_incident(description=long))
        text_blocks = [
            b
            for b in card["body"]
            if b.get("type") == "TextBlock" and b.get("isSubtle")
        ]
        assert text_blocks
        assert len(text_blocks[0]["text"]) <= 300


class TestWrappers:
    def test_wrap_card_as_attachment_assigns_unique_id(self):
        card = build_page_card_adaptive(
            _incident(),
            include_native_actions=True,
        )
        a = wrap_card_as_attachment(card)
        b = wrap_card_as_attachment(card)
        assert a["contentType"] == ADAPTIVE_CARD_CONTENT_TYPE
        assert a["content"] is card
        assert a["id"] != b["id"]

    def test_build_graph_chat_message_references_attachment(self):
        msg = build_graph_chat_message(_incident())
        assert msg["body"]["contentType"] == "html"
        att_id = msg["attachments"][0]["id"]
        assert f'<attachment id="{att_id}"></attachment>' in msg["body"]["content"]


class TestParseIncidentIdFromAction:
    def test_accepts_raw_data_dict(self):
        inc_id = uuid.uuid4()
        assert (
            parse_incident_id_from_action(
                {"action": ACTION_ACK, "incident_id": str(inc_id)}
            )
            == inc_id
        )

    def test_accepts_wrapped_value(self):
        inc_id = uuid.uuid4()
        assert (
            parse_incident_id_from_action(
                {"value": {"action": ACTION_TAKE, "incident_id": str(inc_id)}}
            )
            == inc_id
        )

    def test_missing_id_returns_none(self):
        assert parse_incident_id_from_action({"action": ACTION_ACK}) is None

    def test_malformed_returns_none(self):
        assert parse_incident_id_from_action({"incident_id": "not-a-uuid"}) is None
