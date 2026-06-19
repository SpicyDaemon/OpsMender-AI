from __future__ import annotations

import json
import uuid

import httpx

from backend.bots.connectors.discord import DiscordAdapter
from backend.db.models import BotConnector, IntegrationConnector
from backend.ingest.adapters.newrelic import NewRelicAdapter
from backend.ingest.adapters.sentry import SentryAdapter
from backend.ingest.adapters.splunk import SplunkAdapter
from backend.ingest.registry import list_providers
from backend.integrations.kubernetes import KubernetesAdapter
from backend.integrations.tools import (
    IntegrationToolDescriptor,
    merge_integration_skill,
)
from backend.skills.parser import SkillDefinition
from backend.tiers.enforcement import check


def _connector() -> IntegrationConnector:
    return IntegrationConnector(
        id=uuid.uuid4(),
        org_id=uuid.uuid4(),
        kind="kubernetes",
        name="production",
        base_url="https://cluster.example.test:6443",
        auth_type="pat",
        config={"namespace": "production"},
        is_enabled=True,
    )


def test_sentry_newrelic_and_splunk_parse_provider_contracts():
    sentry = SentryAdapter().parse(
        {
            "action": "created",
            "data": {
                "issue": {
                    "id": "100",
                    "title": "ZeroDivisionError",
                    "culprit": "checkout.views",
                    "level": "fatal",
                    "status": "unresolved",
                    "permalink": "https://sentry.example/issues/100",
                    "project": {"slug": "checkout"},
                }
            },
        }
    )
    assert sentry.external_source == "sentry"
    assert sentry.external_id == "100"
    assert sentry.severity == "critical"
    assert sentry.status == "open"

    resolved = SentryAdapter().parse(
        {
            "action": "resolved",
            "data": {
                "issue": {
                    "id": "100",
                    "title": "ZeroDivisionError",
                    "level": "error",
                }
            },
        }
    )
    assert resolved.status == "resolved"

    newrelic = NewRelicAdapter().parse(
        {
            "issueId": "nr-1",
            "issueTitle": "Checkout latency",
            "priority": "CRITICAL",
            "state": "ACTIVATED",
            "issuePageUrl": "https://one.newrelic.com/issues/nr-1",
            "accumulations": {
                "conditionName": ["Latency above 2s"],
                "policyName": ["Checkout"],
            },
            "entitiesData": {"names": ["checkout-api"]},
        }
    )
    assert newrelic.external_id == "nr-1"
    assert newrelic.severity == "critical"
    assert newrelic.status == "open"
    assert "checkout-api" in newrelic.description

    splunk = SplunkAdapter().parse(
        {
            "sid": "scheduler_admin_search_W2_at_1",
            "search_name": "Elevated 5xx",
            "owner": "admin",
            "app": "search",
            "results_link": "https://splunk.example/app/search/results",
            "result": {"count": "8", "severity": "warning"},
        }
    )
    assert splunk.external_source == "splunk"
    assert splunk.external_id == "scheduler_admin_search_W2_at_1"
    assert splunk.severity == "medium"
    assert "Elevated 5xx" in splunk.title

    providers = {item["key"] for item in list_providers()}
    assert {"sentry", "newrelic", "splunk"} <= providers


async def test_kubernetes_context_and_remediation_contracts_are_tier_governed():
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["authorization"] == "Bearer service-token"
        path = request.url.path
        if path == "/version":
            return httpx.Response(200, json={"gitVersion": "v1.36.0"})
        if path.endswith("/pods") and request.method == "GET":
            return httpx.Response(
                200, json={"items": [{"metadata": {"name": "api-1"}}]}
            )
        if path.endswith("/pods/api-1/log"):
            return httpx.Response(200, text="line one\nline two")
        if path.endswith("/events"):
            return httpx.Response(200, json={"items": [{"reason": "BackOff"}]})
        if path.endswith("/deployments") and request.method == "GET":
            return httpx.Response(200, json={"items": [{"metadata": {"name": "api"}}]})
        if path.endswith("/deployments/api") and request.method == "GET":
            return httpx.Response(200, json={"metadata": {"name": "api"}})
        if path.endswith("/deployments/api") and request.method == "PATCH":
            assert (
                request.headers["content-type"]
                == "application/strategic-merge-patch+json"
            )
            body = json.loads(request.content)
            annotations = body["spec"]["template"]["metadata"]["annotations"]
            assert "opsmender.io/restartedAt" in annotations
            return httpx.Response(200, json={"metadata": {"name": "api"}})
        if path.endswith("/pods/api-1") and request.method == "DELETE":
            return httpx.Response(200, json={"status": "Success"})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    adapter = KubernetesAdapter(
        http_client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        )
    )
    connector = _connector()
    auth = {"token": "service-token"}
    assert (await adapter.safe_invoke("test_connection", connector, auth)).ok
    pods = await adapter.safe_invoke("list_pods", connector, auth)
    assert pods.data["pods"][0]["metadata"]["name"] == "api-1"
    logs = await adapter.safe_invoke("get_pod_logs", connector, auth, {"pod": "api-1"})
    assert "line two" in logs.data["logs"]
    assert (await adapter.safe_invoke("list_events", connector, auth)).ok
    assert (await adapter.safe_invoke("list_deployments", connector, auth)).ok
    assert (
        await adapter.safe_invoke(
            "get_deployment", connector, auth, {"deployment": "api"}
        )
    ).ok
    assert (
        await adapter.safe_invoke(
            "restart_deployment", connector, auth, {"deployment": "api"}
        )
    ).ok
    assert (
        await adapter.safe_invoke("delete_pod", connector, auth, {"pod": "api-1"})
    ).ok

    for action in ("restart_deployment", "delete_pod"):
        capability = next(
            item for item in adapter.capabilities if item.action == action
        )
        descriptor = IntegrationToolDescriptor(
            name=f"integration__kubernetes__{action}__{connector.id.hex}",
            description=capability.description,
            connector_id=connector.id,
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
    assert len(requests) == 8


async def test_discord_returns_durable_receipts_and_edits_track_messages():
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["authorization"] == "Bot discord-token"
        if request.method == "POST":
            return httpx.Response(
                200, json={"id": "message-1", "channel_id": "channel-1"}
            )
        if request.method == "PATCH":
            return httpx.Response(
                200, json={"id": "message-1", "channel_id": "channel-1"}
            )
        raise AssertionError(str(request.url))

    adapter = DiscordAdapter(
        http_client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        )
    )
    connector = BotConnector(
        name="discord-status",
        platform="discord",
        credentials={"bot_token": "discord-token"},
        config={"default_chat_id": "channel-1"},
        allowed_capabilities=["notifications"],
        lanes=["respond", "track"],
        status="configured",
        is_enabled=True,
    )
    receipt = await adapter.send_incident_update(
        connector,
        chat_id="channel-1",
        text="Incident created",
        status_update=True,
    )
    assert receipt.ok is True
    assert receipt.external_message_id == "message-1"
    assert receipt.can_update is True

    updated = await adapter.update_incident_update(
        connector,
        chat_id="channel-1",
        text="Incident resolved",
        external_message_id="message-1",
        status_update=True,
    )
    assert updated.ok is True
    assert updated.receipt.external_message_id == "message-1"
    assert requests[1].method == "PATCH"
    assert requests[1].url.path.endswith("/channels/channel-1/messages/message-1")
