"""Terraform Cloud, Argo CD, and Ansible Automation integration adapters."""

from __future__ import annotations

import base64
from typing import Any
from urllib.parse import quote

import httpx

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
    untrusted_certificate,
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

    async def list_workspaces(self, connector, auth, organization: str | None = None):
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

    async def get_workspace(self, connector, auth, workspace_id: str):
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            f"/workspaces/{quote(required(workspace_id, 'workspace_id'), safe='')}",
        )
        return failure or IntegrationResult.success(
            workspace=response.json().get("data")
        )

    async def list_runs(self, connector, auth, workspace_id: str | None = None):
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

    async def get_run(self, connector, auth, run_id: str):
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
        workspace_id: str | None = None,
        message: str | None = None,
        refresh_only: bool = False,
        variables: dict[str, Any] | None = None,
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

    async def apply(self, connector, auth, run_id: str, comment: str | None = None):
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
        """Prove the token on a route that needs it.

        The old probe read ``/api/v1/version``, which Argo CD does not serve
        (404 on v3.6.1), so a working token tested as an error; the route
        that does exist, ``/api/version``, answers without a token and would
        have passed a wrong one. The answer has to be Argo CD's own: a page
        from a proxy or from the web interface is not proof of anything
        (M1-145).
        """
        application = str(connector.config.get("application") or "").strip()
        path = "/api/v1/applications"
        params: dict[str, Any] | None = {"fields": "items.metadata.name"}
        if application:
            path += f"/{quote(application, safe='')}"
            params = None
        try:
            async with self._factory() as client:
                response = await client.get(
                    f"{self._base(connector)}{path}",
                    headers=self._headers(auth),
                    params=params,
                )
        except httpx.ConnectError as exc:
            untrusted = untrusted_certificate("Argo CD", exc)
            if untrusted is None:
                raise
            return untrusted
        status = response.status_code
        try:
            body = response.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            # Argo CD answers every API request, refusals included, with a
            # JSON object.
            return IntegrationResult.failure(
                "The base URL does not answer as an Argo CD API server "
                f"(HTTP {status}, not JSON). Enter the address of argocd-server."
            )
        if status >= 400:
            reason = response_error("Argo CD", response).error
            if status == 401:
                return IntegrationResult.failure(
                    f"Argo CD rejected the token ({reason}). Create a new "
                    "token and save it in the connector."
                )
            if application and status in (403, 404):
                # Argo CD answers 403 both for an application that does not
                # exist and for one the token may not read.
                return IntegrationResult.failure(
                    "Argo CD accepted the token, but the application "
                    f"'{application}' was not found or the token may not read "
                    f"it ({reason})."
                )
            return IntegrationResult.failure(reason)
        if application:
            metadata = body.get("metadata")
            name = metadata.get("name") if isinstance(metadata, dict) else None
            if name != application:
                return IntegrationResult.failure(
                    "The base URL answered, but not with the application "
                    f"'{application}'. Enter the address of argocd-server."
                )
            return IntegrationResult.success(
                detail=(
                    f"Argo CD accepted the token; application '{application}' "
                    "is readable."
                )
            )
        if "items" not in body:
            return IntegrationResult.failure(
                "The base URL answered, but not with Argo CD's application "
                "list. Enter the address of argocd-server."
            )
        count = len(body.get("items") or [])
        return IntegrationResult.success(
            detail=(
                f"Argo CD accepted the token; {count} application(s) are "
                "visible to it. No default application is set."
            )
        )

    async def list_applications(self, connector, auth):
        response, failure = await self._request(
            connector, auth, "GET", "/api/v1/applications"
        )
        return failure or IntegrationResult.success(
            applications=response.json().get("items", [])
        )

    async def get_application(self, connector, auth, application: str | None = None):
        application = quote(self._application(connector, application), safe="")
        response, failure = await self._request(
            connector, auth, "GET", f"/api/v1/applications/{application}"
        )
        return failure or IntegrationResult.success(application=response.json())

    async def get_diff(self, connector, auth, application: str | None = None):
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
        application: str | None = None,
        revision: str | None = None,
        prune: bool = False,
        dry_run: bool = False,
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

    async def rollback(
        self, connector, auth, history_id: int, application: str | None = None
    ):
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

    async def list_job_templates(
        self, connector, auth, name: str | None = None, page_size: int = 100
    ):
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

    async def get_job_template(self, connector, auth, template_id: int):
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            f"/api/v2/job_templates/{int(template_id)}/",
        )
        return failure or IntegrationResult.success(job_template=response.json())

    async def get_job(self, connector, auth, job_id: int):
        response, failure = await self._request(
            connector, auth, "GET", f"/api/v2/jobs/{int(job_id)}/"
        )
        return failure or IntegrationResult.success(job=response.json())

    async def launch(
        self,
        connector,
        auth,
        template_id: int,
        extra_vars: dict[str, Any] | str | None = None,
        inventory: int | None = None,
        limit: str | None = None,
        job_tags: str | None = None,
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
