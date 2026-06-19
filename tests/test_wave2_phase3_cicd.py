"""Wave 2 Phase 3 — Jenkins, CircleCI, and Azure Pipelines tests."""

from __future__ import annotations

import base64
import json
import uuid
from urllib.parse import parse_qsl

import httpx

from backend.db.models import IntegrationConnector
from backend.integrations.cicd import (
    AzurePipelinesAdapter,
    CircleCIAdapter,
    JenkinsAdapter,
)
from backend.integrations.registry import get_adapter
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


async def test_jenkins_reads_nested_jobs_and_triggers_parameterized_build():
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        expected = base64.b64encode(b"builder:jenkins-token").decode()
        assert request.headers["authorization"] == f"Basic {expected}"
        path = request.url.path
        if path == "/api/json":
            return httpx.Response(200, json={"mode": "NORMAL"})
        if path.endswith("/job/folder/job/service/api/json"):
            return httpx.Response(
                200,
                json={
                    "name": "service",
                    "color": "blue",
                    "lastBuild": {"number": 7, "result": "SUCCESS"},
                },
            )
        if path.endswith("/job/folder/job/service/7/api/json"):
            return httpx.Response(
                200, json={"number": 7, "building": False, "result": "SUCCESS"}
            )
        if path.endswith("/job/folder/job/service/buildWithParameters"):
            return httpx.Response(
                201,
                headers={"Location": "https://jenkins.example/queue/item/9/"},
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    adapter = JenkinsAdapter(
        http_client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        )
    )
    connector = _connector(
        "jenkins",
        auth_type="pat",
        base_url="https://jenkins.example",
        config={"job": "folder/service"},
    )
    auth = {"username": "builder", "api_token": "jenkins-token"}
    assert (await adapter.safe_invoke("test_connection", connector, auth)).ok
    assert (await adapter.safe_invoke("get_job", connector, auth)).ok
    build = await adapter.safe_invoke("get_build", connector, auth, {"build_number": 7})
    assert build.data["build"]["result"] == "SUCCESS"
    triggered = await adapter.safe_invoke(
        "trigger_build",
        connector,
        auth,
        {"parameters": {"DEPLOY_ENV": "staging"}},
    )
    assert triggered.data["queue_url"].endswith("/queue/item/9/")
    assert dict(parse_qsl(seen[-1].content.decode())) == {"DEPLOY_ENV": "staging"}


async def test_circleci_reads_pipeline_and_job_and_triggers_pipeline():
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.headers["circle-token"] == "circle-token"
        path = request.url.raw_path.decode().split("?", 1)[0]
        if path == "/api/v2/me":
            return httpx.Response(200, json={"id": "user-1"})
        if path.endswith("/project/gh%2Facme%2Fservice/pipeline"):
            if request.method == "GET":
                return httpx.Response(200, json={"items": [{"id": "p-1"}]})
            return httpx.Response(201, json={"id": "p-2", "state": "pending"})
        if path.endswith("/pipeline/p-1"):
            return httpx.Response(200, json={"id": "p-1", "state": "created"})
        if path.endswith("/project/gh%2Facme%2Fservice/job/42"):
            return httpx.Response(200, json={"number": 42, "status": "success"})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    adapter = CircleCIAdapter(
        http_client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        )
    )
    connector = _connector(
        "circleci",
        auth_type="api_key",
        config={"project_slug": "gh/acme/service"},
    )
    auth = {"api_key": "circle-token"}
    assert (await adapter.safe_invoke("test_connection", connector, auth)).ok
    assert (await adapter.safe_invoke("list_pipelines", connector, auth)).ok
    assert (
        await adapter.safe_invoke(
            "get_pipeline", connector, auth, {"pipeline_id": "p-1"}
        )
    ).ok
    job = await adapter.safe_invoke("get_job", connector, auth, {"job_number": 42})
    assert job.data["job"]["status"] == "success"
    trigger = await adapter.safe_invoke(
        "trigger_pipeline",
        connector,
        auth,
        {"branch": "main", "parameters": {"deploy": True}},
    )
    assert trigger.data["pipeline"]["id"] == "p-2"
    assert json.loads(seen[-1].content) == {
        "branch": "main",
        "parameters": {"deploy": True},
    }


async def test_azure_pipelines_reads_runs_and_queues_pipeline():
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        expected = base64.b64encode(b":ado-token").decode()
        assert request.headers["authorization"] == f"Basic {expected}"
        path = request.url.path
        if path.endswith("/_apis/pipelines") and request.method == "GET":
            return httpx.Response(200, json={"value": [{"id": 8, "name": "Deploy"}]})
        if path.endswith("/_apis/pipelines/8") and request.method == "GET":
            return httpx.Response(200, json={"id": 8, "name": "Deploy"})
        if path.endswith("/_apis/pipelines/8/runs") and request.method == "GET":
            return httpx.Response(
                200, json={"value": [{"id": 17, "state": "completed"}]}
            )
        if path.endswith("/_apis/pipelines/8/runs/17"):
            return httpx.Response(
                200,
                json={"id": 17, "state": "completed", "result": "succeeded"},
            )
        if path.endswith("/_apis/pipelines/8/runs") and request.method == "POST":
            return httpx.Response(200, json={"id": 18, "state": "inProgress"})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    adapter = AzurePipelinesAdapter(
        http_client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        )
    )
    connector = _connector(
        "azure_pipelines",
        auth_type="pat",
        config={"organization": "acme", "project": "Operations"},
    )
    auth = {"token": "ado-token"}
    assert (await adapter.safe_invoke("test_connection", connector, auth)).ok
    assert (await adapter.safe_invoke("list_pipelines", connector, auth)).ok
    assert (
        await adapter.safe_invoke("get_pipeline", connector, auth, {"pipeline_id": 8})
    ).ok
    assert (
        await adapter.safe_invoke("list_runs", connector, auth, {"pipeline_id": 8})
    ).ok
    assert (
        await adapter.safe_invoke(
            "get_run",
            connector,
            auth,
            {"pipeline_id": 8, "run_id": 17},
        )
    ).ok
    queued = await adapter.safe_invoke(
        "run_pipeline",
        connector,
        auth,
        {
            "pipeline_id": 8,
            "branch": "main",
            "variables": {"environment": "staging"},
            "template_parameters": {"region": "central"},
        },
    )
    assert queued.data["run"]["id"] == 18
    payload = json.loads(seen[-1].content)
    assert payload["resources"]["repositories"]["self"]["refName"] == (
        "refs/heads/main"
    )
    assert payload["variables"]["environment"]["value"] == "staging"


def test_ci_cd_trigger_actions_require_tier_one_approval():
    for adapter, action in (
        (JenkinsAdapter(), "trigger_build"),
        (CircleCIAdapter(), "trigger_pipeline"),
        (AzurePipelinesAdapter(), "run_pipeline"),
    ):
        capability = next(
            item for item in adapter.capabilities if item.action == action
        )
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
        tier_one = check(descriptor.name, 1, skill)
        assert tier_one.permitted is True
        assert tier_one.requires_approval is True
        assert check(descriptor.name, 2, skill).permitted is False


def test_ci_cd_adapters_are_registered():
    assert isinstance(get_adapter("jenkins"), JenkinsAdapter)
    assert isinstance(get_adapter("circleci"), CircleCIAdapter)
    assert isinstance(get_adapter("azure_pipelines"), AzurePipelinesAdapter)
