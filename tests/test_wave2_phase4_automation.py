"""Wave 2 Phase 4 — infrastructure automation adapter tests."""

from __future__ import annotations

import base64
import json
import uuid

import httpx

from backend.db.models import IntegrationConnector
from backend.integrations.automation import (
    AnsibleControllerAdapter,
    ArgoCDAdapter,
    TerraformCloudAdapter,
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


async def test_terraform_cloud_reads_state_and_queues_plan_and_apply():
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.headers["authorization"] == "Bearer terraform-token"
        assert request.headers["content-type"] == "application/vnd.api+json"
        path = request.url.path
        if path.endswith("/organizations/acme/workspaces"):
            return httpx.Response(
                200, json={"data": [{"id": "ws-1", "type": "workspaces"}]}
            )
        if path.endswith("/workspaces/ws-1"):
            return httpx.Response(
                200, json={"data": {"id": "ws-1", "type": "workspaces"}}
            )
        if path.endswith("/runs") and request.method == "GET":
            assert request.url.params["filter[workspace][id]"] == "ws-1"
            return httpx.Response(200, json={"data": [{"id": "run-1", "type": "runs"}]})
        if path.endswith("/runs/run-1") and request.method == "GET":
            return httpx.Response(200, json={"data": {"id": "run-1", "type": "runs"}})
        if path.endswith("/runs") and request.method == "POST":
            return httpx.Response(201, json={"data": {"id": "run-2", "type": "runs"}})
        if path.endswith("/runs/run-2/actions/apply"):
            return httpx.Response(202)
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    adapter = TerraformCloudAdapter(
        http_client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        )
    )
    connector = _connector(
        "terraform_cloud",
        auth_type="api_key",
        config={"organization": "acme", "workspace_id": "ws-1"},
    )
    auth = {"api_key": "terraform-token"}
    assert (await adapter.safe_invoke("test_connection", connector, auth)).ok
    assert (await adapter.safe_invoke("list_workspaces", connector, auth)).ok
    assert (
        await adapter.safe_invoke(
            "get_workspace", connector, auth, {"workspace_id": "ws-1"}
        )
    ).ok
    assert (await adapter.safe_invoke("list_runs", connector, auth)).ok
    assert (
        await adapter.safe_invoke("get_run", connector, auth, {"run_id": "run-1"})
    ).ok
    planned = await adapter.safe_invoke(
        "plan",
        connector,
        auth,
        {
            "message": "Check production",
            "refresh_only": True,
            "variables": {"region": "central"},
        },
    )
    assert planned.data["run"]["id"] == "run-2"
    plan_payload = json.loads(seen[-1].content)
    assert plan_payload["data"]["attributes"]["plan-only"] is True
    assert plan_payload["data"]["attributes"]["refresh-only"] is True
    assert plan_payload["data"]["relationships"]["workspace"]["data"]["id"] == "ws-1"
    applied = await adapter.safe_invoke(
        "apply",
        connector,
        auth,
        {"run_id": "run-2", "comment": "Change approved"},
    )
    assert applied.data["applied"] is True
    assert json.loads(seen[-1].content) == {"comment": "Change approved"}


async def test_argocd_reads_application_state_and_runs_guarded_operations():
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.headers["authorization"] == "Bearer argo-token"
        path = request.url.path
        if path.endswith("/api/v1/version"):
            return httpx.Response(200, json={"Version": "v3"})
        if path.endswith("/api/v1/applications"):
            return httpx.Response(200, json={"items": [{"metadata": {"name": "api"}}]})
        if path.endswith("/api/v1/applications/api/managed-resources"):
            return httpx.Response(200, json={"items": [{"kind": "Deployment"}]})
        if path.endswith("/api/v1/applications/api/sync"):
            return httpx.Response(200, json={"metadata": {"name": "api"}})
        if path.endswith("/api/v1/applications/api/rollback"):
            return httpx.Response(200, json={"metadata": {"name": "api"}})
        if path.endswith("/api/v1/applications/api"):
            return httpx.Response(
                200,
                json={
                    "status": {
                        "sync": {"status": "Synced"},
                        "health": {"status": "Healthy"},
                    }
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    adapter = ArgoCDAdapter(
        http_client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        )
    )
    connector = _connector(
        "argocd",
        auth_type="pat",
        base_url="https://argo.example",
        config={"application": "api"},
    )
    auth = {"token": "argo-token"}
    assert (await adapter.safe_invoke("test_connection", connector, auth)).ok
    assert (await adapter.safe_invoke("list_applications", connector, auth)).ok
    application = await adapter.safe_invoke("get_application", connector, auth)
    assert application.data["application"]["status"]["sync"]["status"] == "Synced"
    assert (await adapter.safe_invoke("get_diff", connector, auth)).ok
    assert (
        await adapter.safe_invoke(
            "sync",
            connector,
            auth,
            {"revision": "main", "prune": True},
        )
    ).ok
    assert json.loads(seen[-1].content) == {
        "prune": True,
        "dryRun": False,
        "revision": "main",
    }
    assert (
        await adapter.safe_invoke("rollback", connector, auth, {"history_id": 7})
    ).ok
    assert json.loads(seen[-1].content) == {"id": 7}


async def test_ansible_controller_reads_templates_and_launches_job():
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        expected = base64.b64encode(b"operator:secret").decode()
        assert request.headers["authorization"] == f"Basic {expected}"
        path = request.url.path
        if path.endswith("/api/v2/ping/"):
            return httpx.Response(200, json={"version": "24.6"})
        if path.endswith("/api/v2/job_templates/"):
            return httpx.Response(200, json={"results": [{"id": 4, "name": "Deploy"}]})
        if path.endswith("/api/v2/job_templates/4/launch/"):
            return httpx.Response(201, json={"job": 19, "status": "pending"})
        if path.endswith("/api/v2/job_templates/4/"):
            return httpx.Response(200, json={"id": 4, "name": "Deploy"})
        if path.endswith("/api/v2/jobs/19/"):
            return httpx.Response(200, json={"id": 19, "status": "successful"})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    adapter = AnsibleControllerAdapter(
        http_client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        )
    )
    connector = _connector(
        "ansible",
        auth_type="basic",
        base_url="https://automation.example",
    )
    auth = {"username": "operator", "password": "secret"}
    assert (await adapter.safe_invoke("test_connection", connector, auth)).ok
    assert (
        await adapter.safe_invoke(
            "list_job_templates", connector, auth, {"name": "deploy"}
        )
    ).ok
    assert (
        await adapter.safe_invoke(
            "get_job_template", connector, auth, {"template_id": 4}
        )
    ).ok
    assert (await adapter.safe_invoke("get_job", connector, auth, {"job_id": 19})).ok
    launched = await adapter.safe_invoke(
        "launch",
        connector,
        auth,
        {
            "template_id": 4,
            "extra_vars": {"release": "2026.06"},
            "inventory": 2,
            "limit": "web",
        },
    )
    assert launched.data["job"]["job"] == 19
    assert json.loads(seen[-1].content) == {
        "extra_vars": {"release": "2026.06"},
        "inventory": 2,
        "limit": "web",
    }


def test_automation_mutations_always_require_tier_one_approval():
    for adapter, actions in (
        (TerraformCloudAdapter(), ("plan", "apply")),
        (ArgoCDAdapter(), ("sync", "rollback")),
        (AnsibleControllerAdapter(), ("launch",)),
    ):
        for action in actions:
            capability = next(
                item for item in adapter.capabilities if item.action == action
            )
            assert capability.classification == "destructive"
            assert capability.always_requires_approval is True
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


def test_automation_adapters_are_registered():
    assert isinstance(get_adapter("terraform_cloud"), TerraformCloudAdapter)
    assert isinstance(get_adapter("argocd"), ArgoCDAdapter)
    assert isinstance(get_adapter("ansible"), AnsibleControllerAdapter)
