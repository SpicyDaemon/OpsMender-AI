"""Terraform Cloud, Argo CD, and Ansible Automation integration adapters."""

from __future__ import annotations

import base64
from typing import Any
from urllib.parse import quote

from backend.db.models import IntegrationConnector
from backend.integrations.base import (
    IntegrationAdapter,
    IntegrationCapability,
    IntegrationResult,
)
from backend.integrations.http import (
    HttpClientFactory,
    default_http_client,
    required,
    response_error,
)
from backend.integrations.registry import register_adapter


class TerraformCloudAdapter(IntegrationAdapter):
    kind = "terraform_cloud"
    capabilities = (
        IntegrationCapability("test_connection", "Validate Terraform Cloud access."),
        IntegrationCapability("list_workspaces", "List organization workspaces."),
        IntegrationCapability("get_workspace", "Read workspace metadata."),
        IntegrationCapability("list_runs", "List workspace runs."),
        IntegrationCapability("get_run", "Read run and plan status."),
        IntegrationCapability(
            "plan",
            "Queue a speculative Terraform plan.",
            classification="destructive",
            mutating=True,
            always_requires_approval=True,
        ),
        IntegrationCapability(
            "apply",
            "Apply an approved Terraform run.",
            classification="destructive",
            mutating=True,
            always_requires_approval=True,
        ),
    )

    def __init__(self, *, http_client_factory: HttpClientFactory | None = None):
        self._factory = http_client_factory or default_http_client

    @staticmethod
    def _base(connector: IntegrationConnector) -> str:
        return (connector.base_url or "https://app.terraform.io/api/v2").rstrip("/")

    @staticmethod
    def _headers(auth: dict[str, Any]) -> dict[str, str]:
        token = required(auth.get("api_key") or auth.get("token"), "api_key")
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/vnd.api+json",
        }

    @staticmethod
    def _organization(connector: IntegrationConnector, organization=None) -> str:
        return required(
            organization or connector.config.get("organization"), "organization"
        )

    async def _request(self, connector, auth, method, path, **kwargs):
        async with self._factory() as client:
            response = await client.request(
                method,
                f"{self._base(connector)}{path}",
                headers=self._headers(auth),
                **kwargs,
            )
        return (
            (None, response_error("Terraform Cloud", response))
            if response.status_code >= 400
            else (response, None)
        )

    async def test_connection(self, connector, auth):
        organization = quote(self._organization(connector), safe="")
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            f"/organizations/{organization}/workspaces",
            params={"page[size]": 1},
        )
        return failure or IntegrationResult.success(
            detail=f"Terraform Cloud connection accepted ({response.status_code})."
        )

    async def list_workspaces(self, connector, auth, organization=None):
        organization = quote(self._organization(connector, organization), safe="")
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            f"/organizations/{organization}/workspaces",
        )
        return failure or IntegrationResult.success(
            workspaces=response.json().get("data", [])
        )

    async def get_workspace(self, connector, auth, workspace_id):
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            f"/workspaces/{quote(required(workspace_id, 'workspace_id'), safe='')}",
        )
        return failure or IntegrationResult.success(
            workspace=response.json().get("data")
        )

    async def list_runs(self, connector, auth, workspace_id=None):
        workspace_id = required(
            workspace_id or connector.config.get("workspace_id"), "workspace_id"
        )
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            "/runs",
            params={"filter[workspace][id]": workspace_id},
        )
        return failure or IntegrationResult.success(
            runs=response.json().get("data", [])
        )

    async def get_run(self, connector, auth, run_id):
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            f"/runs/{quote(required(run_id, 'run_id'), safe='')}",
        )
        return failure or IntegrationResult.success(run=response.json().get("data"))

    async def plan(
        self,
        connector,
        auth,
        workspace_id=None,
        message=None,
        refresh_only=False,
        variables=None,
    ):
        workspace_id = required(
            workspace_id or connector.config.get("workspace_id"), "workspace_id"
        )
        attributes: dict[str, Any] = {
            "message": message or "OpsMender speculative plan",
            "plan-only": True,
        }
        if refresh_only:
            attributes["refresh-only"] = True
        if variables:
            attributes["variables"] = [
                {"key": str(key), "value": str(value)}
                for key, value in variables.items()
            ]
        payload = {
            "data": {
                "type": "runs",
                "attributes": attributes,
                "relationships": {
                    "workspace": {"data": {"type": "workspaces", "id": workspace_id}}
                },
            }
        }
        response, failure = await self._request(
            connector, auth, "POST", "/runs", json=payload
        )
        return failure or IntegrationResult.success(run=response.json().get("data"))

    async def apply(self, connector, auth, run_id, comment=None):
        payload = {"comment": comment or "Approved through OpsMender"}
        response, failure = await self._request(
            connector,
            auth,
            "POST",
            f"/runs/{quote(required(run_id, 'run_id'), safe='')}/actions/apply",
            json=payload,
        )
        return failure or IntegrationResult.success(
            applied=True, status_code=response.status_code
        )


class ArgoCDAdapter(IntegrationAdapter):
    kind = "argocd"
    capabilities = (
        IntegrationCapability("test_connection", "Validate Argo CD access."),
        IntegrationCapability("list_applications", "List Argo CD applications."),
        IntegrationCapability(
            "get_application", "Read application health and sync status."
        ),
        IntegrationCapability("get_diff", "Read managed-resource diff state."),
        IntegrationCapability(
            "sync",
            "Synchronize an Argo CD application.",
            classification="destructive",
            mutating=True,
            always_requires_approval=True,
        ),
        IntegrationCapability(
            "rollback",
            "Roll back an Argo CD application.",
            classification="destructive",
            mutating=True,
            always_requires_approval=True,
        ),
    )

    def __init__(self, *, http_client_factory: HttpClientFactory | None = None):
        self._factory = http_client_factory or default_http_client

    @staticmethod
    def _base(connector: IntegrationConnector) -> str:
        return required(connector.base_url, "base_url").rstrip("/")

    @staticmethod
    def _headers(auth: dict[str, Any]) -> dict[str, str]:
        token = required(
            auth.get("access_token") or auth.get("token") or auth.get("api_key"),
            "token",
        )
        return {"Authorization": f"Bearer {token}"}

    @staticmethod
    def _application(connector: IntegrationConnector, application=None) -> str:
        return required(
            application or connector.config.get("application"), "application"
        )

    async def _request(self, connector, auth, method, path, **kwargs):
        async with self._factory() as client:
            response = await client.request(
                method,
                f"{self._base(connector)}{path}",
                headers=self._headers(auth),
                **kwargs,
            )
        return (
            (None, response_error("Argo CD", response))
            if response.status_code >= 400
            else (response, None)
        )

    async def test_connection(self, connector, auth):
        response, failure = await self._request(
            connector, auth, "GET", "/api/v1/version"
        )
        return failure or IntegrationResult.success(
            detail=f"Argo CD connection accepted ({response.status_code})."
        )

    async def list_applications(self, connector, auth):
        response, failure = await self._request(
            connector, auth, "GET", "/api/v1/applications"
        )
        return failure or IntegrationResult.success(
            applications=response.json().get("items", [])
        )

    async def get_application(self, connector, auth, application=None):
        application = quote(self._application(connector, application), safe="")
        response, failure = await self._request(
            connector, auth, "GET", f"/api/v1/applications/{application}"
        )
        return failure or IntegrationResult.success(application=response.json())

    async def get_diff(self, connector, auth, application=None):
        application = quote(self._application(connector, application), safe="")
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            f"/api/v1/applications/{application}/managed-resources",
        )
        return failure or IntegrationResult.success(
            resources=response.json().get("items", [])
        )

    async def sync(
        self,
        connector,
        auth,
        application=None,
        revision=None,
        prune=False,
        dry_run=False,
    ):
        application = quote(self._application(connector, application), safe="")
        payload: dict[str, Any] = {"prune": bool(prune), "dryRun": bool(dry_run)}
        if revision:
            payload["revision"] = revision
        response, failure = await self._request(
            connector,
            auth,
            "POST",
            f"/api/v1/applications/{application}/sync",
            json=payload,
        )
        return failure or IntegrationResult.success(operation=response.json())

    async def rollback(self, connector, auth, history_id, application=None):
        application = quote(self._application(connector, application), safe="")
        response, failure = await self._request(
            connector,
            auth,
            "POST",
            f"/api/v1/applications/{application}/rollback",
            json={"id": int(history_id)},
        )
        return failure or IntegrationResult.success(operation=response.json())


class AnsibleControllerAdapter(IntegrationAdapter):
    kind = "ansible"
    capabilities = (
        IntegrationCapability(
            "test_connection", "Validate Ansible Automation Controller access."
        ),
        IntegrationCapability("list_job_templates", "List job templates."),
        IntegrationCapability("get_job_template", "Read job-template metadata."),
        IntegrationCapability("get_job", "Read automation job status."),
        IntegrationCapability(
            "launch",
            "Launch an Ansible Automation job template.",
            classification="destructive",
            mutating=True,
            always_requires_approval=True,
        ),
    )

    def __init__(self, *, http_client_factory: HttpClientFactory | None = None):
        self._factory = http_client_factory or default_http_client

    @staticmethod
    def _base(connector: IntegrationConnector) -> str:
        return required(connector.base_url, "base_url").rstrip("/")

    @staticmethod
    def _headers(
        connector: IntegrationConnector, auth: dict[str, Any]
    ) -> dict[str, str]:
        token = auth.get("access_token") or auth.get("token")
        if token:
            return {"Authorization": f"Bearer {token}"}
        username = required(auth.get("username"), "username")
        password = required(auth.get("password"), "password")
        encoded = base64.b64encode(f"{username}:{password}".encode()).decode()
        return {"Authorization": f"Basic {encoded}"}

    async def _request(self, connector, auth, method, path, **kwargs):
        async with self._factory() as client:
            response = await client.request(
                method,
                f"{self._base(connector)}{path}",
                headers=self._headers(connector, auth),
                **kwargs,
            )
        return (
            (None, response_error("Ansible Automation Controller", response))
            if response.status_code >= 400
            else (response, None)
        )

    async def test_connection(self, connector, auth):
        response, failure = await self._request(connector, auth, "GET", "/api/v2/ping/")
        return failure or IntegrationResult.success(
            detail=f"Ansible controller connection accepted ({response.status_code})."
        )

    async def list_job_templates(self, connector, auth, name=None, page_size=100):
        params: dict[str, Any] = {"page_size": min(max(int(page_size), 1), 200)}
        if name:
            params["name__icontains"] = name
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            "/api/v2/job_templates/",
            params=params,
        )
        return failure or IntegrationResult.success(
            job_templates=response.json().get("results", [])
        )

    async def get_job_template(self, connector, auth, template_id):
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            f"/api/v2/job_templates/{int(template_id)}/",
        )
        return failure or IntegrationResult.success(job_template=response.json())

    async def get_job(self, connector, auth, job_id):
        response, failure = await self._request(
            connector, auth, "GET", f"/api/v2/jobs/{int(job_id)}/"
        )
        return failure or IntegrationResult.success(job=response.json())

    async def launch(
        self,
        connector,
        auth,
        template_id,
        extra_vars=None,
        inventory=None,
        limit=None,
        job_tags=None,
    ):
        payload: dict[str, Any] = {}
        if extra_vars:
            payload["extra_vars"] = extra_vars
        if inventory is not None:
            payload["inventory"] = inventory
        if limit:
            payload["limit"] = limit
        if job_tags:
            payload["job_tags"] = job_tags
        response, failure = await self._request(
            connector,
            auth,
            "POST",
            f"/api/v2/job_templates/{int(template_id)}/launch/",
            json=payload,
        )
        return failure or IntegrationResult.success(job=response.json())


register_adapter(TerraformCloudAdapter())
register_adapter(ArgoCDAdapter())
register_adapter(AnsibleControllerAdapter())
