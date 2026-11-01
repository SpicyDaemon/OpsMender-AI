"""M1-121 (L-01): Telegram sends can never be refused for their content.

The live round lost an incident page whose title held a lone asterisk, a
completion notice whose model summary held unmatched entities, and a
co-pilot reply, all to legacy Markdown parsing. Every Telegram send now
goes through one rendering boundary that produces Telegram HTML with every
dynamic value escaped; pairs of our own delimiters become tags and any
other delimiter stays literal.
"""

# The notifier fixtures come from the channel notification tests.
# ruff: noqa: F811

from __future__ import annotations

import json
import re

import httpx
import pytest

from backend.bots import telegram
from backend.bots.connectors.telegram import TelegramAdapter
from backend.bots.telegram_markup import PARSE_MODE, render

# The content the live round sent, plus the hazards the plan lists.
HAZARDS = [
    "*Created: lone * asterisk*",
    "*Created: trailing underscore_*",
    "Incident: `back`tick title`",
    "Severity: `high` · Status: `open` [bracket",
    "Summary: *bold without end",
    "Summary: _x_ **double** `code` ```fence",
    "Emoji 🚒 and non-Latin тест 名前",
    "Tags: <script>alert('x')</script> & co",
    "Description " + "x" * 277 + "…",
]


def _legacy_markdown_balanced(text: str) -> bool:
    """A stand-in for Telegram's legacy Markdown parser: every ``*``, ``_``
    and backtick outside a code span must pair up on the message."""
    for marker in ("`", "*", "_"):
        if text.count(marker) % 2:
            return False
    return True


_TAG = re.compile(r"</?(b|code|pre|a)(\s+href=\"[^\"]*\")?>")


def _well_formed_html(text: str) -> bool:
    """Only Telegram's tags, properly nested, and nothing else that could be
    read as markup."""
    stack: list[str] = []
    pos = 0
    for match in _TAG.finditer(text):
        if "<" in text[pos : match.start()] or ">" in text[pos : match.start()]:
            return False
        name = match.group(1)
        if match.group(0).startswith("</"):
            if not stack or stack.pop() != name:
                return False
        else:
            stack.append(name)
        pos = match.end()
    tail = text[pos:]
    return not stack and "<" not in tail and ">" not in tail


class TestRender:
    def test_a_headline_keeps_an_asterisk_inside_the_title(self):
        assert render("*Created: lone * asterisk*") == "<b>Created: lone * asterisk</b>"

    def test_unmatched_delimiters_stay_literal(self):
        assert render("Incident: `back (unknown)") == "Incident: `back (unknown)"
        assert render("a_b and *c") == "a_b and *c"
        assert render("[bracket") == "[bracket"

    def test_pairs_become_tags(self):
        assert render("Session ID: `abc` *done*") == (
            "Session ID: <code>abc</code> <b>done</b>"
        )
        assert render("*Session `abc`*") == "<b>Session <code>abc</code></b>"
        assert render("**double**") == "<b>double</b>"
        # Several bold phrases on one line stay several.
        assert render("*a* and *b*") == "<b>a</b> and <b>b</b>"

    def test_code_is_literal_inside(self):
        assert render("`a*b_c<d`") == "<code>a*b_c&lt;d</code>"

    def test_html_characters_are_escaped(self):
        assert render("<script>&</script>") == "&lt;script&gt;&amp;&lt;/script&gt;"

    def test_a_bare_url_becomes_a_link(self):
        assert render("Open incident: https://ops.example.com/i?id=1&x=2.") == (
            "Open incident: "
            '<a href="https://ops.example.com/i?id=1&amp;x=2">'
            "https://ops.example.com/i?id=1&amp;x=2</a>."
        )

    def test_fences_become_pre_and_an_open_fence_is_literal(self):
        assert render("x\n```\na*b\n```\ny") == "x\n<pre>a*b</pre>\ny"
        assert render("x\n```\na*b") == "x\n```\na*b"

    @pytest.mark.parametrize("text", HAZARDS)
    def test_every_hazard_renders_as_well_formed_html(self, text):
        rendered = render(text)
        assert _well_formed_html(rendered)
        # The words of the message survive the rendering.
        for word in re.findall(r"[A-Za-z]{4,}", text):
            assert word in rendered


class TestSendBoundary:
    @pytest.fixture
    def outbound(self, monkeypatch):
        sent: list[dict] = []

        async def post(self, url, *, json=None, **kwargs):
            sent.append({"url": url, "payload": json})
            return httpx.Response(200, json={"ok": True})

        monkeypatch.setattr(httpx.AsyncClient, "post", post)
        return sent

    @pytest.mark.parametrize("text", HAZARDS)
    async def test_an_outbound_message_cannot_be_refused_for_its_content(
        self, outbound, text
    ):
        ok, error = await telegram.send_message(
            bot_token="123:abc", chat_id="-100", text=text
        )

        assert ok and error is None
        [call] = outbound
        payload = call["payload"]
        assert "123:abc" in call["url"]
        assert payload["chat_id"] == "-100"
        assert payload["disable_web_page_preview"] is True
        # Legacy Markdown would refuse the unmatched entity; HTML with every
        # dynamic value escaped cannot be refused for its formatting.
        if payload["parse_mode"] != PARSE_MODE:
            assert _legacy_markdown_balanced(payload["text"]), text
        assert payload["parse_mode"] == PARSE_MODE
        assert _well_formed_html(payload["text"])

    @pytest.mark.parametrize("text", HAZARDS)
    def test_a_webhook_reply_cannot_be_refused_for_its_content(self, text):
        reply = TelegramAdapter().inline_reply("-100", text)

        assert reply["method"] == "sendMessage"
        assert reply["chat_id"] == "-100"
        if reply["parse_mode"] != PARSE_MODE:
            assert _legacy_markdown_balanced(reply["text"]), text
        assert reply["parse_mode"] == PARSE_MODE
        assert _well_formed_html(reply["text"])
        json.dumps(reply)  # what the webhook route returns

    async def test_a_refused_send_still_reports_the_provider_error(self, monkeypatch):
        async def post(self, url, *, json=None, **kwargs):
            return httpx.Response(
                400, json={"ok": False, "description": "chat not found"}
            )

        monkeypatch.setattr(httpx.AsyncClient, "post", post)
        ok, error = await telegram.send_message(bot_token="t", chat_id="-1", text="x")
        assert not ok and error.startswith("http 400")


# The notifier's own path: an incident page and a completion notice built
# from stored content reach Telegram through the boundary.
from tests.test_incident_channel_notify import (  # noqa: E402, F401
    TEST_ORG_ID,
    _make_connector,
    _make_incident,
    factory,
)


class TestNotifierBoundary:
    async def test_an_incident_page_with_a_hazardous_title_is_delivered(
        self, factory, monkeypatch
    ):
        from backend.bots import notifier
        from backend.db.repos import BotActionAuditRepo

        sent: list[dict] = []

        async def post(self, url, *, json=None, **kwargs):
            sent.append(json)
            return httpx.Response(200, json={"ok": True})

        monkeypatch.setattr(httpx.AsyncClient, "post", post)
        connector_id = await _make_connector(
            factory, capabilities=["notifications"], allowed_chat_ids=["-100A"]
        )
        incident_id = await _make_incident(
            factory, title="ratings down: lone * asterisk_title `tick [bracket"
        )

        await notifier.deliver_incident_event(
            factory,
            org_id=TEST_ORG_ID,
            incident_id=incident_id,
            event_type="incident.created",
            base_url="https://ops.example.com",
        )

        [payload] = sent
        assert payload["parse_mode"] == PARSE_MODE
        assert _well_formed_html(payload["text"])
        assert "lone * asterisk_title `tick [bracket" in payload["text"]
        assert (
            '<a href="https://ops.example.com/dashboard/incidents/detail?id='
            in payload["text"]
        )
        async with factory() as db:
            audits = await BotActionAuditRepo.list_by_connector(
                db, TEST_ORG_ID, connector_id
            )
        assert [(a.command, a.status) for a in audits] == [
            ("notify:incident.created", "ok")
        ]

    def test_a_completion_summary_with_unmatched_entities_renders(self):
        from types import SimpleNamespace

        from backend.bots.notifier import _format_session_event

        session = SimpleNamespace(
            status="completed",
            summary="Restarted *ratings*; see `pod_x` and http://x.test/a_b (ok",
        )
        text = _format_session_event(
            event_type="session.completed",
            session_id="s1",
            session=session,
            incident=SimpleNamespace(title="lone * title", severity="high"),
            base_url="https://ops.example.com",
        )
        rendered = render(text)
        assert _well_formed_html(rendered)
        assert "<b>AI session completed</b>" in rendered
        assert "lone * title" in rendered
