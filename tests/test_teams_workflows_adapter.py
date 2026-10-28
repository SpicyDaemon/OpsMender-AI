"""M1-100: the Microsoft Teams (Workflows) adapter against a mocked workflow.

The adapter POSTs the exact card bytes to the decrypted workflow URL, maps the
trigger's answers (2xx accepted; 429, 408, 5xx and network errors retryable;
redirects and other 4xx permanent), never follows a redirect and never puts
the URL, its path, its query or its signature in an error, a log line or a
check. Card buttons are fresh signed links for the actions the incident
offers. The optional Microsoft Entra sign-in fetches a Power Automate token
once and drops it when the workflow refuses it. No Microsoft host is ever
contacted: every request goes to an ``httpx.MockTransport``.
"""

from __future__ import annotations

import json
import dataclasses
import logging
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.auth.graph_oauth import reset_token_cache
from backend.auth.secrets import encrypt_secret
from backend.bots.action_links import LINK_PATH, link_ttl, read_link
from backend.bots.connectors.teams_workflows import (
    DESTINATION,
    FLOW_SCOPE,
    TeamsWorkflowsAdapter,
    config_checks,
)
from backend.db.models import Base, BotConnector, Organization
from backend.db.repos import BotConnectorRepo, IncidentRepo
from backend.paging.teams_cards import (
    LINKS_NOTE,
    workflows_incident_body,
    workflows_text_body,
)

ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000100")
PUBLIC = "https://ops.example.com"
HOST = "prod-12.westus.logic.azure.com"
URL = (
    f"https://{HOST}/workflows/abc123/triggers/manual/paths/invoke"
    "?api-version=2016-06-01&sig=SECRETSIG"
)
SECRET_PARTS = ("/workflows/", "abc123", "invoke", "api-version", "sig=", "SECRETSIG")
CONFIG = SimpleNamespace(
    auth=SimpleNamespace(jwt_secret="test-secret", action_link_ttl_minutes=1440)
)
TOKEN_URL = "https://login.microsoftonline.com/"
TEST_TEXT = "OpsMender test message: your Notification Channel is configured correctly."


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("OPSMENDER_SECRET_KEY", "workflows-test-key")
    monkeypatch.setenv("OPSMENDER_PUBLIC_URL", PUBLIC)
    reset_token_cache()
    yield
    reset_token_cache()


class Workflow:
    """A mocked workflow trigger and Entra token endpoint. Records every
    request; the trigger answers with the next queued answer (202 by
    default), which may be an exception class to raise."""

    def __init__(self, *answers, token_answer=None):
        self.answers = list(answers)
        self.token_answer = token_answer
        self.requests: list[httpx.Request] = []

    @property
    def posts(self) -> list[httpx.Request]:
        return [r for r in self.requests if not str(r.url).startswith(TOKEN_URL)]

    @property
    def token_calls(self) -> list[httpx.Request]:
        return [r for r in self.requests if str(r.url).startswith(TOKEN_URL)]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if str(request.url).startswith(TOKEN_URL):
            answer = self.token_answer
            if answer is None:
                return httpx.Response(
                    200,
                    json={
                        "access_token": f"tok-{len(self.token_calls)}",
                        "expires_in": 3600,
                        "token_type": "Bearer",
                    },
                )
        else:
            answer = self.answers.pop(0) if self.answers else 202
        if isinstance(answer, httpx.Response):
            return answer
        if isinstance(answer, int):
            return httpx.Response(answer)
        # An exception class: its message carries the full URL, as httpx's do.
        if issubclass(answer, httpx.RequestError):
            raise answer(f"failed {request.url}", request=request)
        raise answer(f"failed {request.url}")

    def factory(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


def _adapter(workflow: Workflow, config_loader=lambda: CONFIG) -> TeamsWorkflowsAdapter:
    return TeamsWorkflowsAdapter(
        http_client_factory=workflow.factory, config_loader=config_loader
    )


def _connector(*, url: str | None = URL, entra: bool = False, **overrides):
    credentials: dict[str, str] = {}
    if url is not None:
        credentials["webhook_url"] = "enc:" + encrypt_secret(url)
    config: dict[str, str] = {}
    if entra:
        config = {"entra_tenant_id": "tenant-1", "entra_client_id": "client-1"}
        credentials["entra_client_secret"] = "enc:" + encrypt_secret("SECRET-VALUE")
    values = dict(
        id=uuid.uuid4(),
        org_id=ORG_ID,
        name="teams-cards",
        platform="teams_workflows",
        config=config,
        credentials=credentials,
        allowed_capabilities=["notifications"],
        status="configured",
        is_enabled=True,
    )
    values.update(overrides)
    return BotConnector(**values)


def _incident(**overrides):
    values = dict(
        id=uuid.uuid4(),
        title="Checkout 500s",
        description="Spike in 500s",
        priority="P1",
        status="open",
        severity="critical",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _card(request: httpx.Request) -> dict:
    return json.loads(request.content)["attachments"][0]["content"]


def _our_logs(caplog) -> str:
    """What OpsMender logged (httpx's own request lines are kept at WARNING
    by backend/logging_config.py in the app)."""
    return "\n".join(
        record.getMessage()
        for record in caplog.records
        if record.name.startswith("backend.")
    )


def _button_titles(request: httpx.Request) -> list[str]:
    return [action["title"] for action in _card(request)["actions"]]


async def _update(adapter, connector, incident=None, **kwargs):
    return await adapter.send_incident_update(
        connector,
        chat_id=DESTINATION,
        text="fallback",
        incident=incident if incident is not None else _incident(),
        **kwargs,
    )


# -- Form and contract ---------------------------------------------------------


def test_form_schema_keeps_secrets_in_credentials():
    fields = {field.name: field for field in TeamsWorkflowsAdapter.form_schema()}
    assert list(fields) == [
        "webhook_url",
        "entra_tenant_id",
        "entra_client_id",
        "entra_client_secret",
    ]
    assert (fields["webhook_url"].kind, fields["webhook_url"].group) == (
        "secret",
        "credentials",
    )
    assert fields["webhook_url"].required is True
    assert (
        fields["entra_client_secret"].kind,
        fields["entra_client_secret"].group,
    ) == (
        "secret",
        "credentials",
    )
    for name in ("entra_tenant_id", "entra_client_id"):
        assert (fields[name].kind, fields[name].group) == ("text", "config")
    assert not any(
        field.required for name, field in fields.items() if name != "webhook_url"
    )


def test_outbound_only():
    adapter = TeamsWorkflowsAdapter()
    with pytest.raises(HTTPException) as exc:
        adapter.verify_webhook(_connector(), headers={}, raw_body=b"")
    assert exc.value.status_code == 405
    assert adapter.parse_inbound({"text": "/ack"}) is None
    assert adapter.inline_reply(DESTINATION, "hi") is None
    # Any probe would post, and a posted card is never edited.
    assert not hasattr(adapter, "test_connection")
    assert not hasattr(adapter, "update_incident_update")
    assert adapter.fixed_destination == DESTINATION
    assert (adapter.posts_per_window, adapter.post_window) == (20, timedelta(minutes=5))


# -- Posting ---------------------------------------------------------------------


async def test_posts_the_encoded_bytes_to_the_decrypted_url():
    workflow = Workflow()
    incident = _incident()
    facts = [("Service", "checkout")]
    receipt = await _update(
        _adapter(workflow),
        _connector(),
        incident,
        facts=facts,
        event_type="incident.created",
    )
    assert receipt.ok is True
    [request] = workflow.requests
    assert request.method == "POST"
    assert str(request.url) == URL
    assert request.headers["content-type"] == "application/json"
    assert "authorization" not in request.headers
    assert request.content == workflows_incident_body(
        incident,
        headline="🚨 New incident",
        facts=facts,
        acknowledged_by=None,
        base_url=PUBLIC,
        action_links={},
        exhausted=False,
    )


@pytest.mark.parametrize("code", [200, 202])
async def test_any_2xx_is_accepted_without_a_message_id(code):
    receipt = await _update(_adapter(Workflow(code)), _connector())
    assert receipt.ok is True
    assert receipt.delivery_status == "accepted"
    assert receipt.can_update is False
    assert receipt.external_message_id is None
    assert receipt.external_channel_id == "workflow"
    assert receipt.retryable is False


STATUS_CASES = [
    (
        302,
        False,
        "Teams Workflows answered with a redirect (HTTP 302), which OpsMender "
        "does not follow",
    ),
    (400, False, "Teams Workflows refused the card (HTTP 400)"),
    (
        401,
        False,
        "Teams Workflows refused the caller (HTTP 401): check who may trigger the "
        "workflow, or the Microsoft Entra sign-in",
    ),
    (
        403,
        False,
        "Teams Workflows refused the caller (HTTP 403): check who may trigger the "
        "workflow, or the Microsoft Entra sign-in",
    ),
    (
        404,
        False,
        "Teams Workflows did not find the workflow (HTTP 404): it may be off or "
        "deleted, or its URL regenerated; paste the current URL",
    ),
    (408, True, "Teams Workflows did not answer (HTTP 408)"),
    (413, False, "Teams Workflows refused the card as too large (HTTP 413)"),
    (429, True, "Teams Workflows is throttling posts (HTTP 429)"),
    (500, True, "Teams Workflows did not answer (HTTP 500)"),
    (503, True, "Teams Workflows did not answer (HTTP 503)"),
    (httpx.ConnectError, True, "Teams Workflows did not answer (ConnectError)"),
    (httpx.ReadTimeout, True, "Teams Workflows did not answer (ReadTimeout)"),
    (RuntimeError, False, "Teams Workflows error: RuntimeError"),
]


@pytest.mark.parametrize(("answer", "retryable", "error"), STATUS_CASES)
async def test_status_mapping(answer, retryable, error):
    receipt = await _update(_adapter(Workflow(answer)), _connector())
    assert receipt.ok is False
    assert receipt.retryable is retryable
    assert receipt.error == error
    assert receipt.delivery_status is None


async def test_errors_never_contain_the_url_path_or_sig(caplog):
    caplog.set_level(logging.DEBUG)
    errors = []
    for answer, _retryable, _error in STATUS_CASES:
        receipt = await _update(_adapter(Workflow(answer)), _connector())
        errors.append(receipt.error)
    ok, error = await _adapter(Workflow(httpx.ConnectError)).send_message(
        _connector(), chat_id=DESTINATION, text="hi"
    )
    assert ok is False
    errors.append(error)
    for error in errors:
        assert error
        assert URL not in error
        for part in SECRET_PARTS:
            assert part not in error, (part, error)
    logs = _our_logs(caplog)
    assert "Teams Workflows post failed: RuntimeError" in logs
    for part in SECRET_PARTS:
        assert part not in logs


async def test_redirect_is_not_followed(monkeypatch):
    workflow = Workflow(
        httpx.Response(302, headers={"Location": "https://evil.example/steal"})
    )
    real_client = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(workflow.handler)
        return real_client(*args, **kwargs)

    # The adapter's own client, so its redirect setting is what is tested.
    monkeypatch.setattr(httpx, "AsyncClient", client)
    adapter = TeamsWorkflowsAdapter(config_loader=lambda: CONFIG)
    receipt = await _update(adapter, _connector())
    assert receipt.ok is False and receipt.retryable is False
    assert "redirect (HTTP 302)" in receipt.error
    assert [r.url.host for r in workflow.requests] == [HOST]


async def test_non_https_url_is_refused_without_a_request():
    workflow = Workflow()
    for url in (URL.replace("https://", "http://"), "ftp://x/y", "https:///no-host"):
        receipt = await _update(_adapter(workflow), _connector(url=url))
        assert receipt.ok is False and receipt.retryable is False
        assert receipt.error == "The Teams Workflows URL must use https"
    assert workflow.requests == []


async def test_missing_or_unreadable_url_is_refused_without_a_request():
    workflow = Workflow()
    adapter = _adapter(workflow)
    missing = await _update(adapter, _connector(url=None))
    assert missing.error == "The Teams Workflows URL is not configured"
    unreadable = await _update(
        adapter, _connector(url=None, credentials={"webhook_url": "enc:not-a-token"})
    )
    assert unreadable.error == "The stored workflow URL cannot be read; enter it again."
    assert not missing.ok and not unreadable.ok
    assert workflow.requests == []


# -- Signed links ------------------------------------------------------------------


@pytest.fixture
async def factory(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'workflows-links.db'}", echo=False
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db:
        db.add(Organization(id=ORG_ID, name="Workflows Org", slug="workflows-org"))
        await db.commit()
    yield session_factory
    await engine.dispose()


async def test_default_config_loader_signs_with_the_current_app_config(
    factory, monkeypatch
):
    from backend.api import deps
    from backend.api.app import create_app
    from backend.config_loader import AppConfig

    monkeypatch.setattr(deps, "_app_config", None)
    config = AppConfig.load()
    config.deployment = dataclasses.replace(
        config.deployment, environment="development"
    )
    config.auth.jwt_secret = "initial-test-signing-secret"
    app = create_app(config)
    # Settings can update this same object after the adapter was registered.
    app.state.config.auth.jwt_secret = "changed-test-signing-secret"
    async with factory() as db:
        connector = await BotConnectorRepo.create(
            db,
            ORG_ID,
            name="default-config",
            platform="teams_workflows",
            credentials={"webhook_url": URL},
            allowed_capabilities=["notifications"],
            is_enabled=True,
        )
        incident = await IncidentRepo.create(
            db, ORG_ID, title="Link config", description="Config test", priority="P1"
        )
        await db.commit()
    workflow = Workflow()
    adapter = TeamsWorkflowsAdapter(http_client_factory=workflow.factory)
    assert (await _update(adapter, connector, incident, links_ready=True)).ok
    assert len(_card(workflow.requests[0])["actions"]) == 5
    async with factory() as db:
        for button in _card(workflow.requests[0])["actions"][:-1]:
            token = button["url"].split("#token=", 1)[1]
            assert (
                await read_link(db, app.state.config, token, org_id=ORG_ID)
            ).state == "ready"


async def test_card_links_are_signed_confirm_links(factory):
    async with factory() as db:
        connector = await BotConnectorRepo.create(
            db,
            ORG_ID,
            name="teams-cards",
            platform="teams_workflows",
            credentials={"webhook_url": URL},
            allowed_capabilities=["notifications"],
            is_enabled=True,
        )
        incident = await IncidentRepo.create(
            db, ORG_ID, title="Checkout 500s", description="500s", priority="P1"
        )
        await db.commit()

    workflow = Workflow()
    before = datetime.now(timezone.utc)
    receipt = await _update(_adapter(workflow), connector, incident, links_ready=True)
    after = datetime.now(timezone.utc)
    assert receipt.ok is True
    actions = _card(workflow.requests[0])["actions"]
    expected = ["acknowledge", "resolve", "escalate", "start_ai_session"]
    assert len(actions) == len(expected) + 1
    async with factory() as db:
        for action, button in zip(expected, actions):
            assert button["url"].startswith(f"{PUBLIC}{LINK_PATH}#token=")
            token = button["url"].split("#token=", 1)[1]
            link = await read_link(db, CONFIG, token, org_id=ORG_ID)
            assert link.state == "ready"
            assert link.claims.action == action
            assert link.claims.incident_id == incident.id
            assert link.claims.connector_id == connector.id
            ttl = link_ttl(CONFIG)
            assert before + ttl - timedelta(seconds=1) <= link.claims.expires_at
            assert link.claims.expires_at <= after + ttl
    assert LINKS_NOTE in json.dumps(_card(workflow.requests[0]))


async def test_links_are_minted_only_when_ready_and_only_for_offered_actions(
    monkeypatch,
):
    minted: list[str] = []

    def fake_link(config, *, connector, incident_id, action):
        minted.append(action)
        return f"{PUBLIC}{LINK_PATH}#token={action}"

    monkeypatch.setattr("backend.bots.action_links.action_link", fake_link)
    workflow = Workflow()
    adapter = _adapter(workflow)

    await _update(adapter, _connector(), links_ready=False)
    assert minted == []
    assert _button_titles(workflow.requests[-1]) == ["View in OpsMender"]

    await _update(adapter, _connector(), links_ready=True, acknowledged_by="Sam Lee")
    assert minted == ["resolve", "escalate", "start_ai_session"]
    assert _button_titles(workflow.requests[-1]) == [
        "Resolve",
        "Escalate",
        "Start AI session",
        "View in OpsMender",
    ]

    minted.clear()
    await _update(adapter, _connector(), _incident(status="resolved"), links_ready=True)
    assert minted == []


async def test_link_minting_failure_posts_the_card_without_links(caplog):
    def broken_config():
        raise RuntimeError("no config with SECRETSIG")

    workflow = Workflow()
    receipt = await _update(
        _adapter(workflow, config_loader=broken_config), _connector(), links_ready=True
    )
    assert receipt.ok is True and receipt.delivery_status == "accepted"
    assert _button_titles(workflow.requests[0]) == ["View in OpsMender"]
    assert LINKS_NOTE not in json.dumps(_card(workflow.requests[0]))
    assert "links not signed: RuntimeError" in _our_logs(caplog)
    assert "SECRETSIG" not in _our_logs(caplog)


# -- Microsoft Entra sign-in ----------------------------------------------------------


async def test_entra_token_scope_header_and_cache():
    workflow = Workflow()
    adapter = _adapter(workflow)
    connector = _connector(entra=True)
    assert (await _update(adapter, connector)).ok is True
    assert (await _update(adapter, connector)).ok is True

    [token_call] = workflow.token_calls
    assert "tenant-1" in str(token_call.url)
    form = parse_qs(token_call.content.decode("utf-8"))
    assert form["grant_type"] == ["client_credentials"]
    assert form["client_id"] == ["client-1"]
    assert form["scope"] == [FLOW_SCOPE]
    assert [post.headers["authorization"] for post in workflow.posts] == [
        "Bearer tok-1",
        "Bearer tok-1",
    ]


@pytest.mark.parametrize(
    "config, credentials",
    [
        ({"entra_tenant_id": "tenant-1"}, {}),
        ({"entra_tenant_id": "tenant-1", "entra_client_id": "client-1"}, {}),
        ({}, {"entra_client_secret": "SECRET-VALUE"}),
    ],
)
async def test_partial_entra_settings_are_refused_without_a_request(
    config, credentials
):
    workflow = Workflow()
    connector = _connector(config=config)
    connector.credentials = {**connector.credentials, **credentials}
    receipt = await _update(_adapter(workflow), connector)
    assert receipt.ok is False and receipt.retryable is False
    assert receipt.error == (
        "Microsoft Entra sign-in needs the tenant ID, client ID and client secret "
        "together"
    )
    assert workflow.requests == []


async def test_entra_refusal_is_permanent_and_hides_the_secret():
    workflow = Workflow(
        token_answer=httpx.Response(
            401,
            json={
                "error": "invalid_client",
                "error_description": "client secret SECRET-VALUE is wrong",
            },
        )
    )
    receipt = await _update(_adapter(workflow), _connector(entra=True))
    assert receipt.ok is False and receipt.retryable is False
    assert receipt.error == (
        "Microsoft Entra refused the app credentials (HTTP 401): check the tenant "
        "ID, client ID and client secret"
    )
    assert "SECRET-VALUE" not in receipt.error
    assert workflow.posts == []


@pytest.mark.parametrize(
    "token_answer, error",
    [
        (503, "Microsoft Entra did not answer (HTTP 503)"),
        (429, "Microsoft Entra did not answer (HTTP 429)"),
        (httpx.ConnectError, "Microsoft Entra did not answer (ConnectError)"),
    ],
)
async def test_entra_outage_is_retryable(token_answer, error):
    workflow = Workflow(token_answer=token_answer)
    receipt = await _update(_adapter(workflow), _connector(entra=True))
    assert receipt.ok is False and receipt.retryable is True
    assert receipt.error == error
    assert workflow.posts == []


async def test_workflow_401_in_entra_mode_drops_the_cached_token():
    workflow = Workflow(401, 202, 202)
    adapter = _adapter(workflow)
    connector = _connector(entra=True)
    refused = await _update(adapter, connector)
    assert refused.ok is False and "HTTP 401" in refused.error
    assert (await _update(adapter, connector)).ok is True
    assert (await _update(adapter, connector)).ok is True
    # A new token after the refusal, then the cached one.
    assert len(workflow.token_calls) == 2
    assert [post.headers["authorization"] for post in workflow.posts] == [
        "Bearer tok-1",
        "Bearer tok-2",
        "Bearer tok-2",
    ]


# -- Text cards and failures --------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://attacker.example/hook",
        "https://logic.azure.com.attacker.example/hook",
        "https://evilpowerplatform.com/hook",
    ],
)
async def test_entra_token_never_sent_to_an_unknown_host(url):
    workflow = Workflow()
    adapter = _adapter(workflow)
    # Warm the shared token cache using a legitimate channel first.
    assert (await _update(adapter, _connector(entra=True))).ok
    before = len(workflow.requests)
    receipt = await _update(adapter, _connector(url=url, entra=True))
    assert not receipt.ok
    assert (
        receipt.error == "Microsoft Entra sign-in requires a known Power Automate host"
    )
    assert len(workflow.requests) == before
    assert {
        name: level for name, level, _ in config_checks(_connector(url=url, entra=True))
    }["entra"] == "fail"


async def test_send_message_posts_a_text_card():
    workflow = Workflow()
    ok, error = await _adapter(workflow).send_message(
        _connector(), chat_id=DESTINATION, text=TEST_TEXT
    )
    assert (ok, error) == (True, None)
    [request] = workflow.requests
    assert request.content == workflows_text_body(TEST_TEXT, base_url=PUBLIC)
    assert _card(request)["body"][0]["text"] == TEST_TEXT


async def test_incident_update_without_the_incident_posts_the_text():
    workflow = Workflow()
    link = f"{PUBLIC}/dashboard/incidents/detail?id={uuid.UUID(int=1)}"
    receipt = await _adapter(workflow).send_incident_update(
        _connector(),
        chat_id=DESTINATION,
        text=f"*Possible duplicate*\nOpen incident: {link}",
        incident=None,
        incident_id=uuid.UUID(int=1),
        links_ready=True,
        event_type="incident.collision",
    )
    assert receipt.ok is True
    card = _card(workflow.requests[0])
    assert card["body"][0]["text"] == "Possible duplicate"
    assert card["actions"] == [
        {"type": "Action.OpenUrl", "title": "View in OpsMender", "url": link}
    ]


async def test_adapter_never_raises():
    def broken_body() -> bytes:
        raise RuntimeError("bad card")

    workflow = Workflow()
    receipt = await _adapter(workflow)._post(_connector(), broken_body)
    assert receipt.ok is False
    assert receipt.retryable is False
    assert receipt.error == "Teams Workflows error: RuntimeError"
    assert workflow.requests == []


# -- Check configuration ------------------------------------------------------------


@pytest.mark.parametrize(
    "url, config, extra, public, expected",
    [
        (URL.replace("https://", "http://"), {}, {}, PUBLIC, {"workflow_url": "fail"}),
        (
            "https://outlook.office.com/webhook/abc/IncomingWebhook/x",
            {},
            {},
            PUBLIC,
            {"workflow_url": "fail"},
        ),
        (
            "https://acme.webhook.office.com/webhookb2/abc",
            {},
            {},
            PUBLIC,
            {"workflow_url": "fail"},
        ),
        (
            URL,
            {},
            {},
            PUBLIC,
            {"workflow_url": "pass", "entra": "pass", "public_url": "pass"},
        ),
        (
            "https://x.environment.api.powerplatform.com/powerautomate/abc?sig=s",
            {},
            {},
            PUBLIC,
            {"workflow_url": "pass"},
        ),
        (
            "https://hooks.example.net/abc?sig=s",
            {},
            {},
            PUBLIC,
            {"workflow_url": "warn"},
        ),
        (
            URL.replace("&sig=SECRETSIG", ""),
            {},
            {},
            PUBLIC,
            {"workflow_url": "pass", "workflow_signature": "warn"},
        ),
        (
            URL.replace("&sig=SECRETSIG", ""),
            {"entra_tenant_id": "t", "entra_client_id": "c"},
            {"entra_client_secret": "s"},
            PUBLIC,
            {"workflow_url": "pass", "entra": "pass"},
        ),
        (URL, {"entra_tenant_id": "t"}, {}, PUBLIC, {"entra": "fail"}),
        (
            URL,
            {},
            {"entra_client_secret": "enc:not-a-token"},
            PUBLIC,
            {"entra": "fail"},
        ),
        (URL, {}, {}, None, {"public_url": "warn"}),
        (URL, {}, {}, "http://localhost:3000", {"public_url": "warn"}),
        (URL, {}, {}, "http://127.0.0.1:8000", {"public_url": "warn"}),
        (URL, {}, {}, "http://ops.example.com", {"public_url": "warn"}),
    ],
)
def test_config_checks(monkeypatch, url, config, extra, public, expected):
    if public is None:
        monkeypatch.delenv("OPSMENDER_PUBLIC_URL", raising=False)
    else:
        monkeypatch.setenv("OPSMENDER_PUBLIC_URL", public)
    connector = _connector(url=url, config=config)
    connector.credentials = {**connector.credentials, **extra}
    checks = config_checks(connector)
    levels = {name: level for name, level, _detail in checks}
    names = [name for name, _level, _detail in checks]
    assert len(names) == len(set(names))
    for name, level in expected.items():
        assert levels.get(name) == level, (name, checks)
    if "workflow_signature" not in expected:
        assert "workflow_signature" not in levels
    details = dict((name, detail) for name, _level, detail in checks)
    if url.startswith("https://hooks.example.net"):
        assert details["workflow_url"].startswith(
            "hooks.example.net is not a Power Automate host OpsMender knows."
        )
    if public is None:
        assert details["public_url"] == (
            "OPSMENDER_PUBLIC_URL is not set to a public address, so card buttons "
            "open http://localhost:3000. Set it to the address people use to open "
            "OpsMender."
        )
    if public == "http://ops.example.com":
        assert details["public_url"] == (
            "Card buttons open http://ops.example.com over plain http."
        )


def test_config_checks_say_why_an_unreadable_url_fails():
    connector = _connector(url=None, credentials={"webhook_url": "enc:not-a-token"})
    assert config_checks(connector)[0] == (
        "workflow_url",
        "fail",
        "The saved workflow URL cannot be read with this server's secret key. "
        "Paste it again.",
    )


def test_config_checks_never_print_the_path_or_sig():
    for url in (
        URL,
        URL.replace("https://", "http://"),
        URL.replace(HOST, "hooks.example.net"),
        URL.replace(HOST, "outlook.office.com"),
        URL.replace("&sig=SECRETSIG", "&sp=SECRETSIG"),
    ):
        text = " ".join(
            detail for _name, _level, detail in config_checks(_connector(url=url))
        )
        for part in SECRET_PARTS:
            assert part not in text, (part, text)
