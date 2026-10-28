"""M1-100: the Microsoft Teams (Workflows) Adaptive Card.

One card per incident change in the Workflows envelope, always under the size
limit. Its buttons are ``Action.OpenUrl`` signed links to OpsMender's confirm
page, offered only while the incident is open (Acknowledge only until someone
has), with View in OpsMender last on every card. Alert text is escaped, so it
can never become a link or a button. Pure: no database and no network.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import pytest

from backend.api.routes.action_links import ACTION_LABELS
from backend.bots.incident_card import EVENT_HEADLINES, event_headline
from backend.paging import teams_cards
from backend.paging.teams_cards import (
    ADAPTIVE_CARD_CONTENT_TYPE,
    CLOSED_STATUSES,
    EXHAUSTED_NOTE,
    FACT_VALUE_MAX,
    LINKS_NOTE,
    TEXT_CARD_MAX_CHARS,
    TEXT_CARD_MAX_LINES,
    WORKFLOWS_ACTIONS,
    WORKFLOWS_MAX_BODY_BYTES,
    build_workflows_card,
    build_workflows_minimal_card,
    build_workflows_text_card,
    encode_workflows_body,
    offered_actions,
    workflows_envelope,
    workflows_incident_body,
    workflows_text_body,
)

BASE = "https://ops.example.com"
INCIDENT_ID = uuid.UUID("3f2b9c1e-7d4a-4c55-9a0e-2b1f6c8d9e01")
ALL_ACTIONS = ["acknowledge", "resolve", "escalate", "start_ai_session"]
LINKS = {
    action: f"{BASE}/actions/confirm#token={action}-token" for action in ALL_ACTIONS
}
FACTS = [
    ("Service", "checkout"),
    ("Team", "Payments"),
    ("Org", "Acme"),
    ("Responder", "Awaiting Sam Lee"),
]
VIEW_URL = f"{BASE}/dashboard/incidents/detail?id={INCIDENT_ID}&from=teams"
EVIL = "[Ack](https://evil)"
EVIL_ESCAPED = r"\[Ack\]\(https://evil\)"


def _incident(**overrides):
    values = dict(
        id=INCIDENT_ID,
        title="Checkout 500s",
        description="Spike in 500s from the payment gateway\nSecond line",
        priority="P1",
        status="open",
        severity="critical",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _card(incident=None, **overrides):
    values = dict(
        headline="🚨 New incident",
        facts=FACTS,
        base_url=BASE,
        action_links=LINKS,
        acknowledged_by=None,
        exhausted=False,
    )
    values.update(overrides)
    return build_workflows_card(incident or _incident(), **values)


def _content(body: bytes) -> dict:
    return json.loads(body.decode("utf-8"))["attachments"][0]["content"]


def _texts(card: dict) -> list[str]:
    return [block["text"] for block in card["body"] if block["type"] == "TextBlock"]


def _facts(card: dict) -> list[tuple[str, str]]:
    fact_set = next(block for block in card["body"] if block["type"] == "FactSet")
    return [(fact["title"], fact["value"]) for fact in fact_set["facts"]]


def _types(node) -> list[str]:
    """Every ``type`` value anywhere in the JSON."""
    found: list[str] = []
    if isinstance(node, dict):
        if isinstance(node.get("type"), str):
            found.append(node["type"])
        for value in node.values():
            found += _types(value)
    elif isinstance(node, list):
        for item in node:
            found += _types(item)
    return found


# -- The envelope ------------------------------------------------------------


def test_envelope_has_only_the_workflows_keys():
    data = json.loads(encode_workflows_body(_card()))
    assert set(data) == {"type", "attachments"}
    assert data["type"] == "message"
    assert len(data["attachments"]) == 1
    attachment = data["attachments"][0]
    assert set(attachment) == {"contentType", "contentUrl", "content"}
    assert attachment["contentType"] == ADAPTIVE_CARD_CONTENT_TYPE
    assert attachment["contentUrl"] is None
    assert attachment["content"]["type"] == "AdaptiveCard"
    assert attachment["content"]["version"] == "1.4"


def test_body_is_the_compact_utf8_encoding():
    card = _card()
    body = encode_workflows_body(card)
    assert body == json.dumps(
        workflows_envelope(card), ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    # The headline emoji is sent as UTF-8, not as a \u escape.
    assert "🚨".encode("utf-8") in body


# -- Buttons -------------------------------------------------------------------


def test_card_buttons_are_open_url_links_in_order():
    card = _card()
    actions = card["actions"]
    assert [action["title"] for action in actions] == [
        "Acknowledge",
        "Resolve",
        "Escalate",
        "Start AI session",
        "View in OpsMender",
    ]
    assert all(action["type"] == "Action.OpenUrl" for action in actions)
    assert [action["url"] for action in actions[:4]] == [LINKS[a] for a in ALL_ACTIONS]
    assert [action.get("style") for action in actions] == [
        "positive",
        None,
        None,
        None,
        None,
    ]
    assert actions[-1]["url"] == VIEW_URL
    assert actions[-1]["url"].endswith("&from=teams")
    assert LINKS_NOTE in _texts(card)


def test_card_action_labels_match_the_confirm_page():
    assert {action: label for action, label, _style in WORKFLOWS_ACTIONS} == (
        ACTION_LABELS
    )


def test_offered_actions_rules():
    for status in sorted(CLOSED_STATUSES):
        assert offered_actions(status, None) == []
        assert offered_actions(status, "Sam Lee") == []
    assert offered_actions("open", None) == ALL_ACTIONS
    assert offered_actions("in_progress", None) == ALL_ACTIONS
    # Acknowledge goes once someone has acknowledged.
    assert offered_actions("open", "Sam Lee") == [
        "resolve",
        "escalate",
        "start_ai_session",
    ]
    card = _card(acknowledged_by="Sam Lee")
    assert "Acknowledge" not in [action["title"] for action in card["actions"]]


@pytest.mark.parametrize("status", ["resolved", "merged"])
def test_no_action_links_once_resolved_or_merged(status):
    # Even when links are handed in, a closed incident offers none.
    card = _card(_incident(status=status))
    assert card["actions"] == [
        {"type": "Action.OpenUrl", "title": "View in OpsMender", "url": VIEW_URL}
    ]
    assert LINKS_NOTE not in _texts(card)


def test_card_without_links_has_view_only_and_no_note():
    for links in (None, {}):
        card = _card(action_links=links)
        assert [action["title"] for action in card["actions"]] == ["View in OpsMender"]
        assert LINKS_NOTE not in _texts(card)


def test_no_submit_execute_showcard_or_toggle_anywhere():
    cards = [
        _card(),
        _card(acknowledged_by="Sam Lee", exhausted=True),
        build_workflows_minimal_card(_incident(), headline="x", base_url=BASE),
        build_workflows_text_card(
            f"*Hi*\nOpen incident: {BASE}/dashboard/incidents/detail?id=1",
            base_url=BASE,
        ),
    ]
    forbidden = {
        "Action.Submit",
        "Action.Execute",
        "Action.ShowCard",
        "Action.ToggleVisibility",
    }
    for card in cards:
        types = _types(workflows_envelope(card))
        assert not forbidden & set(types)
        assert {kind for kind in types if kind.startswith("Action.")} <= {
            "Action.OpenUrl"
        }


# -- Untrusted text --------------------------------------------------------------


def test_title_snippet_and_facts_are_markdown_escaped():
    card = _card(
        _incident(title=EVIL, description=EVIL, severity=EVIL),
        facts=[("Service", EVIL)],
    )
    texts = _texts(card)
    assert texts[0] == f"🚨 New incident: {EVIL_ESCAPED}"
    assert EVIL_ESCAPED in texts
    facts = dict(_facts(card))
    assert facts["Service"] == EVIL_ESCAPED
    assert facts["Severity"] == EVIL_ESCAPED
    # The raw link syntax appears nowhere in the card's text.
    for value in texts + [value for _title, value in _facts(card)]:
        assert "[Ack](" not in value


def test_fact_values_are_capped():
    card = _card(facts=[("Service", "x" * 500)])
    assert dict(_facts(card))["Service"] == "x" * FACT_VALUE_MAX


def test_org_fact_appears_once():
    card = _card()
    titles = [title for title, _value in _facts(card)]
    assert titles.count("Org") == 1
    assert titles == [
        "Priority",
        "Status",
        "Severity",
        "Service",
        "Team",
        "Org",
        "Responder",
        "Incident",
    ]
    assert dict(_facts(card))["Incident"] == str(INCIDENT_ID)


# -- Headlines -------------------------------------------------------------------


@pytest.mark.parametrize("event_type", sorted(EVENT_HEADLINES))
def test_headline_per_event(event_type):
    headline = event_headline(event_type)
    assert headline == EVENT_HEADLINES[event_type]
    card = _card(headline=headline)
    assert _texts(card)[0] == f"{EVENT_HEADLINES[event_type]}: Checkout 500s"


def test_exhausted_card_says_no_further_level():
    card = _card(
        headline=event_headline("incident.escalation_exhausted"), exhausted=True
    )
    assert _texts(card)[:2] == [
        "Escalation exhausted: Checkout 500s",
        EXHAUSTED_NOTE,
    ]
    assert EXHAUSTED_NOTE not in _texts(_card())


# -- Size --------------------------------------------------------------------------


def _long_links(size: int = 450) -> dict[str, str]:
    prefix = f"{BASE}/actions/confirm#token="
    return {action: prefix + "a" * (size - len(prefix)) for action in ALL_ACTIONS}


def test_worst_case_card_fits_under_the_limit():
    incident = _incident(title="𝔘" * 500, description="𝔘" * 300)
    facts = [(f"Fact {index}", "*" * 200) for index in range(6)]
    body = workflows_incident_body(
        incident,
        headline=event_headline("incident.escalation_exhausted"),
        facts=facts,
        acknowledged_by=None,
        base_url=BASE,
        action_links=_long_links(),
        exhausted=True,
    )
    assert len(body) <= WORKFLOWS_MAX_BODY_BYTES
    card = _content(body)
    # Still the full card: every link, the snippet and every fact.
    assert len(card["actions"]) == 5
    assert "𝔘" * 300 in _texts(card)
    assert len(_facts(card)) == 3 + len(facts) + 1


def test_oversized_card_falls_back_to_minimal_card(monkeypatch):
    monkeypatch.setattr(teams_cards, "WORKFLOWS_MAX_BODY_BYTES", 1500)
    body = workflows_incident_body(
        _incident(title="T" * 400),
        headline="🚨 New incident",
        facts=FACTS,
        acknowledged_by=None,
        base_url=BASE,
        action_links=_long_links(),
        exhausted=False,
    )
    assert len(body) <= 1500
    card = _content(body)
    assert _texts(card) == [f"🚨 New incident: {'T' * 200}"]
    assert [title for title, _value in _facts(card)] == ["Priority", "Status"]
    assert card["actions"] == [
        {"type": "Action.OpenUrl", "title": "View in OpsMender", "url": VIEW_URL}
    ]
    assert "/actions/confirm" not in body.decode("utf-8")


# -- Text cards ----------------------------------------------------------------------


def test_text_card_one_block_per_line_first_bold_capped():
    lines = "\n".join(f"line {index}" for index in range(60))
    card = build_workflows_text_card(f"*Line `one`*\n\n   \n{lines}", base_url=BASE)
    blocks = card["body"]
    assert len(blocks) == TEXT_CARD_MAX_LINES
    assert blocks[0] == {
        "type": "TextBlock",
        "wrap": True,
        "weight": "Bolder",
        "text": "Line one",
    }
    assert blocks[1] == {"type": "TextBlock", "wrap": True, "text": "line 0"}
    assert all("weight" not in block for block in blocks[1:])
    assert "`" not in json.dumps(card)
    assert card["actions"] == []

    long_card = build_workflows_text_card("a" * 5000, base_url=BASE)
    assert long_card["body"][0]["text"] == "a" * TEXT_CARD_MAX_CHARS

    empty = build_workflows_text_card("", base_url=BASE)
    assert empty["body"] == [{"type": "TextBlock", "wrap": True, "text": "OpsMender"}]


def test_text_card_turns_own_open_line_into_view_button():
    link = f"{BASE}/dashboard/incidents/detail?id={INCIDENT_ID}"
    card = build_workflows_text_card(
        f"*⏫ Incident escalated: Checkout 500s*\nPriority: P1\n\n"
        f"Open incident: {link}\nSign in to OpsMender to act.",
        base_url=BASE,
        incident_id=INCIDENT_ID,
    )
    assert card["actions"] == [
        {"type": "Action.OpenUrl", "title": "View in OpsMender", "url": link}
    ]
    assert _texts(card) == [
        "⏫ Incident escalated: Checkout 500s",
        "Priority: P1",
        "Sign in to OpsMender to act.",
    ]


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/a_(b)",
        # Starts with the OpsMender origin but is another host.
        "https://ops.example.com.evil.example/a_(b)",
    ],
)
def test_text_card_keeps_foreign_url_as_text(url):
    card = build_workflows_text_card(f"Alert\nOpen incident: {url}", base_url=BASE)
    assert card["actions"] == []
    escaped = url.replace("_", r"\_").replace("(", r"\(").replace(")", r"\)")
    assert _texts(card) == ["Alert", f"Open incident: {escaped}"]


@pytest.mark.parametrize(
    "planted",
    [
        f"{BASE}/dashboard/incidents/detail?id=other",
        f"{BASE}/actions/confirm#token=other-action",
        "https://evil.example/hook",
    ],
)
def test_alert_text_cannot_choose_the_view_button(planted):
    expected = f"{BASE}/dashboard/incidents/detail?id={INCIDENT_ID}"
    text = f"Alert\nOpen incident: {planted}\nOpen incident: {expected}"
    card = build_workflows_text_card(text, base_url=BASE, incident_id=INCIDENT_ID)
    assert card["actions"] == [
        {"type": "Action.OpenUrl", "title": "View in OpsMender", "url": expected}
    ]
    assert len(card["body"]) == 2
    assert build_workflows_text_card(text, base_url=BASE)["actions"] == []


def test_oversized_text_card_keeps_its_first_line_and_view_button(monkeypatch):
    monkeypatch.setattr(teams_cards, "WORKFLOWS_MAX_BODY_BYTES", 600)
    link = f"{BASE}/dashboard/incidents/detail?id={INCIDENT_ID}"
    text = "*Heading*\n" + "\n".join("x" * 80 for _ in range(30))
    body = workflows_text_body(
        f"{text}\nOpen incident: {link}", base_url=BASE, incident_id=INCIDENT_ID
    )
    card = _content(body)
    assert card["body"][0] == {
        "type": "TextBlock",
        "wrap": True,
        "weight": "Bolder",
        "text": "Heading",
    }
    assert len(card["body"]) == 2
    assert len(card["body"][1]["text"]) == 1000
    assert card["actions"][0]["url"] == link
