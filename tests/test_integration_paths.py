"""Verify connector paths at the HTTP transport boundary using synthetic inputs."""

from __future__ import annotations

import httpx
import pytest

from backend.db.models import IntegrationConnector
from backend.integrations.registry import get_adapter, list_kinds

KINDS = {kind.kind: kind for kind in list_kinds() if get_adapter(kind.kind)}
AUTH = {
    "token": "fixture-token",
    "api_token": "fixture-token",
    "access_token": "fixture-token",
    "api_key": "fixture-token",
    "username": "operator",
    "password": "fixture-password",
    "email": "operator@example.test",
}
IDENTIFIERS = {
    "issue_key": ("OPS-12", "OPS-13"),
    "page_id": ("123", "124"),
    "parent_id": ("321", "322"),
    "ticket_id": ("321", "322"),
    "task_id": ("321", "322"),
}
TRAVERSAL = (
    ".",
    "..",
    "../x",
    "a/../b",
    r"a\..\b",
    "%2e%2e%2fx",
    "..;x=y",
    "%2e%2e%3bx",
)


def _connector(kind, config=None):
    return IntegrationConnector(
        kind=kind,
        name=kind,
        base_url="https://vendor.example.test",
        auth_type=KINDS[kind].auth_types[0],
        config=config or {},
    )


async def _invoke(kind, action, arguments, *, config=None, status=418):
    requests = []

    def capture(request):
        requests.append(request)
        return httpx.Response(status, json={})

    adapter = type(get_adapter(kind))(
        http_client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(capture)
        ),
    )
    result = await adapter.safe_invoke(
        action, _connector(kind, config), AUTH, arguments
    )
    return result, requests


def _paths(requests):
    return [request.url.raw_path.partition(b"?")[0] for request in requests]


def _sample(argument, alternate=False):
    scalar = argument.type.split("/")[0]
    if scalar == "string":
        return IDENTIFIERS.get(
            argument.name, (f"{argument.name.replace('_', '-')}-first", "second")
        )[alternate]
    if scalar.startswith("array of "):
        return []
    return {"integer": 7, "number": 7, "boolean": False, "object": {"name": "value"}}[
        scalar
    ]


ACTIONS = [
    (kind, capability.action)
    for kind in KINDS
    for capability in get_adapter(kind).capabilities
    if capability.action != "test_connection"
]


@pytest.mark.parametrize(("kind", "action"), ACTIONS)
async def test_action_path_arguments_are_confined(kind, action):
    adapter = get_adapter(kind)
    contract = adapter.arguments_for(action, _connector(kind))
    baseline = {arg.name: _sample(arg) for arg in contract if arg.required}
    # This action permits an empty JSON object at the contract boundary,
    # but requires a field before it can construct a provider request.
    if action == "update_work_item":
        baseline["fields"] = {"System.Title": "Fixture title"}
    if kind == "notion" and action == "create_doc":
        baseline["parent_page_id"] = "123"
    if kind == "notion" and action == "append_doc":
        baseline["markdown"] = "Fixture text"
    result, original = await _invoke(kind, action, baseline)
    assert original, result.error
    for arg in contract:
        types = arg.type.split("/")
        if "string" not in types:
            if "integer" in types:
                for invalid in ("..", "a/b"):
                    result, requests = await _invoke(
                        kind, action, {**baseline, arg.name: invalid}
                    )
                    assert not requests and not result.ok
                    assert arg.name in (result.error or "")
            continue
        values = {**baseline, arg.name: _sample(arg)}
        result, ordinary = await _invoke(kind, action, values)
        assert ordinary, (arg.name, result.error)
        result, changed = await _invoke(
            kind, action, {**values, arg.name: _sample(arg, alternate=True)}
        )
        assert changed, (arg.name, result.error)
        if _paths(ordinary) == _paths(changed):
            # This argument changes the query/body, or belongs to an edition
            # other than the one used by this fixture.
            continue
        for invalid in TRAVERSAL:
            result, requests = await _invoke(
                kind, action, {**values, arg.name: invalid}
            )
            assert not requests, (arg.name, invalid, _paths(requests))
            assert not result.ok and arg.name in (result.error or "")

        result, requests = await _invoke(kind, action, {**values, arg.name: "a/b"})
        slash_expected = arg.name in {"path", "ref"} or (kind, arg.name) in {
            ("gitlab", "project"),
            ("circleci", "project_slug"),
            ("jenkins", "job"),
        }
        if slash_expected:
            original_path = _paths(ordinary)[0]
            original_value = str(values[arg.name]).encode()
            assert original_value in original_path
            # Files and Bitbucket Cloud refs are intentionally multi-part.
            # Jenkins puts an explicit /job/ between nested job names.
            replacement = b"a%2Fb"
            if arg.name in {"path", "ref"} and kind != "gitlab":
                replacement = b"a/b"
            elif kind == "jenkins" and arg.name == "job":
                replacement = b"a/job/b"
            assert _paths(requests) == [
                original_path.replace(original_value, replacement)
            ]
        else:
            assert not requests
            assert not result.ok and arg.name in (result.error or "")


@pytest.mark.parametrize(
    ("kind", "action", "arguments", "config", "name"),
    [
        ("kubernetes", "delete_pod", {"pod": "."}, {"namespace": "prod"}, "pod"),
        ("kubernetes", "delete_pod", {"pod": ".."}, {"namespace": "prod"}, "pod"),
        ("kubernetes", "delete_pod", {"pod": "pod/x"}, {"namespace": "prod"}, "pod"),
        ("kubernetes", "delete_pod", {"pod": "pod..other"}, {}, "pod"),
        ("kubernetes", "list_pods", {}, {"namespace": ".."}, "namespace"),
        ("kubernetes", "list_pods", {"namespace": "two.labels"}, {}, "namespace"),
        ("jira", "get_issue", {"issue_key": "../users/search"}, {}, "issue_key"),
        ("jira", "get_issue", {"issue_key": "createmeta"}, {}, "issue_key"),
        ("jira", "test_connection", {}, {"api_version": "../2"}, "api_version"),
        ("confluence", "read_doc", {"page_id": "search"}, {}, "page_id"),
        (
            "confluence",
            "write_doc",
            {"title": "Fixture", "content": "text", "page_id": ""},
            {"edition": "data_center", "space_id": "OPS"},
            "page_id",
        ),
        (
            "confluence",
            "write_doc",
            {"title": "Fixture", "content": "text", "parent_id": "draft"},
            {"space_id": "42"},
            "parent_id",
        ),
        (
            "bitbucket",
            "get_repository",
            {"repo": "lab", "project": ".."},
            {"edition": "data_center"},
            "project",
        ),
        ("gitlab", "test_connection", {}, {"project": "../user"}, "project"),
        (
            "terraform_cloud",
            "test_connection",
            {},
            {"organization": ".."},
            "organization",
        ),
        ("servicenow", "get_record", {"sys_id": "123"}, {"table": ".."}, "table"),
        ("statuspage", "list_incidents", {}, {"page_id": "%252e%252e"}, "page_id"),
        ("argocd", "get_application", {}, {"application": "../session"}, "application"),
        ("jenkins", "trigger_build", {}, {"job": "folder/../admin"}, "job"),
        ("jenkins", "get_job", {"job": "bad\nname"}, {}, "job"),
        ("zendesk", "get_ticket", {"ticket_id": "recent"}, {}, "ticket_id"),
        ("freshservice", "get_ticket", {"ticket_id": "filter"}, {}, "ticket_id"),
        ("asana", "get_task", {"task_id": "search"}, {}, "task_id"),
    ],
)
async def test_unsafe_identifiers_are_refused_without_http(
    kind, action, arguments, config, name
):
    result, requests = await _invoke(kind, action, arguments, config=config)
    assert not requests
    assert not result.ok and name in (result.error or "")


@pytest.mark.parametrize(
    ("kind", "action", "arguments", "config", "method", "path"),
    [
        (
            "kubernetes",
            "delete_pod",
            {"pod": "worker.prod", "namespace": "prod"},
            {},
            "DELETE",
            b"/api/v1/namespaces/prod/pods/worker.prod",
        ),
        (
            "jira",
            "get_issue",
            {"issue_key": "OPS_2-17"},
            {"api_version": "latest"},
            "GET",
            b"/rest/api/latest/issue/OPS_2-17",
        ),
        (
            "jira",
            "get_issue",
            {"issue_key": "9223372036854775807"},
            {},
            "GET",
            b"/rest/api/3/issue/9223372036854775807",
        ),
        (
            "gitlab",
            "get_file",
            {"project": "group/project", "path": "./src/a b.py "},
            {},
            "GET",
            b"/api/v4/projects/group%2Fproject/repository/files/src%2Fa%20b.py%20",
        ),
        (
            "jenkins",
            "get_job",
            {"job": "./folder/./job name "},
            {},
            "GET",
            b"/job/folder/job/job%20name%20/api/json",
        ),
        (
            "bitbucket",
            "get_file",
            {
                "workspace": "acme",
                "repo": "lab",
                "path": "./src/a b.py",
                "ref": "feature/test",
            },
            {},
            "GET",
            b"/repositories/acme/lab/src/feature/test/src/a%20b.py",
        ),
        (
            "confluence",
            "write_doc",
            {"title": "Fixture", "content": "text", "page_id": "123"},
            {"space_id": "42"},
            "PUT",
            b"/wiki/api/v2/pages/123",
        ),
        (
            "confluence",
            "write_doc",
            {"title": "Fixture", "content": "text", "page_id": "123"},
            {"edition": "data_center", "space_id": "OPS"},
            "PUT",
            b"/rest/api/content/123",
        ),
    ],
)
async def test_legitimate_paths_keep_the_expected_provider_route(
    kind, action, arguments, config, method, path
):
    _, requests = await _invoke(kind, action, arguments, config=config)
    assert _paths(requests) == [path]
    assert requests[0].method == method


def test_sweep_covers_every_registered_kind():
    # Custom HTTP probes the exact endpoint configured by the operator and
    # exposes no action arguments. Its probe is exercised separately below.
    assert len(KINDS) == 23
    assert {kind for kind, _ in ACTIONS} == KINDS.keys() - {"custom"}


async def test_custom_http_probe_preserves_the_configured_path_and_query():
    result, requests = await _invoke(
        "custom",
        "test_connection",
        {},
        config={"health_path": "/health/ready%20check?format=json"},
        status=200,
    )
    assert result.ok
    assert [request.url.raw_path for request in requests] == [
        b"/health/ready%20check?format=json"
    ]


@pytest.mark.parametrize("edition", ["cloud", "data_center"])
async def test_confluence_refuses_an_unsafe_default_before_the_credentials_probe(
    edition,
):
    result, requests = await _invoke(
        "confluence",
        "test_connection",
        {},
        config={"edition": edition, "space_id": ".."},
        status=200,
    )
    assert not requests
    assert not result.ok and "space_id" in (result.error or "")


@pytest.mark.parametrize("path", ["../admin", "%2e%2e/admin", "/a/..;x=1/admin"])
async def test_custom_http_probe_rejects_traversal_in_its_configured_path(path):
    result, requests = await _invoke(
        "custom", "test_connection", {}, config={"health_path": path}, status=200
    )
    assert not requests
    assert not result.ok and "health_path" in (result.error or "")


@pytest.mark.parametrize("repo", ["lab/actions/secrets", "lab%2factions%2fsecrets"])
async def test_github_repository_argument_cannot_name_a_subroute(repo):
    result, requests = await _invoke(
        "github", "get_repository", {"owner": "acme", "repo": repo}
    )
    assert not requests
    assert not result.ok and "repo" in (result.error or "")
