from __future__ import annotations

import base64
import json
import uuid

import httpx

from backend.db.models import IntegrationConnector
from backend.integrations.atlassian import (
    BitbucketAdapter,
    ConfluenceAdapter,
    JiraAdapter,
)
from backend.integrations.azure_devops import AzureDevOpsAdapter
from backend.integrations.linear import LinearAdapter
from backend.integrations.notion import NotionAdapter
from backend.integrations.servicenow import ServiceNowAdapter


def _connector(
    kind: str,
    *,
    auth_type: str,
    base_url: str | None = None,
    config: dict | None = None,
) -> IntegrationConnector:
    return IntegrationConnector(
        id=uuid.uuid4(),
        org_id=uuid.uuid4(),
        kind=kind,
        name=f"{kind}-test",
        base_url=base_url,
        auth_type=auth_type,
        config=config or {},
        is_enabled=True,
    )


async def test_bitbucket_cloud_repositories_issues_and_pull_requests():
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert (
            request.headers["authorization"]
            == "Basic " + base64.b64encode(b"admin@example.com:bb-token").decode()
        )
        path = request.url.path
        if path == "/2.0/user":
            return httpx.Response(200, json={"display_name": "Admin"})
        if path.endswith("/repositories/acme/api"):
            return httpx.Response(200, json={"full_name": "acme/api"})
        if path.endswith("/src/main/README.md"):
            return httpx.Response(200, text="hello")
        if path.endswith("/issues") and request.method == "GET":
            return httpx.Response(200, json={"values": [{"id": 1}]})
        if path.endswith("/issues") and request.method == "POST":
            return httpx.Response(201, json={"id": 2})
        if path.endswith("/pullrequests") and request.method == "POST":
            return httpx.Response(201, json={"id": 3})
        if path.endswith("/pullrequests/3/merge"):
            return httpx.Response(200, json={"state": "MERGED"})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    adapter = BitbucketAdapter(
        http_client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        )
    )
    connector = _connector(
        "bitbucket",
        auth_type="pat",
        config={"workspace": "acme", "repo": "api"},
    )
    auth = {"email": "admin@example.com", "api_token": "bb-token"}

    assert (await adapter.safe_invoke("test_connection", connector, auth)).ok
    repository = await adapter.safe_invoke("get_repository", connector, auth)
    assert repository.data["repository"]["full_name"] == "acme/api"
    document = await adapter.safe_invoke(
        "get_file", connector, auth, {"path": "README.md"}
    )
    assert document.data["file"]["content"] == "hello"
    assert (await adapter.safe_invoke("list_issues", connector, auth)).ok
    assert (
        await adapter.safe_invoke(
            "create_issue",
            connector,
            auth,
            {"title": "Failure", "content": "Details"},
        )
    ).ok
    assert (
        await adapter.safe_invoke(
            "create_pull_request",
            connector,
            auth,
            {
                "title": "Fix",
                "source_branch": "fix",
                "target_branch": "main",
            },
        )
    ).ok
    assert (
        await adapter.safe_invoke(
            "merge_pull_request",
            connector,
            auth,
            {"pull_request_id": 3},
        )
    ).ok
    merge = next(
        item for item in adapter.capabilities if item.action == "merge_pull_request"
    )
    assert merge.always_requires_approval is True
    assert len(seen) == 7


async def test_jira_and_confluence_cloud_contracts_use_adf_and_storage():
    jira_requests: list[httpx.Request] = []

    async def jira_handler(request: httpx.Request) -> httpx.Response:
        jira_requests.append(request)
        assert request.headers["authorization"].startswith("Basic ")
        path = request.url.path
        if path.endswith("/myself"):
            return httpx.Response(200, json={"displayName": "Admin"})
        if path.endswith("/issue") and request.method == "POST":
            return httpx.Response(201, json={"id": "10", "key": "OPS-10"})
        if path.endswith("/comment"):
            return httpx.Response(201, json={"id": "20"})
        if path.endswith("/transitions") and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "transitions": [
                        {"id": "31", "name": "Resolve", "to": {"name": "Done"}}
                    ]
                },
            )
        if path.endswith("/transitions") and request.method == "POST":
            return httpx.Response(204)
        raise AssertionError(str(request.url))

    jira = JiraAdapter(
        http_client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(jira_handler)
        )
    )
    jira_connector = _connector(
        "jira",
        auth_type="basic",
        base_url="https://acme.atlassian.net",
        config={"project_key": "OPS"},
    )
    auth = {"email": "admin@example.com", "api_token": "token"}
    assert (await jira.safe_invoke("test_connection", jira_connector, auth)).ok
    created = await jira.safe_invoke(
        "create_issue",
        jira_connector,
        auth,
        {
            "summary": "Failure",
            "description": "Details",
            "incident_id": str(uuid.uuid4()),
        },
    )
    assert created.ok
    assert created.data["ticket_sync"]["external_ticket_id"] == "OPS-10"
    assert (
        await jira.safe_invoke(
            "comment_issue",
            jira_connector,
            auth,
            {"issue_key": "OPS-10", "body": "Investigating"},
        )
    ).ok
    assert (
        await jira.safe_invoke(
            "list_transitions",
            jira_connector,
            auth,
            {"issue_key": "OPS-10"},
        )
    ).ok
    assert (
        await jira.safe_invoke(
            "transition_issue",
            jira_connector,
            auth,
            {"issue_key": "OPS-10", "transition_id": "31"},
        )
    ).ok
    assert (
        await jira.safe_invoke(
            "sync_status_out",
            jira_connector,
            auth,
            {"ticket_id": "OPS-10", "new_status": "Done"},
        )
    ).ok
    create_body = json.loads(jira_requests[1].content)
    assert create_body["fields"]["description"]["type"] == "doc"
    comment_body = json.loads(jira_requests[2].content)
    assert comment_body["body"]["content"][0]["type"] == "paragraph"

    confluence_requests: list[httpx.Request] = []

    async def confluence_handler(request: httpx.Request) -> httpx.Response:
        confluence_requests.append(request)
        if request.url.path.endswith("/spaces"):
            return httpx.Response(200, json={"results": []})
        if request.url.path.endswith("/pages/100"):
            return httpx.Response(200, json={"id": "100", "title": "Runbook"})
        if request.url.path.endswith("/pages"):
            return httpx.Response(201, json={"id": "101", "title": "Postmortem"})
        raise AssertionError(str(request.url))

    confluence = ConfluenceAdapter(
        http_client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(confluence_handler)
        )
    )
    confluence_connector = _connector(
        "confluence",
        auth_type="basic",
        base_url="https://acme.atlassian.net",
        config={"space_id": "123"},
    )
    assert (
        await confluence.safe_invoke("test_connection", confluence_connector, auth)
    ).ok
    assert (
        await confluence.safe_invoke(
            "read_doc",
            confluence_connector,
            auth,
            {"page_id": "100"},
        )
    ).ok
    assert (
        await confluence.safe_invoke(
            "write_doc",
            confluence_connector,
            auth,
            {"title": "Postmortem", "content": "<p>Details</p>"},
        )
    ).ok
    write_body = json.loads(confluence_requests[2].content)
    assert write_body["body"]["representation"] == "storage"


async def test_azure_devops_repos_boards_and_approval_only_merge():
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        expected = base64.b64encode(b":ado-token").decode()
        assert request.headers["authorization"] == f"Basic {expected}"
        path = request.url.path
        if path.endswith("/_apis/projects"):
            return httpx.Response(200, json={"value": []})
        if path.endswith("/repositories/api"):
            return httpx.Response(200, json={"id": "repo-id", "name": "api"})
        if path.endswith("/items"):
            return httpx.Response(
                200,
                text="hello",
                headers={"content-type": "text/plain"},
            )
        if path.endswith("/pullrequests") and request.method == "POST":
            return httpx.Response(201, json={"pullRequestId": 7})
        if path.endswith("/pullrequests/7"):
            return httpx.Response(200, json={"status": "completed"})
        if "/workitems/$Task" in path:
            assert request.headers["content-type"] == "application/json-patch+json"
            return httpx.Response(200, json={"id": 8})
        if path.endswith("/workitems/8") and request.method == "PATCH":
            return httpx.Response(200, json={"id": 8, "rev": 2})
        if path.endswith("/workitems/8"):
            return httpx.Response(200, json={"id": 8, "rev": 1})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    adapter = AzureDevOpsAdapter(
        http_client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        )
    )
    connector = _connector(
        "azure_devops",
        auth_type="pat",
        config={
            "organization": "acme",
            "project": "Ops",
            "repository": "api",
        },
    )
    auth = {"token": "ado-token"}
    assert (await adapter.safe_invoke("test_connection", connector, auth)).ok
    assert (await adapter.safe_invoke("get_repository", connector, auth)).ok
    file_result = await adapter.safe_invoke(
        "get_file", connector, auth, {"path": "/README.md"}
    )
    assert file_result.data["file"]["content"] == "hello"
    assert (
        await adapter.safe_invoke(
            "create_pull_request",
            connector,
            auth,
            {
                "title": "Fix",
                "source_branch": "fix",
                "target_branch": "main",
            },
        )
    ).ok
    assert (
        await adapter.safe_invoke(
            "merge_pull_request",
            connector,
            auth,
            {"pull_request_id": 7, "squash": True},
        )
    ).ok
    assert (
        await adapter.safe_invoke(
            "create_work_item",
            connector,
            auth,
            {"title": "Investigate", "description": "Details"},
        )
    ).ok
    assert (
        await adapter.safe_invoke(
            "get_work_item",
            connector,
            auth,
            {"work_item_id": 8},
        )
    ).ok
    assert (
        await adapter.safe_invoke(
            "update_work_item",
            connector,
            auth,
            {"work_item_id": 8, "fields": {"System.State": "Active"}},
        )
    ).ok
    merge = next(
        item for item in adapter.capabilities if item.action == "merge_pull_request"
    )
    assert merge.always_requires_approval is True
    patch_request = next(
        item
        for item in seen
        if item.method == "PATCH" and item.url.path.endswith("/workitems/8")
    )
    assert json.loads(patch_request.content)[0]["path"] == "/fields/System.State"


async def test_servicenow_table_api_create_and_update_records():
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.headers["authorization"].startswith("Basic ")
        if request.method == "GET" and request.url.path.endswith("/incident"):
            return httpx.Response(200, json={"result": []})
        if request.method == "POST":
            return httpx.Response(
                201, json={"result": {"sys_id": "abc", "number": "INC001"}}
            )
        if request.method == "GET":
            return httpx.Response(200, json={"result": {"sys_id": "abc", "state": "1"}})
        if request.method == "PATCH":
            return httpx.Response(200, json={"result": {"sys_id": "abc", "state": "2"}})
        raise AssertionError(str(request.url))

    adapter = ServiceNowAdapter(
        http_client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        )
    )
    connector = _connector(
        "servicenow",
        auth_type="basic",
        base_url="https://acme.service-now.com",
        config={"table": "incident"},
    )
    auth = {"username": "api", "password": "secret"}
    assert (await adapter.safe_invoke("test_connection", connector, auth)).ok
    assert (
        await adapter.safe_invoke(
            "create_record",
            connector,
            auth,
            {"fields": {"short_description": "Failure"}},
        )
    ).ok
    assert (
        await adapter.safe_invoke("get_record", connector, auth, {"sys_id": "abc"})
    ).ok
    updated = await adapter.safe_invoke(
        "update_record",
        connector,
        auth,
        {"sys_id": "abc", "fields": {"state": "2"}},
    )
    assert updated.data["record"]["state"] == "2"
    synced = await adapter.safe_invoke(
        "sync_status_out",
        connector,
        auth,
        {"ticket_id": "abc", "new_status": "6"},
    )
    assert synced.data["record"]["state"] == "2"
    assert json.loads(seen[-1].content)["state"] == "6"
    assert all("/api/now/table/incident" in item.url.path for item in seen)


async def test_linear_graphql_and_notion_document_contracts():
    linear_requests: list[dict] = []

    async def linear_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "lin_api_key"
        body = json.loads(request.content)
        linear_requests.append(body)
        query = body["query"]
        if "query Viewer" in query:
            return httpx.Response(200, json={"data": {"viewer": {"name": "Admin"}}})
        if "query Issue(" in query:
            return httpx.Response(
                200,
                json={"data": {"issue": {"id": "i1", "identifier": "OPS-1"}}},
            )
        if "query Issues(" in query:
            return httpx.Response(
                200, json={"data": {"issues": {"nodes": [{"id": "i1"}]}}}
            )
        if "mutation CreateIssue" in query:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "issueCreate": {
                            "success": True,
                            "issue": {"id": "i2", "identifier": "OPS-2"},
                        }
                    }
                },
            )
        if "mutation UpdateIssue" in query:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "issueUpdate": {
                            "success": True,
                            "issue": {"id": "i2", "identifier": "OPS-2"},
                        }
                    }
                },
            )
        raise AssertionError(query)

    linear = LinearAdapter(
        http_client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(linear_handler)
        )
    )
    linear_connector = _connector(
        "linear",
        auth_type="api_key",
        config={"team_id": "team-1"},
    )
    auth = {"api_key": "lin_api_key"}
    assert (await linear.safe_invoke("test_connection", linear_connector, auth)).ok
    assert (
        await linear.safe_invoke(
            "get_issue", linear_connector, auth, {"issue_id": "OPS-1"}
        )
    ).ok
    assert (await linear.safe_invoke("list_issues", linear_connector, auth)).ok
    assert (
        await linear.safe_invoke(
            "create_issue",
            linear_connector,
            auth,
            {"title": "Failure", "description": "Details"},
        )
    ).ok
    assert (
        await linear.safe_invoke(
            "update_issue",
            linear_connector,
            auth,
            {"issue_id": "i2", "fields": {"priority": 1}},
        )
    ).ok
    assert linear_requests[3]["variables"]["input"]["teamId"] == "team-1"

    notion_requests: list[httpx.Request] = []

    async def notion_handler(request: httpx.Request) -> httpx.Response:
        notion_requests.append(request)
        assert request.headers["authorization"] == "Bearer notion-token"
        assert request.headers["notion-version"] == "2026-03-11"
        path = request.url.path
        if path.endswith("/users/me"):
            return httpx.Response(200, json={"id": "bot"})
        if path.endswith("/pages/page-1/markdown"):
            return httpx.Response(200, json={"markdown": "# Runbook"})
        if path.endswith("/pages") and request.method == "POST":
            return httpx.Response(200, json={"id": "page-2"})
        if path.endswith("/blocks/page-2/children"):
            return httpx.Response(200, json={"results": [{"id": "block-1"}]})
        raise AssertionError(str(request.url))

    notion = NotionAdapter(
        http_client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(notion_handler)
        )
    )
    notion_connector = _connector(
        "notion",
        auth_type="api_key",
        config={"parent_page_id": "parent-1"},
    )
    notion_auth = {"api_key": "notion-token"}
    assert (
        await notion.safe_invoke("test_connection", notion_connector, notion_auth)
    ).ok
    read = await notion.safe_invoke(
        "read_doc",
        notion_connector,
        notion_auth,
        {"page_id": "page-1"},
    )
    assert read.data["document"]["markdown"] == "# Runbook"
    assert (
        await notion.safe_invoke(
            "create_doc",
            notion_connector,
            notion_auth,
            {"title": "Postmortem", "markdown": "# Details"},
        )
    ).ok
    assert (
        await notion.safe_invoke(
            "append_doc",
            notion_connector,
            notion_auth,
            {"page_id": "page-2", "markdown": "Follow-up"},
        )
    ).ok
    create_body = json.loads(notion_requests[2].content)
    assert create_body["parent"]["page_id"] == "parent-1"
