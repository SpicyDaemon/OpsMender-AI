"""Tool-source overlap on one service (v1.1.1 Part A).

When a service allowlists an MCP server and a native connector that reach the
same system, both tool surfaces reach the model. These tests pin that
behaviour (no dedupe, no precedence), prove the documented escape hatch, pin
the side-effect asymmetry between the two routes, and cover the advisory
warning in the services API and ``opsmender doctor``.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import bcrypt
import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from mcp.types import CallToolResult, TextContent, Tool
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.api.app import create_app
from backend.api.deps import get_db, set_mcp_pool, set_session_factory
from backend.api import session_runner
from backend.api.session_runner import build_combined_tool_caller
from backend.config_loader import set_env_path
from backend.db.models import (
    Base,
    IncidentIntegrationLink,
    Organization,
    TicketSyncState,
)
from backend.db.repos import (
    IncidentRepo,
    IntegrationConnectorRepo,
    MCPServerRepo,
    ServiceRepo,
    SessionRepo,
    SkillRepo,
    TeamRepo,
    UserRepo,
)
from backend.doctor import check_tool_source_overlaps, exit_code
from backend.integrations import tools as integration_tools
from backend.integrations.atlassian import JiraAdapter
from backend.integrations.base import IntegrationCapability
from backend.integrations.overlap import (
    find_tool_source_overlaps,
    overlaps_for_service,
)
from backend.integrations.tools import (
    IntegrationToolDescriptor,
    IntegrationToolRuntime,
    merge_integration_skill,
)
from backend.skills.parser import OperationClassification, loads
from backend.tiers.enforcement import check
from backend.tiers.sandbox import Tier0SandboxViolation

TEST_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")

# An operator-authored MCP skill for an Atlassian-style MCP server. Its
# create policy is deliberately different from the native connector baseline
# (T1 autonomous here vs approval there) so independence is observable.
MCP_SKILL = """---
version: "1"
environment: overlap-test
operations:
  - tool: jira_search
    classification: safe
    tiers:
      T0: {enabled: true, mode: autonomous}
      T1: {enabled: true, mode: autonomous}
      T2: {enabled: true, mode: advisory}
  - tool: jira_create_issue
    classification: caution
    tiers:
      T0: {enabled: false, mode: blocked}
      T1: {enabled: true, mode: autonomous}
      T2: {enabled: false, mode: blocked}
---
"""

# The documented escape hatch: keep the MCP route by denying the native
# create op in the connector-bound skill.
CONNECTOR_DENY_SKILL = """---
version: "1"
environment: jira-connector
operations:
  - tool: "integration__jira__create_issue__*"
    classification: destructive
    deny: true
---
"""


# ---------------------------------------------------------------------------
# Detection (pure)
# ---------------------------------------------------------------------------


def _server(
    name, *, command=None, args=None, url=None, env=None, token=None, active=True
):
    return SimpleNamespace(
        id=uuid.uuid4(),
        name=name,
        command=command,
        args=args or [],
        url=url,
        env_vars=env or {},
        token=token,
        is_active=active,
    )


def _connector(kind, name=None, *, enabled=True):
    return SimpleNamespace(
        id=uuid.uuid4(), kind=kind, name=name or f"{kind} connector", is_enabled=enabled
    )


def test_atlassian_mcp_overlaps_native_jira():
    server = _server("atlassian", command="npx", args=["-y", "mcp-atlassian"])
    jira = _connector("jira", "Jira DOT")
    (overlap,) = find_tool_source_overlaps([server], [jira])
    assert overlap.kind == "jira"
    assert overlap.connector_id == jira.id
    assert overlap.mcp_server_id == server.id
    assert overlap.matched_term == "atlassian"


def test_kind_found_in_command_args_or_url():
    github_server = _server(
        "source-control",
        command="docker",
        args=["run", "ghcr.io/github/github-mcp-server"],
    )
    k8s_server = _server("cluster", url="https://k8s-mcp.internal.example/sse")
    github = _connector("github")
    kubernetes = _connector("kubernetes")
    overlaps = find_tool_source_overlaps(
        [github_server, k8s_server], [github, kubernetes]
    )
    assert {(o.kind, o.mcp_server_name, o.matched_term) for o in overlaps} == {
        ("github", "source-control", "github"),
        ("kubernetes", "cluster", "k8s"),
    }


def test_multiword_kind_matches_separated_and_compact_forms():
    separated = _server("azure-devops-mcp")
    compact = _server("tools", command="mcp-server-azuredevops")
    ado = _connector("azure_devops")
    overlaps = find_tool_source_overlaps([separated, compact], [ado])
    assert sorted(o.mcp_server_name for o in overlaps) == ["azure-devops-mcp", "tools"]


def test_different_systems_do_not_overlap():
    assert (
        find_tool_source_overlaps([_server("k8s-prod")], [_connector("github")]) == []
    )


def test_empty_mcp_allowlist_or_no_connectors_never_overlap():
    assert find_tool_source_overlaps([], [_connector("jira")]) == []
    assert find_tool_source_overlaps([_server("atlassian")], []) == []


def test_inactive_server_and_disabled_connector_never_overlap():
    assert (
        find_tool_source_overlaps(
            [_server("atlassian", active=False)], [_connector("jira")]
        )
        == []
    )
    assert (
        find_tool_source_overlaps(
            [_server("atlassian")], [_connector("jira", enabled=False)]
        )
        == []
    )


def test_custom_http_connector_never_overlaps():
    proxy = _connector("custom", "Jira proxy")
    assert find_tool_source_overlaps([_server("atlassian")], [proxy]) == []


def test_same_kind_connector_pair_reports_each_connector():
    server = _server("atlassian")
    first = _connector("jira", "Jira DOT")
    second = _connector("jira", "Jira DATA")
    overlaps = find_tool_source_overlaps([server], [first, second])
    assert [o.connector_name for o in overlaps] == ["Jira DATA", "Jira DOT"]


def test_same_kind_connector_pair_without_mcp_is_not_an_overlap():
    assert find_tool_source_overlaps([], [_connector("jira"), _connector("jira")]) == []


def test_secrets_are_never_inspected():
    server = _server(
        "prod-tools",
        command="run-tools",
        env={"JIRA_API_TOKEN": "jira-secret-value"},
        token="atlassian-bearer-token",
    )
    assert find_tool_source_overlaps([server], [_connector("jira")]) == []


def test_heuristic_false_positive_is_still_only_advisory():
    # A reporting server that merely mentions jira in its name is flagged.
    # That is acceptable only because the warning never blocks anything; see
    # test_false_positive_overlap_never_blocks_session_start below.
    (overlap,) = find_tool_source_overlaps(
        [_server("jira-reports-readonly")], [_connector("jira")]
    )
    assert overlap.matched_term == "jira"


def test_detection_is_deterministic():
    servers = [_server("atlassian"), _server("mcp-atlassian-backup")]
    connectors = [_connector("jira", "b"), _connector("confluence", "a")]
    first = find_tool_source_overlaps(servers, connectors)
    second = find_tool_source_overlaps(
        list(reversed(servers)), list(reversed(connectors))
    )
    assert first == second
    assert len(first) == 4


def test_overlaps_for_service_ignores_stale_and_malformed_ids():
    server = _server("atlassian")
    jira = _connector("jira")
    service = SimpleNamespace(
        mcp_server_ids=[str(server.id), str(uuid.uuid4()), "not-a-uuid", None],
        allowed_integration_connector_ids=[str(jira.id), "garbage"],
    )
    (overlap,) = overlaps_for_service(
        service, {str(server.id): server}, {str(jira.id): jira}
    )
    assert overlap.connector_id == jira.id
    assert (
        overlaps_for_service(
            SimpleNamespace(
                mcp_server_ids=None, allowed_integration_connector_ids=None
            ),
            {str(server.id): server},
            {str(jira.id): jira},
        )
        == []
    )


# ---------------------------------------------------------------------------
# Pinned behaviour (spec tests 1 and 2)
# ---------------------------------------------------------------------------


def _native_create_descriptor(connector_id: uuid.UUID, authored=None):
    return IntegrationToolDescriptor(
        name=f"integration__jira__create_issue__{connector_id.hex}",
        description="Create an issue. Connector: Jira DOT (jira).",
        connector_id=connector_id,
        capability=IntegrationCapability(
            "create_issue", "Create an issue.", "caution", True
        ),
        authored_operation=authored,
    )


def test_both_tool_names_survive_merge_with_independent_policy():
    descriptor = _native_create_descriptor(uuid.uuid4())
    skill = merge_integration_skill(loads(MCP_SKILL), [descriptor])
    names = [operation.tool for operation in skill.operations]
    assert names.count("jira_create_issue") == 1
    assert names.count(descriptor.name) == 1

    # The MCP op keeps its authored policy; the native op keeps its baseline.
    mcp_t1 = check("jira_create_issue", 1, skill)
    native_t1 = check(descriptor.name, 1, skill)
    assert (mcp_t1.decision, mcp_t1.requires_approval) == ("autonomous", False)
    assert (native_t1.decision, native_t1.requires_approval) == ("approval", True)
    assert check("jira_create_issue", 0, skill).permitted is False
    assert check(descriptor.name, 0, skill).permitted is False


async def test_connector_deny_escape_hatch_keeps_only_the_mcp_route(integration_db):
    factory, org_id = integration_db
    async with factory() as db:
        connector = await IntegrationConnectorRepo.create(
            db,
            org_id,
            kind="jira",
            name="Jira DOT",
            base_url="https://jira.example.test",
            auth_type="pat",
            auth={"token": "x"},
            config={"project_key": "DOT"},
            is_enabled=True,
        )
        await SkillRepo.create(
            db,
            org_id,
            name="Jira connector policy",
            content_md=CONNECTOR_DENY_SKILL,
            integration_connector_id=connector.id,
            assignment="integration",
        )
        await db.commit()
        connector_id = connector.id

    runtime = await IntegrationToolRuntime.create(
        factory, org_id, allowed_connector_ids={connector_id}
    )
    native = f"integration__jira__create_issue__{connector_id.hex}"
    assert runtime.owns(native)
    skill = merge_integration_skill(loads(MCP_SKILL), runtime.descriptors)
    for tier in (0, 1, 2):
        result = check(native, tier, skill)
        assert result.permitted is False, tier
        assert result.decision == "deny", tier
    assert check("jira_create_issue", 1, skill).permitted is True
    # Only the create op is denied; the connector's reads still work.
    get_issue = f"integration__jira__get_issue__{connector_id.hex}"
    assert check(get_issue, 1, skill).permitted is True


def test_malformed_connector_skill_still_fails_closed_with_overlap():
    # A broken connector skill must deny, never widen, even when an MCP route
    # exists for the same system.
    connector_id = uuid.uuid4()
    denied = OperationClassification(
        tool=f"integration__jira__create_issue__{connector_id.hex}",
        classification="destructive",
        deny=True,
    )
    descriptor = _native_create_descriptor(connector_id, authored=denied)
    skill = merge_integration_skill(loads(MCP_SKILL), [descriptor])
    assert all(
        check(descriptor.name, tier, skill).permitted is False for tier in (0, 1, 2)
    )
    assert check("jira_create_issue", 1, skill).permitted is True


# ---------------------------------------------------------------------------
# Side-effect asymmetry (spec test 3)
# ---------------------------------------------------------------------------


@pytest.fixture
async def integration_db(monkeypatch):
    monkeypatch.setenv("OPSMENDER_SECRET_KEY", "overlap-test-secret")
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    org_id = uuid.uuid4()
    async with factory() as db:
        db.add(Organization(id=org_id, name="Overlap", slug="overlap"))
        await db.commit()
    yield factory, org_id
    await engine.dispose()


async def _count(factory, model, **filters):
    async with factory() as db:
        stmt = select(func.count()).select_from(model)
        for column, value in filters.items():
            stmt = stmt.where(getattr(model, column) == value)
        return (await db.execute(stmt)).scalar_one()


async def test_native_route_links_the_ticket_and_the_mcp_route_does_not(
    integration_db, monkeypatch
):
    factory, org_id = integration_db
    async with factory() as db:
        connector = await IntegrationConnectorRepo.create(
            db,
            org_id,
            kind="jira",
            name="Jira DOT",
            base_url="https://jira.example.test",
            auth_type="pat",
            auth={"token": "x"},
            config={"project_key": "DOT", "ticket_sync_enabled": True},
            is_enabled=True,
        )
        incident = await IncidentRepo.create(
            db, org_id, title="Checkout 500s", description="d"
        )
        await db.commit()
        connector_id, incident_id = connector.id, incident.id

    async def jira_api(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST" and request.url.path.endswith("/issue")
        return httpx.Response(201, json={"id": "10001", "key": "DOT-1"})

    adapter = JiraAdapter(
        http_client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(jira_api)
        )
    )
    monkeypatch.setattr(
        integration_tools,
        "get_adapter",
        lambda kind: adapter if kind == "jira" else None,
    )
    runtime = await IntegrationToolRuntime.create(
        factory, org_id, allowed_connector_ids={connector_id}
    )
    mcp_calls: list[str] = []

    async def fake_mcp_caller(_session, tool_name, params):
        mcp_calls.append(tool_name)
        return CallToolResult(
            isError=False,
            content=[TextContent(type="text", text=json.dumps({"key": "DOT-2"}))],
        )

    caller = build_combined_tool_caller(runtime, fake_mcp_caller)
    params = {"summary": "Checkout 500s", "incident_id": str(incident_id)}

    native = await caller(
        None, f"integration__jira__create_issue__{connector_id.hex}", params
    )
    assert native.isError is False
    assert await _count(factory, IncidentIntegrationLink, incident_id=incident_id) == 1
    assert await _count(factory, TicketSyncState, incident_id=incident_id) == 1
    assert mcp_calls == []

    via_mcp = await caller(None, "jira_create_issue", params)
    assert via_mcp.isError is False
    assert mcp_calls == ["jira_create_issue"]
    # Same operator intent, no tracking: the MCP route writes nothing.
    assert await _count(factory, IncidentIntegrationLink, incident_id=incident_id) == 1
    assert await _count(factory, TicketSyncState, incident_id=incident_id) == 1


# ---------------------------------------------------------------------------
# Services API + session start
# ---------------------------------------------------------------------------


@pytest.fixture
async def app(tmp_path):
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'overlap-api.db'}"
    engine = create_async_engine(database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(Organization(id=TEST_ORG_ID, name="Test", slug="test"))
        await session.commit()
    set_session_factory(factory)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "OPSMENDER_TIER=2\n"
        "OPSMENDER_LOG_LEVEL=INFO\n"
        "OPSMENDER_AUDIT_LOG=./logs/audit.jsonl\n"
        "OPSMENDER_JWT_SECRET=test-secret\n"
        "OPSMENDER_SECRET_KEY=overlap-api-secret\n"
        f"OPSMENDER_DATABASE_URL={database_url}\n"
        f"OPSMENDER_MCP_SERVERS_JSON={json.dumps([])}\n"
    )
    set_env_path(env_file)
    application = create_app()
    application.state.session_factory = factory
    application.state.workflow_start_delay_seconds = 3600

    class _Pool:
        async def get_server(self, *a, **kw):
            return object()

        @asynccontextmanager
        async def connect(self, *a, **kw):
            yield SimpleNamespace()

    set_mcp_pool(_Pool())

    async def _get_db():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    application.dependency_overrides[get_db] = _get_db
    yield application
    set_env_path(None)
    pending = list(getattr(application.state, "session_tasks", set())) + list(
        getattr(application.state, "background_tasks", set())
    )
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    await engine.dispose()


@pytest.fixture
async def client(app):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


async def _login(client: AsyncClient, username: str, password: str) -> dict[str, str]:
    resp = await client.post(
        "/auth/login", json={"username": username, "password": password}
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture
async def admin_headers(client):
    await client.post(
        "/auth/register",
        json={
            "username": "overlap-admin",
            "email": "overlap-admin@test.com",
            "password": "securepass123",
        },
    )
    return await _login(client, "overlap-admin", "securepass123")


async def _seed_sources(app, *, mcp_name: str = "atlassian"):
    async with app.state.session_factory() as db:
        team = await TeamRepo.create(db, TEST_ORG_ID, name="Payments", slug="payments")
        mcp = await MCPServerRepo.create(
            db,
            TEST_ORG_ID,
            name=mcp_name,
            transport="stdio",
            command="npx",
            args=["-y", "mcp-atlassian"] if mcp_name == "atlassian" else [],
        )
        other_mcp = await MCPServerRepo.create(
            db, TEST_ORG_ID, name="k8s-prod", transport="stdio", command="kubectl-mcp"
        )
        jira = await IntegrationConnectorRepo.create(
            db,
            TEST_ORG_ID,
            kind="jira",
            name="Jira DOT",
            base_url="https://jira.example.test",
            auth_type="pat",
            auth={"token": "x"},
            config={"project_key": "DOT"},
            is_enabled=True,
        )
        await db.commit()
        return team.id, mcp.id, other_mcp.id, jira.id


async def _create_service(client, headers, *, team_id, slug, mcp_ids, connector_ids):
    resp = await client.post(
        "/services",
        json={
            "team_id": str(team_id),
            "name": slug,
            "slug": slug,
            "mcp_server_ids": [str(i) for i in mcp_ids],
            "allowed_integration_connector_ids": [str(i) for i in connector_ids],
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_services_api_warns_admins_about_overlap(client, app, admin_headers):
    team_id, mcp_id, other_mcp_id, jira_id = await _seed_sources(app)
    overlapping = await _create_service(
        client,
        admin_headers,
        team_id=team_id,
        slug="checkout",
        mcp_ids=[mcp_id],
        connector_ids=[jira_id],
    )
    (warning,) = overlapping["tool_source_overlaps"]
    assert warning == {
        "kind": "jira",
        "connector_id": str(jira_id),
        "connector_name": "Jira DOT",
        "mcp_server_id": str(mcp_id),
        "mcp_server_name": "atlassian",
        "matched_term": "atlassian",
    }
    single_source = await _create_service(
        client,
        admin_headers,
        team_id=team_id,
        slug="ledger",
        mcp_ids=[other_mcp_id],
        connector_ids=[jira_id],
    )
    assert single_source["tool_source_overlaps"] == []

    listed = await client.get("/services", headers=admin_headers)
    assert listed.status_code == 200
    by_slug = {item["slug"]: item for item in listed.json()["items"]}
    assert len(by_slug["checkout"]["tool_source_overlaps"]) == 1
    assert by_slug["ledger"]["tool_source_overlaps"] == []

    # Removing the MCP server clears the warning on update.
    updated = await client.put(
        f"/services/{overlapping['id']}",
        json={"mcp_server_ids": []},
        headers=admin_headers,
    )
    assert updated.status_code == 200
    assert updated.json()["tool_source_overlaps"] == []


async def test_viewers_never_receive_overlap_details(client, app, admin_headers):
    team_id, mcp_id, _other, jira_id = await _seed_sources(app)
    await _create_service(
        client,
        admin_headers,
        team_id=team_id,
        slug="checkout",
        mcp_ids=[mcp_id],
        connector_ids=[jira_id],
    )
    async with app.state.session_factory() as db:
        await UserRepo.create(
            db,
            username="overlap-viewer",
            email="overlap-viewer@test.com",
            password_hash=bcrypt.hashpw(b"viewerpass123", bcrypt.gensalt()).decode(),
            role="viewer",
            primary_org_id=TEST_ORG_ID,
        )
        await db.commit()
    viewer_headers = await _login(client, "overlap-viewer", "viewerpass123")
    listed = await client.get("/services", headers=viewer_headers)
    assert listed.status_code == 200
    (service,) = listed.json()["items"]
    assert service["tool_source_overlaps"] == []


async def test_false_positive_overlap_never_blocks_session_start(
    client, app, admin_headers
):
    team_id, mcp_id, _other, jira_id = await _seed_sources(
        app, mcp_name="jira-reports-readonly"
    )
    service = await _create_service(
        client,
        admin_headers,
        team_id=team_id,
        slug="reports",
        mcp_ids=[mcp_id],
        connector_ids=[jira_id],
    )
    assert service["tool_source_overlaps"][0]["matched_term"] == "jira"
    async with app.state.session_factory() as db:
        incident = await IncidentRepo.create(
            db,
            TEST_ORG_ID,
            title="Report job failing",
            description="d",
            service_id=uuid.UUID(service["id"]),
        )
        await db.commit()
        incident_id = incident.id
    # Normal responder flow: a Tier 2 session starts after the incident is
    # acknowledged. The overlap warning plays no part in either step.
    acked = await client.post(
        f"/incidents/{incident_id}/ack", json={"via": "web_ui"}, headers=admin_headers
    )
    assert acked.status_code == 200, acked.text
    resp = await client.post(
        "/sessions",
        json={"tier": 2, "incident_id": str(incident_id)},
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text


# ---------------------------------------------------------------------------
# opsmender doctor
# ---------------------------------------------------------------------------


async def test_doctor_without_database_warns_and_does_not_fail():
    (result,) = await check_tool_source_overlaps(None)
    assert result.status == "warn"
    assert exit_code([result]) == 0


async def test_doctor_reports_only_overlapping_active_services(integration_db):
    factory, org_id = integration_db
    async with factory() as db:
        team = await TeamRepo.create(db, org_id, name="T", slug="t")
        atlassian = await MCPServerRepo.create(
            db, org_id, name="atlassian", transport="stdio", command="npx"
        )
        k8s = await MCPServerRepo.create(db, org_id, name="k8s-prod", transport="stdio")
        jira = await IntegrationConnectorRepo.create(
            db,
            org_id,
            kind="jira",
            name="Jira DOT",
            base_url="https://jira.example.test",
            auth_type="pat",
            auth={"token": "jira-secret-token-123"},
            config={},
            is_enabled=True,
        )
        for slug, mcp_ids, active in (
            ("overlapping", [atlassian.id], True),
            ("single-source", [k8s.id], True),
            ("retired", [atlassian.id], False),
        ):
            await ServiceRepo.create(
                db,
                org_id,
                team_id=team.id,
                name=slug,
                slug=slug,
                mcp_server_ids=[str(i) for i in mcp_ids],
                allowed_integration_connector_ids=[str(jira.id)],
                is_active=active,
            )
        await db.commit()

    results = await check_tool_source_overlaps(factory)
    assert [r.name for r in results] == ["Tool-source overlap: overlapping"]
    (warning,) = results
    assert warning.status == "warn"
    assert "Jira DOT" in warning.detail and "atlassian" in warning.detail
    assert "jira-secret-token-123" not in warning.detail  # secrets never echoed
    assert exit_code(results) == 0


async def test_doctor_is_ok_when_nothing_overlaps(integration_db):
    factory, _org_id = integration_db
    (result,) = await check_tool_source_overlaps(factory)
    assert (result.status, result.name) == ("ok", "Tool-source overlap")


# ---------------------------------------------------------------------------
# Session runner wiring: the real runner, for a Service with an MCP server and
# a native connector, hands the graph one combined caller. At Tier 0 the MCP
# half of that caller is still the sandbox.
# ---------------------------------------------------------------------------


class _FakeMCPSession:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def list_tools(self):
        return SimpleNamespace(
            tools=[
                Tool(
                    name="jira_search",
                    description="Search Jira",
                    inputSchema={"type": "object"},
                ),
                Tool(
                    name="jira_create_issue",
                    description="Create a Jira issue",
                    inputSchema={"type": "object"},
                ),
            ]
        )

    async def call_tool(self, name, arguments=None):
        self.calls.append(name)
        return CallToolResult(
            isError=False, content=[TextContent(type="text", text="ok")]
        )


class _FakePool:
    def __init__(self, session: _FakeMCPSession, server_name: str) -> None:
        self._session = session
        self._server_name = server_name

    async def list_servers(self, active_only: bool = True):
        return [SimpleNamespace(name=self._server_name, transport="stdio")]

    async def get_server(self, *args, **kwargs):
        return object()

    @asynccontextmanager
    async def connect(self, name):
        assert name == self._server_name
        yield self._session


async def _run_overlap_session(app, monkeypatch, *, tier: int):
    suffix = f"t{tier}"
    async with app.state.session_factory() as db:
        team = await TeamRepo.create(
            db, TEST_ORG_ID, name=f"Team {suffix}", slug=f"team-{suffix}"
        )
        mcp = await MCPServerRepo.create(
            db,
            TEST_ORG_ID,
            name=f"atlassian-{suffix}",
            transport="stdio",
            command="npx",
        )
        await SkillRepo.create(
            db,
            TEST_ORG_ID,
            name=f"MCP policy {suffix}",
            content_md=MCP_SKILL,
            mcp_server_id=mcp.id,
        )
        jira = await IntegrationConnectorRepo.create(
            db,
            TEST_ORG_ID,
            kind="jira",
            name=f"Jira {suffix}",
            base_url="https://jira.example.test",
            auth_type="pat",
            auth={"token": "x"},
            config={"project_key": "DOT"},
            is_enabled=True,
        )
        service = await ServiceRepo.create(
            db,
            TEST_ORG_ID,
            team_id=team.id,
            name=f"svc-{suffix}",
            slug=f"svc-{suffix}",
            mcp_server_ids=[str(mcp.id)],
            allowed_integration_connector_ids=[str(jira.id)],
        )
        incident = await IncidentRepo.create(
            db,
            TEST_ORG_ID,
            title="Checkout 500s",
            description="d",
            service_id=service.id,
        )
        session = await SessionRepo.create(
            db, TEST_ORG_ID, tier=tier, incident_id=incident.id
        )
        await db.commit()
        session_id, connector_id, server_name = session.id, jira.id, mcp.name

    async def jira_api(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"id": "10001", "key": "DOT-1"})

    adapter = JiraAdapter(
        http_client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(jira_api)
        )
    )
    monkeypatch.setattr(
        integration_tools,
        "get_adapter",
        lambda kind: adapter if kind == "jira" else None,
    )

    async def _no_llm(factory, session):
        return SimpleNamespace()

    captured: dict = {}

    class _FakeGraph:
        async def ainvoke(self, state):
            return {"status": "completed", "summary": "ok"}

    def _capture_graph(**kwargs):
        captured.update(kwargs)
        return _FakeGraph()

    monkeypatch.setattr(session_runner, "_resolve_llm", _no_llm)
    monkeypatch.setattr(session_runner, "build_graph", _capture_graph)
    fake_session = _FakeMCPSession()
    app.state.mcp_pool = _FakePool(fake_session, server_name)
    app.state.workflow_start_delay_seconds = 0

    await session_runner._run_session_workflow_inner(app, session_id=session_id)

    async with app.state.session_factory() as db:
        stored = await SessionRepo.get_by_id(db, TEST_ORG_ID, session_id)
    assert stored.status == "completed", stored.status
    return captured, fake_session, connector_id


async def test_session_runner_wires_one_caller_for_both_sources(app, monkeypatch):
    captured, fake_session, connector_id = await _run_overlap_session(
        app, monkeypatch, tier=1
    )
    native_create = f"integration__jira__create_issue__{connector_id.hex}"
    names = captured["plan_tool_names"]
    assert "jira_create_issue" in names and "jira_search" in names
    assert native_create in names
    assert names == sorted(names)  # one flat, sorted list: no precedence

    caller = captured["tool_caller"]
    native = await caller(fake_session, native_create, {"summary": "Checkout 500s"})
    assert native.isError is False
    assert fake_session.calls == []  # the native call never touched MCP
    await caller(fake_session, "jira_create_issue", {"summary": "Checkout 500s"})
    assert fake_session.calls == ["jira_create_issue"]


async def test_tier0_session_still_routes_mcp_calls_through_the_sandbox(
    app, monkeypatch
):
    captured, fake_session, connector_id = await _run_overlap_session(
        app, monkeypatch, tier=0
    )
    names = captured["plan_tool_names"]
    # The sandbox exposes only Tier 0-permitted MCP tools, and the native
    # mutating tool is filtered out at Tier 0.
    assert "jira_search" in names
    assert "jira_create_issue" not in names
    assert f"integration__jira__create_issue__{connector_id.hex}" not in names

    caller = captured["tool_caller"]
    with pytest.raises(Tier0SandboxViolation):
        await caller(fake_session, "jira_create_issue", {})
    assert fake_session.calls == []
    await caller(fake_session, "jira_search", {"query": "DOT"})
    assert fake_session.calls == ["jira_search"]
