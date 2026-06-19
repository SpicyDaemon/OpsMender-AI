"""Kubernetes API context and remediation adapter."""

from __future__ import annotations

import ssl
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx

from backend.db.models import IntegrationConnector
from backend.integrations.base import (
    IntegrationAdapter,
    IntegrationCapability,
    IntegrationResult,
)
from backend.integrations.http import required, response_error
from backend.integrations.registry import register_adapter


class KubernetesAdapter(IntegrationAdapter):
    kind = "kubernetes"
    capabilities = (
        IntegrationCapability("test_connection", "Validate Kubernetes API access."),
        IntegrationCapability("list_pods", "List pods in a namespace."),
        IntegrationCapability("get_pod", "Read one pod and its status."),
        IntegrationCapability("get_pod_logs", "Read recent pod logs."),
        IntegrationCapability("list_events", "List namespace events."),
        IntegrationCapability("list_deployments", "List deployments."),
        IntegrationCapability("get_deployment", "Read deployment status."),
        IntegrationCapability(
            "restart_deployment",
            "Trigger a deployment rollout restart.",
            classification="caution",
            mutating=True,
        ),
        IntegrationCapability(
            "delete_pod",
            "Delete a pod so its controller may replace it.",
            classification="destructive",
            mutating=True,
        ),
    )

    def __init__(self, *, http_client_factory=None):
        self._factory = http_client_factory

    @staticmethod
    def _base(connector: IntegrationConnector) -> str:
        return required(connector.base_url, "base_url").rstrip("/")

    @staticmethod
    def _namespace(connector: IntegrationConnector, namespace: str | None) -> str:
        return required(
            namespace or connector.config.get("namespace") or "default",
            "namespace",
        )

    @staticmethod
    def _headers(
        connector: IntegrationConnector,
        auth: dict[str, Any],
    ) -> dict[str, str]:
        headers = {
            str(key): str(value) for key, value in (auth.get("headers") or {}).items()
        }
        token = auth.get("token") or auth.get("access_token")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _client(
        self,
        connector: IntegrationConnector,
        auth: dict[str, Any],
    ) -> httpx.AsyncClient:
        if self._factory is not None:
            return self._factory()
        verify: bool | ssl.SSLContext = bool(connector.config.get("verify_tls", True))
        ca_cert = auth.get("ca_cert")
        if ca_cert:
            context = ssl.create_default_context()
            context.load_verify_locations(cadata=str(ca_cert))
            verify = context
        return httpx.AsyncClient(timeout=20, follow_redirects=True, verify=verify)

    async def _request(self, connector, auth, method, path, **kwargs):
        headers = self._headers(connector, auth)
        headers.update(kwargs.pop("headers", {}))
        async with self._client(connector, auth) as client:
            response = await client.request(
                method,
                f"{self._base(connector)}{path}",
                headers=headers,
                **kwargs,
            )
        return (
            (None, response_error("Kubernetes", response))
            if response.status_code >= 400
            else (response, None)
        )

    async def test_connection(self, connector, auth):
        response, failure = await self._request(connector, auth, "GET", "/version")
        if failure:
            return failure
        version = response.json().get("gitVersion") or "unknown version"
        return IntegrationResult.success(
            detail=f"Kubernetes API accepted credentials ({version})."
        )

    async def list_pods(
        self,
        connector,
        auth,
        namespace=None,
        label_selector=None,
        field_selector=None,
        limit=100,
    ):
        namespace = self._namespace(connector, namespace)
        params: dict[str, Any] = {"limit": min(max(int(limit), 1), 500)}
        if label_selector:
            params["labelSelector"] = label_selector
        if field_selector:
            params["fieldSelector"] = field_selector
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            f"/api/v1/namespaces/{quote(namespace, safe='')}/pods",
            params=params,
        )
        return failure or IntegrationResult.success(
            pods=response.json().get("items", [])
        )

    async def get_pod(self, connector, auth, pod, namespace=None):
        namespace = self._namespace(connector, namespace)
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            f"/api/v1/namespaces/{quote(namespace, safe='')}/pods/"
            f"{quote(required(pod, 'pod'), safe='')}",
        )
        return failure or IntegrationResult.success(pod=response.json())

    async def get_pod_logs(
        self,
        connector,
        auth,
        pod,
        namespace=None,
        container=None,
        tail_lines=200,
        previous=False,
        timestamps=True,
    ):
        namespace = self._namespace(connector, namespace)
        params: dict[str, Any] = {
            "tailLines": min(max(int(tail_lines), 1), 5000),
            "previous": str(bool(previous)).lower(),
            "timestamps": str(bool(timestamps)).lower(),
        }
        if container:
            params["container"] = container
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            f"/api/v1/namespaces/{quote(namespace, safe='')}/pods/"
            f"{quote(required(pod, 'pod'), safe='')}/log",
            params=params,
        )
        return failure or IntegrationResult.success(
            logs=response.text,
            pod=pod,
            namespace=namespace,
        )

    async def list_events(
        self,
        connector,
        auth,
        namespace=None,
        field_selector=None,
        limit=100,
    ):
        namespace = self._namespace(connector, namespace)
        params: dict[str, Any] = {"limit": min(max(int(limit), 1), 500)}
        if field_selector:
            params["fieldSelector"] = field_selector
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            f"/api/v1/namespaces/{quote(namespace, safe='')}/events",
            params=params,
        )
        return failure or IntegrationResult.success(
            events=response.json().get("items", [])
        )

    async def list_deployments(
        self,
        connector,
        auth,
        namespace=None,
        label_selector=None,
        limit=100,
    ):
        namespace = self._namespace(connector, namespace)
        params: dict[str, Any] = {"limit": min(max(int(limit), 1), 500)}
        if label_selector:
            params["labelSelector"] = label_selector
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            f"/apis/apps/v1/namespaces/{quote(namespace, safe='')}/deployments",
            params=params,
        )
        return failure or IntegrationResult.success(
            deployments=response.json().get("items", [])
        )

    async def get_deployment(self, connector, auth, deployment, namespace=None):
        namespace = self._namespace(connector, namespace)
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            f"/apis/apps/v1/namespaces/{quote(namespace, safe='')}/deployments/"
            f"{quote(required(deployment, 'deployment'), safe='')}",
        )
        return failure or IntegrationResult.success(deployment=response.json())

    async def restart_deployment(self, connector, auth, deployment, namespace=None):
        namespace = self._namespace(connector, namespace)
        restarted_at = datetime.now(timezone.utc).isoformat()
        response, failure = await self._request(
            connector,
            auth,
            "PATCH",
            f"/apis/apps/v1/namespaces/{quote(namespace, safe='')}/deployments/"
            f"{quote(required(deployment, 'deployment'), safe='')}",
            headers={"Content-Type": "application/strategic-merge-patch+json"},
            json={
                "spec": {
                    "template": {
                        "metadata": {
                            "annotations": {"opsmender.io/restartedAt": restarted_at}
                        }
                    }
                }
            },
        )
        return failure or IntegrationResult.success(
            deployment=response.json(),
            restarted_at=restarted_at,
        )

    async def delete_pod(
        self,
        connector,
        auth,
        pod,
        namespace=None,
        grace_period_seconds=None,
    ):
        namespace = self._namespace(connector, namespace)
        params = (
            {"gracePeriodSeconds": max(int(grace_period_seconds), 0)}
            if grace_period_seconds is not None
            else None
        )
        response, failure = await self._request(
            connector,
            auth,
            "DELETE",
            f"/api/v1/namespaces/{quote(namespace, safe='')}/pods/"
            f"{quote(required(pod, 'pod'), safe='')}",
            params=params,
        )
        return failure or IntegrationResult.success(deletion=response.json())


register_adapter(KubernetesAdapter())
