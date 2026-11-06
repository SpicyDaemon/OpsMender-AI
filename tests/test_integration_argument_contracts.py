"""M1-138 (I-02): a native integration tool tells the planner its arguments
and refuses a call that does not fit them.

The 2026-11-04 live round ran real sessions against Jira, Confluence,
Jenkins, Kubernetes and Argo CD. The planner was shown each tool's
description and connector name; most said nothing about arguments and five
kinds listed names without types, so the model guessed (``issue_id_or_key``
for Jira's ``issue_key``, ``job_name`` for Jenkins' ``job``, ``name`` for a
Kubernetes ``pod``) and every guessed call died in the adapter as an
unexpected keyword. MCP tools already list their arguments in the plan
prompt; native tools now do too, read from the adapter method's own
signature and the connector's configuration, and the same contract is
checked before the adapter runs.
"""

from __future__ import annotations

import inspect
import json
import types
import uuid

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.integrations  # noqa: F401  (registers every adapter)
from backend.db.models import Base, IntegrationConnector, Organization
from backend.db.repos import IntegrationConnectorRepo
from backend.integrations.atlassian import JiraAdapter
from backend.integrations.automation import ArgoCDAdapter
from backend.integrations.base import describe_arguments
from backend.integrations.registry import _ADAPTERS, get_adapter, list_kinds
from backend.integrations.support import FreshserviceAdapter
from backend.integrations.tools import IntegrationToolRuntime
from backend.services.ticket_sync import _create_params

_SCALARS = {"string", "integer", "number", "boolean", "object", "array"}
AUTH = {
    "token": "t",
    "api_token": "t",
    "access_token": "t",
    "api_key": "t",
    "username": "u",
    "password": "p",
    "email": "ops@example.test",
}


def _actions():
    for kind in list_kinds():
        adapter = get_adapter(kind.kind)
        if adapter is None:
            continue
        for capability in adapter.capabilities:
            if capability.action != "test_connection":
                yield kind, adapter, capability.action


def _usable(label: str) -> bool:
    return all(
        part in _SCALARS
        or (part.startswith("array of ") and part.removeprefix("array of ") in _SCALARS)
        for part in label.split("/")
    )


def _bare(kind, **config) -> IntegrationConnector:
    auth_type = next(
        (c for c in ("pat", "api_key", "basic", "oauth") if c in kind.auth_types),
        kind.auth_types[0],
    )
    return IntegrationConnector(
        kind=kind.kind,
        name=kind.kind,
        base_url="https://vendor.example.test",
        auth_type=auth_type,
        config={"verify_tls": False, **config},
    )


def _benign(argument):
    return {
        "string": "benign",
        "integer": 7,
        "number": 7,
        "boolean": False,
        "object": {"k": "v"},
        "array": ["a"],
    }[argument.type.split("/")[0].split(" ")[0]]


def _mocked(adapter):
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={})

    return (
        type(adapter)(
            http_client_factory=lambda: httpx.AsyncClient(
                transport=httpx.MockTransport(handler)
            )
        ),
        requests,
    )


@pytest.fixture
async def integration_factory(monkeypatch):
    monkeypatch.setenv("OPSMENDER_SECRET_KEY", "integration-test-secret")
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    org_id = uuid.uuid4()
    async with factory() as db:
        db.add(Organization(id=org_id, name="Contracts", slug="contracts"))
        await db.commit()
    yield factory, org_id
    await engine.dispose()


def test_every_action_of_every_adapter_has_a_typed_contract():
    """The contract is the method signature, so every argument of every
    action is named, typed and free of catch-all arguments."""
    actions = list(_actions())
    # The whole catalog, not a sample: a dropped adapter changes these.
    assert len(actions) == 119
    assert len({kind.kind for kind, _, _ in actions}) == 22
    total = 0
    for kind, adapter, action in actions:
        method = getattr(adapter, action, None)
        assert callable(method), f"{kind.kind}.{action} has no method"
        parameters = list(inspect.signature(method).parameters.values())[2:]
        assert not any(
            p.kind in (p.VAR_KEYWORD, p.VAR_POSITIONAL) for p in parameters
        ), f"{kind.kind}.{action} takes catch-all arguments"
        for parameter in parameters:
            assert parameter.annotation is not parameter.empty, (
                f"{kind.kind}.{action}.{parameter.name} has no annotation"
            )
        for argument in adapter.arguments_for(action):
            assert _usable(argument.type), (
                f"{kind.kind}.{action}.{argument.name}: {argument.type}"
            )
            total += 1
    assert total == 353


def test_the_contract_names_the_keywords_the_live_round_guessed_wrong():
    cases = {
        ("jira", "get_issue"): "Arguments: issue_key (string, required).",
        ("jira", "list_transitions"): "Arguments: issue_key (string, required).",
        ("confluence", "read_doc"): "Arguments: page_id (string, required).",
        ("jenkins", "get_job"): "Arguments: job (string).",
        ("jenkins", "trigger_build"): "Arguments: job (string), parameters (object).",
        ("kubernetes", "get_pod"): (
            "Arguments: pod (string, required), namespace (string)."
        ),
        ("argocd", "get_application"): "Arguments: application (string).",
        ("argocd", "list_applications"): "Arguments: none.",
        ("argocd", "rollback"): (
            "Arguments: history_id (integer, required), application (string)."
        ),
        # An array says what it holds: Gitea labels are ids, GitHub's names.
        ("gitea", "create_issue"): (
            "Arguments: title (string, required), owner (string), repo (string), "
            "body (string), labels (array of integer)."
        ),
    }
    for (kind, action), expected in cases.items():
        adapter = get_adapter(kind)
        assert describe_arguments(adapter.arguments_for(action)) == expected


def test_a_connector_shows_its_defaults_and_requires_what_it_has_none_for():
    """ "Leave it out" is only true when there is something to fall back to:
    a Jenkins connector with no default job needs the job from the model."""
    kinds = {kind.kind: kind for kind in list_kinds()}

    def contract(kind, action, **config):
        adapter = get_adapter(kind)
        return describe_arguments(
            adapter.arguments_for(action, _bare(kinds[kind], **config))
        )

    assert contract("jenkins", "get_job") == "Arguments: job (string, required)."
    assert contract("jenkins", "get_job", job="intg1-build") == (
        'Arguments: job (string, default "intg1-build").'
    )
    assert contract("argocd", "get_application", application="intg1-bookinfo") == (
        'Arguments: application (string, default "intg1-bookinfo").'
    )
    assert contract("github", "list_issues", owner="acme") == (
        'Arguments: owner (string, default "acme"), repo (string, required), '
        "state (string)."
    )
    # The adapter's own fallback is a default too.
    assert contract("kubernetes", "get_pod") == (
        'Arguments: pod (string, required), namespace (string, default "default").'
    )
    assert contract("kubernetes", "get_pod", namespace="intg1-bookinfo") == (
        "Arguments: pod (string, required), namespace (string, default "
        '"intg1-bookinfo").'
    )
    assert 'issue_type (string, default "Task")' in contract("jira", "create_issue")
    assert "project_key (string, required)" in contract("jira", "create_issue")
    # Bitbucket Cloud addresses a repository by workspace, Data Center by
    # project; the other one is never asked for.
    cloud = contract("bitbucket", "get_repository", edition="cloud")
    assert "workspace (string, required)" in cloud and "project (string)" in cloud
    server = contract("bitbucket", "get_repository", edition="data_center")
    assert "project (string, required)" in server and "workspace (string)" in server
    # Linear lists across teams but creates in one.
    assert "team_id (string)" in contract("linear", "list_issues")
    assert "team_id (string, required)" in contract("linear", "create_issue")


@pytest.mark.parametrize("case", list(_actions()), ids=lambda c: f"{c[0].kind}.{c[2]}")
async def test_what_the_contract_requires_is_what_the_adapter_demands(case):
    """With nothing configured: a call carrying exactly the arguments the
    contract requires is never refused for a missing one, and each argument
    the contract requires because the connector has no default is one the
    adapter itself would have demanded."""
    kind, registered, action = case
    connector = _bare(kind)
    adapter, _ = _mocked(registered)
    arguments = adapter.arguments_for(action, connector)
    required = {a.name: _benign(a) for a in arguments if a.required}

    result = await adapter.safe_invoke(action, connector, AUTH, required)
    error = result.error or ""
    assert "needs the argument" not in error, error
    if error.endswith(" is required"):
        # Only the declared either-or arguments may still be asked for.
        name = error.removesuffix(" is required")
        assert name in adapter.argument_alternatives(connector, action) or (
            name not in {a.name for a in arguments}
        ), error

    signature = {a.name: a for a in adapter.arguments_for(action)}
    method = getattr(adapter, action)
    for argument in arguments:
        if not argument.required or signature[argument.name].required:
            continue
        without = {k: v for k, v in required.items() if k != argument.name}
        try:
            await method(connector, AUTH, **without)
        except Exception as exc:  # noqa: BLE001
            assert str(exc) == f"{argument.name} is required", (argument.name, exc)
        else:
            raise AssertionError(
                f"{kind.kind}.{action}: the contract requires "
                f"'{argument.name}' but the adapter runs without it"
            )


@pytest.mark.parametrize(
    ("kind", "action", "parameters", "fragment"),
    [
        # The guesses recorded in the live round's evidence.
        (
            "jira",
            "get_issue",
            {"issue_id_or_key": "INTG1-1"},
            "does not take the argument 'issue_id_or_key'",
        ),
        (
            "confluence",
            "read_doc",
            {"doc_id_or_path": "123"},
            "does not take the argument 'doc_id_or_path'",
        ),
        (
            "jenkins",
            "get_job",
            {"job_name": "intg1-build"},
            "does not take the argument 'job_name'",
        ),
        (
            "kubernetes",
            "get_pod",
            {"name": "ratings-v1"},
            "does not take the argument 'name'",
        ),
        (
            "argocd",
            "get_application",
            {"applicationName": "intg1-bookinfo"},
            "does not take the argument 'applicationName'",
        ),
        (
            "kubernetes",
            "get_pod",
            {"name": "a", "ns": "b"},
            "does not take the arguments 'name', 'ns'",
        ),
        ("jira", "get_issue", {}, "needs the argument 'issue_key'"),
        ("jira", "get_issue", {"issue_key": None}, "needs the argument 'issue_key'"),
        # The text "false" is truthy: only a real boolean may set a flag that
        # prunes resources, deletes a branch or notifies subscribers.
        (
            "argocd",
            "sync",
            {"prune": "false"},
            "the argument 'prune' must be boolean, not string",
        ),
        (
            "github",
            "create_issue",
            {"title": "x", "labels": "bug"},
            "the argument 'labels' must be array of string, not string",
        ),
        (
            "gitea",
            "create_issue",
            {"title": "x", "labels": ["bug"]},
            "the argument 'labels' must be array of integer, not array",
        ),
        (
            "zendesk",
            "update_ticket",
            {"ticket_id": 7, "fields": "status=open"},
            "the argument 'fields' must be object, not string",
        ),
        (
            "kubernetes",
            "delete_pod",
            {"pod": "ratings-v1", "grace_period_seconds": "--5"},
            "the argument 'grace_period_seconds' must be integer, not string",
        ),
        (
            "kubernetes",
            "list_pods",
            {"limit": 2.5},
            "the argument 'limit' must be integer, not number",
        ),
    ],
)
def test_a_call_that_does_not_fit_the_contract_is_named(
    kind, action, parameters, fragment
):
    adapter = get_adapter(kind)
    problem = adapter.check_arguments(action, parameters)
    assert problem is not None and fragment in problem
    # The refusal carries the real contract, so the next plan can use it.
    assert describe_arguments(adapter.arguments_for(action)) in problem


def test_a_refusal_does_not_carry_a_caller_s_text_back_at_length():
    names = {f"{'x' * 500}{i}": 1 for i in range(40)}
    problem = get_adapter("jira").check_arguments("get_issue", names)
    assert "and 35 more" in problem
    assert len(problem) < 700


@pytest.mark.parametrize(
    ("kind", "action", "parameters"),
    [
        ("jira", "get_issue", {"issue_key": "INTG1-1"}),
        ("jenkins", "trigger_build", {"job": "a", "parameters": {"TARGET": "x"}}),
        ("jenkins", "get_build", {"build_number": "lastBuild"}),
        ("jenkins", "get_build", {"build_number": 12}),
        # A number may stand for an identifier the adapter turns into text,
        # and a whole number written as text or as 5.0 for an integer.
        ("confluence", "read_doc", {"page_id": 98765}),
        ("github", "comment_issue", {"issue_number": "12", "body": "seen"}),
        ("kubernetes", "list_pods", {"limit": 100.0}),
        ("kubernetes", "delete_pod", {"pod": "a", "grace_period_seconds": "+5"}),
        ("argocd", "sync", {"application": "a", "prune": True, "dry_run": False}),
        ("kubernetes", "get_pod", {"pod": "ratings-v1", "namespace": None}),
        ("gitlab", "create_issue", {"title": "x", "labels": "a,b"}),
        ("gitlab", "create_issue", {"title": "x", "labels": ["a", "b"]}),
        ("gitea", "create_issue", {"title": "x", "labels": [3, 4]}),
        # The controller takes extra variables as an object or as text.
        ("ansible", "launch", {"template_id": 7, "extra_vars": "target: web"}),
    ],
)
def test_a_call_that_fits_the_contract_passes(kind, action, parameters):
    assert get_adapter(kind).check_arguments(action, parameters) is None


async def test_a_null_is_an_argument_left_out_not_a_value():
    """A null for an optional argument must not replace its default: a
    Freshservice note stays private."""
    adapter, requests = _mocked(FreshserviceAdapter())
    connector = IntegrationConnector(
        kind="freshservice",
        name="fs",
        base_url="https://acme.freshservice.com",
        auth_type="api_key",
        config={},
    )
    await adapter.safe_invoke(
        "comment_ticket",
        connector,
        AUTH,
        {"ticket_id": 7, "body": "internal note", "private": None},
    )
    assert json.loads(requests[-1].content) == {
        "body": "internal note",
        "private": True,
    }


async def test_a_whole_number_sent_as_a_float_reaches_the_adapter_as_an_integer():
    kinds = {kind.kind: kind for kind in list_kinds()}
    adapter, requests = _mocked(get_adapter("kubernetes"))
    await adapter.safe_invoke(
        "list_pods", _bare(kinds["kubernetes"]), AUTH, {"limit": 25.0}
    )
    assert requests[-1].url.params["limit"] == "25"


def test_the_ticket_sync_calls_still_fit():
    """The sync service calls adapters with what it builds itself."""
    incident = types.SimpleNamespace(
        id=uuid.uuid4(), title="Payments 500s", description="503s rising"
    )
    kinds = {kind.kind: kind for kind in list_kinds()}
    for kind, config in (("jira", {"project_key": "OPS"}), ("servicenow", {})):
        action, parameters = _create_params(kind, incident)
        connector = _bare(kinds[kind], **config)
        assert get_adapter(kind).check_arguments(action, parameters, connector) is None
        assert (
            get_adapter(kind).check_arguments(
                "sync_status_out",
                {"ticket_id": "INTG1-1", "new_status": "Done"},
                connector,
            )
            is None
        )


async def _connector(factory, org_id, kind, **config):
    async with factory() as db:
        row = await IntegrationConnectorRepo.create(
            db,
            org_id,
            kind=kind,
            name=f"{kind} contract",
            base_url="https://example.test",
            auth_type="pat",
            auth={"token": "t", "api_token": "t", "email": "a@example.test"},
            config=config,
            is_enabled=True,
        )
        await db.commit()
        return row.id


async def test_the_planner_is_shown_each_tools_arguments(integration_factory):
    factory, org_id = integration_factory
    await _connector(factory, org_id, "jira", edition="cloud", project_key="OPS")
    runtime = await IntegrationToolRuntime.create(factory, org_id)

    by_action = {d.capability.action: d for d in runtime.descriptors}
    # The plan prompt and Skill Studio's list of the connector's tools both
    # read this description.
    described = runtime.descriptions
    get_issue = by_action["get_issue"]
    assert described[get_issue.name] == (
        "Read an issue. Connector: jira contract (jira). "
        "Arguments: issue_key (string, required)."
    )
    assert [a.name for a in get_issue.arguments] == ["issue_key"]
    assert described[by_action["create_issue"].name] == (
        "Create an issue. Connector: jira contract (jira). "
        'Arguments: summary (string, required), project_key (string, default "OPS"), '
        'issue_type (string, default "Task"), description (string), '
        "incident_id (string)."
    )
    assert described[by_action["test_connection"].name].endswith("Arguments: none.")


async def test_a_description_carries_one_argument_list(integration_factory):
    """Five kinds listed their arguments by hand, without types and in
    words the generated list now contradicts ("owner, repo" read as
    required where the connector supplies them)."""
    factory, org_id = integration_factory
    await _connector(factory, org_id, "github", owner="acme", repo="lab")
    runtime = await IntegrationToolRuntime.create(factory, org_id)
    for descriptor in runtime.descriptors:
        assert "Parameters:" not in descriptor.description
        assert descriptor.description.count("Arguments:") == 1


async def test_a_guessed_argument_never_reaches_the_provider(
    integration_factory, monkeypatch
):
    factory, org_id = integration_factory
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"key": "INTG1-1", "fields": {}})

    monkeypatch.setitem(
        _ADAPTERS,
        "jira",
        JiraAdapter(
            http_client_factory=lambda: httpx.AsyncClient(
                transport=httpx.MockTransport(handler)
            )
        ),
    )
    await _connector(factory, org_id, "jira", edition="cloud")
    runtime = await IntegrationToolRuntime.create(factory, org_id)
    tool = next(
        d.name for d in runtime.descriptors if d.capability.action == "get_issue"
    )

    # The live round's exact failing call.
    guessed = await runtime.call_tool(runtime, tool, {"issue_id_or_key": "INTG1-1"})
    assert guessed.isError is True
    payload = json.loads(guessed.content[0].text)
    assert payload["ok"] is False
    assert payload["error"] == (
        "jira.get_issue does not take the argument 'issue_id_or_key'. "
        "Arguments: issue_key (string, required)."
    )
    assert requests == []

    # The call the contract describes reads the issue.
    read = await runtime.call_tool(runtime, tool, {"issue_key": "INTG1-1"})
    assert read.isError is False
    assert [r.url.path for r in requests] == ["/rest/api/3/issue/INTG1-1"]


async def test_a_connector_without_a_default_asks_for_it_by_name(
    integration_factory, monkeypatch
):
    """A Jenkins connector with no default job: the contract says the job
    is required, and a call without it is refused with that contract
    rather than failing inside the adapter."""
    factory, org_id = integration_factory
    await _connector(factory, org_id, "jenkins")
    runtime = await IntegrationToolRuntime.create(factory, org_id)
    descriptor = next(
        d for d in runtime.descriptors if d.capability.action == "get_job"
    )
    assert descriptor.description.endswith("Arguments: job (string, required).")
    refused = await runtime.call_tool(runtime, descriptor.name, {})
    assert json.loads(refused.content[0].text)["error"] == (
        "jenkins.get_job needs the argument 'job'. Arguments: job (string, required)."
    )


async def test_a_mutation_with_a_text_flag_is_refused_before_it_runs(
    integration_factory, monkeypatch
):
    factory, org_id = integration_factory
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={})

    monkeypatch.setitem(
        _ADAPTERS,
        "argocd",
        ArgoCDAdapter(
            http_client_factory=lambda: httpx.AsyncClient(
                transport=httpx.MockTransport(handler)
            )
        ),
    )
    await _connector(factory, org_id, "argocd", application="intg1-bookinfo")
    runtime = await IntegrationToolRuntime.create(factory, org_id)
    tool = next(d.name for d in runtime.descriptors if d.capability.action == "sync")

    # bool("false") is True: the old path would have pruned.
    refused = await runtime.call_tool(runtime, tool, {"prune": "false"})
    assert refused.isError is True
    assert (
        "the argument 'prune' must be boolean, not string"
        in (json.loads(refused.content[0].text)["error"])
    )
    assert requests == []

    synced = await runtime.call_tool(runtime, tool, {"prune": False})
    assert synced.isError is False
    assert [r.method for r in requests] == ["POST"]
    assert json.loads(requests[0].content)["prune"] is False
