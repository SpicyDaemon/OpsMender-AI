"""Wave 2 Phase 5 — Gitea, Google Docs, and Statuspage adapter tests."""

from __future__ import annotations

import base64
import json
import uuid
from urllib.parse import parse_qs

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import httpx
import jwt

from backend.db.models import IntegrationConnector
from backend.integrations.gitea import GiteaAdapter
from backend.integrations.google_docs import GoogleDocsAdapter
from backend.integrations.registry import get_adapter
from backend.integrations.statuspage import StatuspageAdapter
from backend.integrations.tools import (
    IntegrationToolDescriptor,
    merge_integration_skill,
)
from backend.skills.parser import SkillDefinition
from backend.tiers.enforcement import check


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


def _private_key() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


async def test_gitea_repository_issue_and_pull_request_shape():
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.headers["authorization"] == "token gitea-token"
        path = request.url.path
        if path.endswith("/user"):
            return httpx.Response(200, json={"login": "operator"})
        if path.endswith("/repos/acme/service") and request.method == "GET":
            return httpx.Response(200, json={"full_name": "acme/service"})
        if path.endswith("/contents/runbook.md"):
            return httpx.Response(
                200,
                json={
                    "path": "runbook.md",
                    "sha": "abc",
                    "encoding": "base64",
                    "content": base64.b64encode(b"# Runbook").decode(),
                },
            )
        if path.endswith("/issues") and request.method == "GET":
            return httpx.Response(200, json=[{"number": 1, "title": "Outage"}])
        if path.endswith("/issues") and request.method == "POST":
            return httpx.Response(201, json={"number": 2, "title": "Follow-up"})
        if path.endswith("/issues/2/comments"):
            return httpx.Response(201, json={"id": 8, "body": "Investigating"})
        if path.endswith("/pulls") and request.method == "GET":
            return httpx.Response(200, json=[{"number": 4, "title": "Fix"}])
        if path.endswith("/pulls") and request.method == "POST":
            return httpx.Response(
                201, json={"number": 5, "title": "Fix", "html_url": "https://g/pr/5"}
            )
        if path.endswith("/pulls/5/merge"):
            return httpx.Response(200)
        if path.endswith("/git/commits/deadbeef"):
            return httpx.Response(
                200,
                json={
                    "sha": "deadbeef",
                    "message": "Repair service",
                    "html_url": "https://g/commit/deadbeef",
                },
            )
        if path.endswith("/pulls/5") and request.method == "GET":
            return httpx.Response(
                200, json={"number": 5, "title": "Fix", "html_url": "https://g/pr/5"}
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    adapter = GiteaAdapter(
        http_client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        )
    )
    connector = _connector(
        "gitea",
        auth_type="pat",
        base_url="https://git.example",
        config={"owner": "acme", "repo": "service"},
    )
    auth = {"token": "gitea-token"}
    assert (await adapter.safe_invoke("test_connection", connector, auth)).ok
    assert (await adapter.safe_invoke("get_repository", connector, auth)).ok
    file_result = await adapter.safe_invoke(
        "get_file", connector, auth, {"path": "runbook.md"}
    )
    assert file_result.data["file"]["content"] == "# Runbook"
    assert (await adapter.safe_invoke("list_issues", connector, auth)).ok
    assert (
        await adapter.safe_invoke(
            "create_issue", connector, auth, {"title": "Follow-up"}
        )
    ).ok
    assert (
        await adapter.safe_invoke(
            "comment_issue",
            connector,
            auth,
            {"issue_number": 2, "body": "Investigating"},
        )
    ).ok
    assert (await adapter.safe_invoke("list_pull_requests", connector, auth)).ok
    assert (
        await adapter.safe_invoke(
            "create_pull_request",
            connector,
            auth,
            {"title": "Fix", "head": "fix", "base": "main"},
        )
    ).ok
    assert (
        await adapter.safe_invoke(
            "merge_pull_request",
            connector,
            auth,
            {"pull_number": 5, "merge_style": "squash"},
        )
    ).ok
    assert json.loads(seen[-1].content) == {"Do": "squash"}
    commit_link = await adapter.safe_invoke(
        "link_commit_to_incident",
        connector,
        auth,
        {"incident_id": str(uuid.uuid4()), "sha": "deadbeef"},
    )
    assert commit_link.data["integration_link"]["external_id"] == "deadbeef"
    pr_link = await adapter.safe_invoke(
        "link_pull_request_to_incident",
        connector,
        auth,
        {"incident_id": str(uuid.uuid4()), "pull_number": 5},
    )
    assert pr_link.data["integration_link"]["reference_type"] == "pull_request"


async def test_google_docs_oauth_reads_and_exports_text():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer oauth-token"
        if request.url.path.endswith("/drive/v3/about"):
            return httpx.Response(200, json={"user": {"displayName": "Ops"}})
        if request.url.path.endswith("/v1/documents/doc-1"):
            return httpx.Response(200, json={"documentId": "doc-1", "title": "Runbook"})
        if request.url.path.endswith("/drive/v3/files/doc-1/export"):
            assert request.url.params["mimeType"] == "text/plain"
            return httpx.Response(200, content=b"Runbook text")
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    adapter = GoogleDocsAdapter(
        http_client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        )
    )
    connector = _connector("google_docs", auth_type="oauth")
    auth = {"access_token": "oauth-token"}
    assert (await adapter.safe_invoke("test_connection", connector, auth)).ok
    document = await adapter.safe_invoke(
        "read_doc", connector, auth, {"document_id": "doc-1"}
    )
    assert document.data["document"]["title"] == "Runbook"
    exported = await adapter.safe_invoke(
        "export_doc", connector, auth, {"document_id": "doc-1"}
    )
    assert exported.data["export"]["content"] == "Runbook text"


async def test_google_docs_service_account_exchanges_assertion_and_exports_binary():
    assertions: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":
            form = parse_qs(request.content.decode())
            assertions.append(form["assertion"][0])
            return httpx.Response(200, json={"access_token": "service-token"})
        assert request.headers["authorization"] == "Bearer service-token"
        return httpx.Response(200, content=b"%PDF-test")

    adapter = GoogleDocsAdapter(
        http_client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        )
    )
    connector = _connector("google_docs", auth_type="custom")
    exported = await adapter.safe_invoke(
        "export_doc",
        connector,
        {
            "client_email": "docs@project.iam.gserviceaccount.com",
            "private_key": _private_key(),
        },
        {"document_id": "doc-2", "mime_type": "application/pdf"},
    )
    assert (
        exported.data["export"]["content_base64"]
        == base64.b64encode(b"%PDF-test").decode()
    )
    claims = jwt.decode(assertions[0], options={"verify_signature": False})
    assert claims["iss"] == "docs@project.iam.gserviceaccount.com"
    assert "documents.readonly" in claims["scope"]


async def test_statuspage_reads_components_and_creates_incident():
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.headers["authorization"] == "OAuth status-token"
        path = request.url.path
        if path.endswith("/pages/page-1"):
            return httpx.Response(200, json={"id": "page-1"})
        if path.endswith("/components"):
            return httpx.Response(200, json=[{"id": "component-1"}])
        if path.endswith("/incidents/incident-1"):
            return httpx.Response(200, json={"id": "incident-1"})
        if path.endswith("/incidents") and request.method == "GET":
            return httpx.Response(200, json=[{"id": "incident-1"}])
        if path.endswith("/incidents") and request.method == "POST":
            return httpx.Response(201, json={"id": "incident-2"})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    adapter = StatuspageAdapter(
        http_client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        )
    )
    connector = _connector(
        "statuspage", auth_type="api_key", config={"page_id": "page-1"}
    )
    auth = {"api_key": "status-token"}
    assert (await adapter.safe_invoke("test_connection", connector, auth)).ok
    assert (await adapter.safe_invoke("list_components", connector, auth)).ok
    assert (await adapter.safe_invoke("list_incidents", connector, auth)).ok
    assert (
        await adapter.safe_invoke(
            "get_incident", connector, auth, {"incident_id": "incident-1"}
        )
    ).ok
    created = await adapter.safe_invoke(
        "create_incident",
        connector,
        auth,
        {
            "name": "Checkout disruption",
            "body": "Investigating elevated errors.",
            "component_ids": ["component-1"],
        },
    )
    assert created.data["incident"]["id"] == "incident-2"
    assert json.loads(seen[-1].content)["incident"]["component_ids"] == ["component-1"]


def test_phase5_mutations_use_tier_policy():
    for adapter, action, always in (
        (GiteaAdapter(), "create_issue", False),
        (GiteaAdapter(), "merge_pull_request", True),
        (StatuspageAdapter(), "create_incident", False),
    ):
        capability = next(
            item for item in adapter.capabilities if item.action == action
        )
        assert capability.always_requires_approval is always
        connector_id = uuid.uuid4()
        descriptor = IntegrationToolDescriptor(
            name=f"integration__{adapter.kind}__{action}__{connector_id.hex}",
            description=capability.description,
            connector_id=connector_id,
            capability=capability,
        )
        skill = merge_integration_skill(
            SkillDefinition(version="1", environment="test", operations=[]),
            [descriptor],
        )
        assert check(descriptor.name, 0, skill).permitted is False
        assert check(descriptor.name, 1, skill).requires_approval is True
        assert check(descriptor.name, 2, skill).permitted is False


def test_phase5_integration_adapters_are_registered():
    assert isinstance(get_adapter("gitea"), GiteaAdapter)
    assert isinstance(get_adapter("google_docs"), GoogleDocsAdapter)
    assert isinstance(get_adapter("statuspage"), StatuspageAdapter)
