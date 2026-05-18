"""Tests for the Teams Adaptive Card builders (Sprint 37 step 3)."""

from __future__ import annotations

import json
import uuid

import httpx
import pytest

from backend.auth import graph_oauth
from backend.db.models import Incident
from backend.paging.channels import TeamsGraphDMChannel
from backend.paging.teams_cards import (
    ACTION_ACK,
    ACTION_RESOLVE,
    ACTION_TAKE,
    ACTION_VIEW,
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

    def test_card_carries_three_submit_actions(self):
        card = build_page_card_adaptive(_incident())
        assert card["type"] == "AdaptiveCard"
        assert card["version"] == ADAPTIVE_CARD_VERSION
        submits = [
            a for a in card["actions"] if a["type"] == "Action.Submit"
        ]
        actions = {a["data"]["action"] for a in submits}
        assert actions == {ACTION_ACK, ACTION_TAKE, ACTION_RESOLVE}
        # No View button when base_url is unset.
        assert all(a["type"] != "Action.OpenUrl" for a in card["actions"])

    def test_view_button_added_when_base_url_set(self):
        card = build_page_card_adaptive(
            _incident(), base_url="https://ops.example.com"
        )
        open_urls = [
            a for a in card["actions"] if a["type"] == "Action.OpenUrl"
        ]
        assert len(open_urls) == 1
        assert open_urls[0]["url"].endswith("&from=teams")
        assert "/dashboard/incidents/detail" in open_urls[0]["url"]

    def test_incident_id_rides_in_action_data(self):
        inc = _incident()
        card = build_page_card_adaptive(inc)
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
        card = build_page_card_adaptive(_incident())
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
        assert parse_incident_id_from_action(
            {"action": ACTION_ACK, "incident_id": str(inc_id)}
        ) == inc_id

    def test_accepts_wrapped_value(self):
        inc_id = uuid.uuid4()
        assert parse_incident_id_from_action(
            {"value": {"action": ACTION_TAKE, "incident_id": str(inc_id)}}
        ) == inc_id

    def test_missing_id_returns_none(self):
        assert parse_incident_id_from_action({"action": ACTION_ACK}) is None

    def test_malformed_returns_none(self):
        assert parse_incident_id_from_action({"incident_id": "not-a-uuid"}) is None


class TestTeamsGraphChannelWithCard:
    """End-to-end: dispatcher passes the adaptive-card attachment via the
    ``blocks`` kwarg, the channel embeds it in the Graph message payload.
    """

    async def test_card_payload_lands_in_graph_post(self):
        graph_oauth.reset_token_cache()
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            if "/oauth2/v2.0/token" in str(request.url):
                return httpx.Response(
                    200,
                    json={
                        "access_token": "t",
                        "expires_in": 3600,
                        "token_type": "Bearer",
                    },
                )
            return httpx.Response(201, json={"id": "1"})

        transport = httpx.MockTransport(handler)

        def factory():
            return httpx.AsyncClient(transport=transport)

        ch = TeamsGraphDMChannel(
            tenant_id="t",
            client_id="c",
            client_secret="s",
            http_client_factory=factory,
        )
        inc = _incident()
        attachment = wrap_card_as_attachment(
            build_page_card_adaptive(inc, base_url="https://ops.example.com")
        )
        result = await ch.send(
            recipient="19:aaa@thread.v2",
            subject="hi",
            body="b",
            blocks=[attachment],
        )
        assert result.status == "sent"
        post = captured[1]
        body = json.loads(post.content.decode("utf-8"))
        assert body["attachments"][0]["contentType"] == ADAPTIVE_CARD_CONTENT_TYPE
        # The HTML body references the attachment by id.
        assert (
            f'<attachment id="{attachment["id"]}"></attachment>'
            in body["body"]["content"]
        )
        graph_oauth.reset_token_cache()
