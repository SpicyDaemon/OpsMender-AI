"""Kubernetes API context and remediation adapter."""

from __future__ import annotations

import re
import ssl
from datetime import datetime, timezone
from typing import Any

import httpx

from backend.db.models import IntegrationConnector
from backend.integrations.base import (
    IntegrationAdapter,
    IntegrationCapability,
    IntegrationResult,
)
from backend.integrations.http import (
    connector_http_client,
    default_http_client,
    path_segment,
    required,
    response_error,
)
from backend.integrations.registry import register_adapter


# A namespace, pod or deployment name is a DNS name (lowercase letters,
# digits, '-' and '.', starting and ending with a letter or digit): nothing
# else can be one, and no such name can change the path it is put into
# (M1-147).
_DNS_LABEL = r"[a-z0-9](?:[-a-z0-9]*[a-z0-9])?"
_DNS_SUBDOMAIN = rf"{_DNS_LABEL}(?:\.{_DNS_LABEL})*"


def _object_name(value: Any, name: str) -> str:
    """A Kubernetes object name as a URL path segment."""
    text = path_segment(value, name)
    pattern = _DNS_LABEL if name == "namespace" else _DNS_SUBDOMAIN
    limit = 63 if name == "namespace" else 253
    if len(text) > limit or not re.fullmatch(pattern, text):
        shown = str(value).strip()
        shown = shown if len(shown) <= 80 else shown[:80] + "..."
        characters = (
            "lowercase letters, digits and '-'"
            if name == "namespace"
            else ("lowercase letters, digits, '-' and '.'")
        )
        raise ValueError(
            f"{name} must be a Kubernetes name ({characters}), "
            f"such as 'ratings-v1', not {shown!r}."
        )
    return text


class KubernetesAdapter(IntegrationAdapter):
    kind = "kubernetes"
    argument_fallbacks = {"namespace": "default"}
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
        return default_http_client(verify=verify)

    async def _request(self, connector, auth, method, path, **kwargs):
        headers = self._headers(connector, auth)
        headers.update(kwargs.pop("headers", {}))
        async with connector_http_client(self._client(connector, auth)) as client:
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
        namespace: str | None = None,
        label_selector: str | None = None,
        field_selector: str | None = None,
        limit: int = 100,
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
            f"/api/v1/namespaces/{_object_name(namespace, 'namespace')}/pods",
            params=params,
        )
        return failure or IntegrationResult.success(
            pods=response.json().get("items", [])
        )

    async def get_pod(self, connector, auth, pod: str, namespace: str | None = None):
        namespace = self._namespace(connector, namespace)
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            f"/api/v1/namespaces/{_object_name(namespace, 'namespace')}/pods/"
            f"{_object_name(pod, 'pod')}",
        )
        return failure or IntegrationResult.success(pod=response.json())

    async def get_pod_logs(
        self,
        connector,
        auth,
        pod: str,
        namespace: str | None = None,
        container: str | None = None,
        tail_lines: int = 200,
        previous: bool = False,
        timestamps: bool = True,
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
            f"/api/v1/namespaces/{_object_name(namespace, 'namespace')}/pods/"
            f"{_object_name(pod, 'pod')}/log",
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
        namespace: str | None = None,
        field_selector: str | None = None,
        limit: int = 100,
    ):
        namespace = self._namespace(connector, namespace)
        params: dict[str, Any] = {"limit": min(max(int(limit), 1), 500)}
        if field_selector:
            params["fieldSelector"] = field_selector
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            f"/api/v1/namespaces/{_object_name(namespace, 'namespace')}/events",
            params=params,
        )
        return failure or IntegrationResult.success(
            events=response.json().get("items", [])
        )

    async def list_deployments(
        self,
        connector,
        auth,
        namespace: str | None = None,
        label_selector: str | None = None,
        limit: int = 100,
    ):
        namespace = self._namespace(connector, namespace)
        params: dict[str, Any] = {"limit": min(max(int(limit), 1), 500)}
        if label_selector:
            params["labelSelector"] = label_selector
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            f"/apis/apps/v1/namespaces/{_object_name(namespace, 'namespace')}/deployments",
            params=params,
        )
        return failure or IntegrationResult.success(
            deployments=response.json().get("items", [])
        )

    async def get_deployment(
        self, connector, auth, deployment: str, namespace: str | None = None
    ):
        namespace = self._namespace(connector, namespace)
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            f"/apis/apps/v1/namespaces/{_object_name(namespace, 'namespace')}/deployments/"
            f"{_object_name(deployment, 'deployment')}",
        )
        return failure or IntegrationResult.success(deployment=response.json())

    async def restart_deployment(
        self, connector, auth, deployment: str, namespace: str | None = None
    ):
        namespace = self._namespace(connector, namespace)
        restarted_at = datetime.now(timezone.utc).isoformat()
        response, failure = await self._request(
            connector,
            auth,
            "PATCH",
            f"/apis/apps/v1/namespaces/{_object_name(namespace, 'namespace')}/deployments/"
            f"{_object_name(deployment, 'deployment')}",
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
        pod: str,
        namespace: str | None = None,
        grace_period_seconds: int | None = None,
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
            f"/api/v1/namespaces/{_object_name(namespace, 'namespace')}/pods/"
            f"{_object_name(pod, 'pod')}",
            params=params,
        )
        return failure or IntegrationResult.success(deletion=response.json())


register_adapter(KubernetesAdapter())
