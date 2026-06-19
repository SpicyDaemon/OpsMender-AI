from __future__ import annotations

import base64
import uuid

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.db.models import Base, Incident, IntegrationConnector, Organization
from backend.db.repos import (
    IncidentIntegrationLinkRepo,
    IntegrationConnectorRepo,
)
from backend.integrations.github import GitHubAdapter
from backend.integrations.gitlab import GitLabAdapter
from backend.integrations.tools import IntegrationToolRuntime, merge_integration_skill
from backend.skills.parser import SkillDefinition
from backend.tiers.enforcement import check


def _connector(
    kind: str,
    *,
    auth_type: str = "pat",
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


async def test_github_pat_capabilities_use_expected_rest_contracts():
    requests: list[tuple[str, str, dict | None]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer gh-token"
        requests.append(
            (
                request.method,
                request.url.path,
                (
                    __import__("json").loads(request.content)
                    if request.content
                    else None
                ),
            )
        )
        path = request.url.path
        if path == "/user":
            return httpx.Response(200, json={"login": "octo"})
        if path.endswith("/contents/README.md"):
            return httpx.Response(
                200,
                json={
                    "path": "README.md",
                    "sha": "abc",
                    "size": 5,
                    "encoding": "base64",
                    "content": base64.b64encode(b"hello").decode(),
                    "html_url": "https://example/readme",
                },
            )
        if path.endswith("/issues") and request.method == "GET":
            return httpx.Response(
                200,
                json=[
                    {
                        "number": 1,
                        "title": "Issue",
                        "state": "open",
                        "html_url": "https://example/issues/1",
                    },
                    {
                        "number": 2,
                        "title": "PR",
                        "state": "open",
                        "pull_request": {},
                    },
                ],
            )
        if path.endswith("/issues") and request.method == "POST":
            return httpx.Response(
                201,
                json={
                    "number": 3,
                    "title": "Created",
                    "html_url": "https://example/issues/3",
                },
            )
        if path.endswith("/issues/3/comments"):
            return httpx.Response(
                201, json={"id": 8, "html_url": "https://example/comments/8"}
            )
        if path.endswith("/pulls") and request.method == "POST":
            return httpx.Response(
                201,
                json={
                    "number": 4,
                    "title": "Fix",
                    "html_url": "https://example/pulls/4",
                },
            )
        if path.endswith("/pulls/4/merge"):
            return httpx.Response(200, json={"merged": True, "sha": "merge-sha"})
        if path.endswith("/repos/acme/api"):
            return httpx.Response(
                200,
                json={
                    "full_name": "acme/api",
                    "default_branch": "main",
                    "private": True,
                    "html_url": "https://example/acme/api",
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {path}")

    adapter = GitHubAdapter(
        http_client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        )
    )
    connector = _connector("github", config={"owner": "acme", "repo": "api"})
    auth = {"token": "gh-token"}
    assert (await adapter.safe_invoke("test_connection", connector, auth)).ok
    repo = await adapter.safe_invoke("get_repository", connector, auth)
    assert repo.data["repository"]["full_name"] == "acme/api"
    file_result = await adapter.safe_invoke(
        "get_file", connector, auth, {"path": "README.md", "ref": "main"}
    )
    assert file_result.data["file"]["content"] == "hello"
    issues = await adapter.safe_invoke("list_issues", connector, auth)
    assert [item["number"] for item in issues.data["issues"]] == [1]
    created = await adapter.safe_invoke(
        "create_issue",
        connector,
        auth,
        {"title": "Created", "body": "Details", "labels": ["incident"]},
    )
    assert created.data["issue"]["number"] == 3
    assert (
        await adapter.safe_invoke(
            "comment_issue",
            connector,
            auth,
            {"issue_number": 3, "body": "Update"},
        )
    ).ok
    assert (
        await adapter.safe_invoke(
            "create_pull_request",
            connector,
            auth,
            {
                "title": "Fix",
                "head": "fix",
                "base": "main",
                "draft": True,
            },
        )
    ).ok
    merged = await adapter.safe_invoke(
        "merge_pull_request",
        connector,
        auth,
        {"pull_number": 4, "sha": "head-sha", "merge_method": "squash"},
    )
    assert merged.data["merge"]["merged"] is True
    assert any(
        method == "PUT"
        and path.endswith("/pulls/4/merge")
        and body == {"merge_method": "squash", "sha": "head-sha"}
        for method, path, body in requests
    )


async def test_github_app_exchanges_jwt_for_installation_token_and_supports_enterprise():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    seen: list[tuple[str, str]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, request.headers.get("authorization", "")))
        if request.url.path.endswith("/app/installations/42/access_tokens"):
            assert request.headers["authorization"].startswith("Bearer ey")
            return httpx.Response(201, json={"token": "installation-token"})
        if request.url.path.endswith("/installation/repositories"):
            assert request.headers["authorization"] == "Bearer installation-token"
            return httpx.Response(200, json={"total_count": 2})
        raise AssertionError(str(request.url))

    adapter = GitHubAdapter(
        http_client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        )
    )
    connector = _connector(
        "github",
        auth_type="app",
        base_url="https://github.example.test",
    )
    result = await adapter.safe_invoke(
        "test_connection",
        connector,
        {"app_id": "7", "installation_id": "42", "private_key": pem},
    )
    assert result.ok is True
    assert all(path.startswith("/api/v3/") for path, _ in seen)


async def test_gitlab_pat_and_oauth_use_encoded_v4_contracts():
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.raw_path.decode().split("?", 1)[0]
        if path == "/api/v4/user":
            return httpx.Response(200, json={"username": "root"})
        if path.endswith("/repository/files/src%2Fapp.py"):
            return httpx.Response(
                200,
                json={
                    "file_path": "src/app.py",
                    "blob_id": "blob",
                    "commit_id": "commit",
                    "size": 7,
                    "encoding": "base64",
                    "content": base64.b64encode(b"print(1)").decode(),
                },
            )
        if path.endswith("/issues") and request.method == "GET":
            return httpx.Response(
                200,
                json=[
                    {
                        "iid": 1,
                        "title": "Issue",
                        "state": "opened",
                        "web_url": "https://gitlab/issues/1",
                    }
                ],
            )
        if path.endswith("/issues") and request.method == "POST":
            return httpx.Response(
                201,
                json={
                    "iid": 2,
                    "title": "Created",
                    "web_url": "https://gitlab/issues/2",
                },
            )
        if path.endswith("/issues/2/notes"):
            return httpx.Response(201, json={"id": 4, "body": "Update"})
        if path.endswith("/merge_requests") and request.method == "POST":
            return httpx.Response(
                201,
                json={
                    "iid": 3,
                    "title": "Fix",
                    "web_url": "https://gitlab/mr/3",
                },
            )
        if path.endswith("/merge_requests/3/merge"):
            return httpx.Response(200, json={"state": "merged"})
        if path.endswith("/projects/group%2Fproject"):
            return httpx.Response(
                200,
                json={
                    "id": 9,
                    "path_with_namespace": "group/project",
                    "default_branch": "main",
                    "web_url": "https://gitlab/group/project",
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    adapter = GitLabAdapter(
        http_client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        )
    )
    connector = _connector(
        "gitlab",
        base_url="https://gitlab.example.test",
        config={"project": "group/project"},
    )
    auth = {"token": "gl-token"}
    assert (await adapter.safe_invoke("test_connection", connector, auth)).ok
    project = await adapter.safe_invoke("get_project", connector, auth)
    assert project.data["project"]["id"] == 9
    file_result = await adapter.safe_invoke(
        "get_file", connector, auth, {"path": "src/app.py", "ref": "main"}
    )
    assert file_result.data["file"]["content"] == "print(1)"
    assert (await adapter.safe_invoke("list_issues", connector, auth)).ok
    assert (
        await adapter.safe_invoke("create_issue", connector, auth, {"title": "Created"})
    ).ok
    assert (
        await adapter.safe_invoke(
            "comment_issue",
            connector,
            auth,
            {"issue_iid": 2, "body": "Update"},
        )
    ).ok
    assert (
        await adapter.safe_invoke(
            "create_merge_request",
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
            "merge_merge_request",
            connector,
            auth,
            {"merge_request_iid": 3, "sha": "head"},
        )
    ).ok
    assert all(request.headers.get("private-token") == "gl-token" for request in seen)

    oauth_seen: dict[str, str] = {}

    async def oauth_handler(request: httpx.Request) -> httpx.Response:
        oauth_seen["authorization"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"username": "oauth-user"})

    oauth_adapter = GitLabAdapter(
        http_client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(oauth_handler)
        )
    )
    oauth_connector = _connector("gitlab", auth_type="oauth")
    assert (
        await oauth_adapter.safe_invoke(
            "test_connection",
            oauth_connector,
            {"access_token": "oauth-token"},
        )
    ).ok
    assert oauth_seen["authorization"] == "Bearer oauth-token"


@pytest.fixture
async def source_control_factory(monkeypatch):
    monkeypatch.setenv("OPSMENDER_SECRET_KEY", "source-control-secret")
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    org_id = uuid.uuid4()
    incident_id = uuid.uuid4()
    async with factory() as db:
        db.add(Organization(id=org_id, name="Source", slug="source"))
        db.add(
            Incident(
                id=incident_id,
                org_id=org_id,
                title="Deploy failure",
                description="Release failed",
            )
        )
        await db.commit()
    yield factory, org_id, incident_id
    await engine.dispose()


async def test_runtime_persists_commit_link_and_merge_tools_require_approval(
    source_control_factory, monkeypatch
):
    factory, org_id, incident_id = source_control_factory

    async def handler(request: httpx.Request) -> httpx.Response:
        if "/commits/" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "sha": "abc123",
                    "html_url": "https://github.example/acme/api/commit/abc123",
                    "commit": {"message": "Fix production deploy\n\nDetails"},
                },
            )
        raise AssertionError(str(request.url))

    adapter = GitHubAdapter(
        http_client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        )
    )
    import backend.integrations.registry as registry

    monkeypatch.setitem(registry._ADAPTERS, "github", adapter)
    async with factory() as db:
        connector = await IntegrationConnectorRepo.create(
            db,
            org_id,
            kind="github",
            name="Production repo",
            base_url=None,
            auth_type="pat",
            auth={"token": "token"},
            config={"owner": "acme", "repo": "api"},
            is_enabled=True,
        )
        await db.commit()

    runtime = await IntegrationToolRuntime.create(factory, org_id)
    link_tool = next(
        item
        for item in runtime.descriptors
        if item.capability.action == "link_commit_to_incident"
    )
    result = await runtime.call_tool(
        runtime,
        link_tool.name,
        {"incident_id": str(incident_id), "sha": "abc123"},
    )
    assert result.isError is False
    async with factory() as db:
        links = await IncidentIntegrationLinkRepo.list_for_incident(
            db, org_id, incident_id
        )
        assert len(links) == 1
        assert links[0].connector_id == connector.id
        assert links[0].reference_type == "commit"
        assert links[0].external_id == "abc123"

    skill = merge_integration_skill(
        SkillDefinition(version="1", environment="test", operations=[]),
        runtime.descriptors,
    )
    merge_tool = next(
        item
        for item in runtime.descriptors
        if item.capability.action == "merge_pull_request"
    )
    assert check(merge_tool.name, 0, skill).permitted is False
    tier_one = check(merge_tool.name, 1, skill)
    assert tier_one.permitted is True
    assert tier_one.requires_approval is True
    assert check(merge_tool.name, 2, skill).permitted is False
