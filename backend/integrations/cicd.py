"""Jenkins, CircleCI, and Azure Pipelines integration adapters."""

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


class JenkinsAdapter(IntegrationAdapter):
    kind = "jenkins"
    capabilities = (
        IntegrationCapability("test_connection", "Validate Jenkins access."),
        IntegrationCapability("get_job", "Read Jenkins job status."),
        IntegrationCapability("get_build", "Read Jenkins build status."),
        IntegrationCapability(
            "trigger_build",
            "Trigger a Jenkins build.",
            classification="caution",
            mutating=True,
        ),
    )

    def __init__(self, *, http_client_factory: HttpClientFactory | None = None):
        self._factory = http_client_factory or default_http_client

    @staticmethod
    def _base(connector: IntegrationConnector) -> str:
        return required(connector.base_url, "base_url").rstrip("/")

    @staticmethod
    def _headers(auth: dict[str, Any]) -> dict[str, str]:
        username = required(auth.get("username"), "username")
        token = required(
            auth.get("api_token") or auth.get("token") or auth.get("password"),
            "api_token",
        )
        encoded = base64.b64encode(f"{username}:{token}".encode()).decode()
        return {"Authorization": f"Basic {encoded}"}

    @staticmethod
    def _job_path(connector: IntegrationConnector, job: str | None) -> str:
        raw = required(job or connector.config.get("job"), "job")
        return "".join(
            f"/job/{quote(part, safe='')}" for part in raw.strip("/").split("/")
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
            (None, response_error("Jenkins", response))
            if response.status_code >= 400
            else (response, None)
        )

    async def test_connection(self, connector, auth):
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            "/api/json",
            params={"tree": "mode,nodeDescription"},
        )
        return failure or IntegrationResult.success(
            detail=f"Jenkins connection accepted ({response.status_code})."
        )

    async def get_job(self, connector, auth, job=None):
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            f"{self._job_path(connector, job)}/api/json",
            params={
                "tree": "name,url,color,buildable,lastBuild[number,url,building,result]"
            },
        )
        return failure or IntegrationResult.success(job=response.json())

    async def get_build(self, connector, auth, build_number="lastBuild", job=None):
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            f"{self._job_path(connector, job)}/"
            f"{quote(str(build_number), safe='')}/api/json",
        )
        return failure or IntegrationResult.success(build=response.json())

    async def trigger_build(self, connector, auth, job=None, parameters=None):
        suffix = "buildWithParameters" if parameters else "build"
        response, failure = await self._request(
            connector,
            auth,
            "POST",
            f"{self._job_path(connector, job)}/{suffix}",
            data=parameters or None,
        )
        return failure or IntegrationResult.success(
            queued=True,
            queue_url=response.headers.get("location"),
            status_code=response.status_code,
        )


class CircleCIAdapter(IntegrationAdapter):
    kind = "circleci"
    capabilities = (
        IntegrationCapability("test_connection", "Validate CircleCI access."),
        IntegrationCapability("list_pipelines", "List project pipelines."),
        IntegrationCapability("get_pipeline", "Read pipeline status."),
        IntegrationCapability("get_job", "Read job status."),
        IntegrationCapability(
            "trigger_pipeline",
            "Trigger a CircleCI pipeline.",
            classification="caution",
            mutating=True,
        ),
    )

    def __init__(self, *, http_client_factory: HttpClientFactory | None = None):
        self._factory = http_client_factory or default_http_client

    @staticmethod
    def _base(connector: IntegrationConnector) -> str:
        return (connector.base_url or "https://circleci.com/api/v2").rstrip("/")

    @staticmethod
    def _headers(auth: dict[str, Any]) -> dict[str, str]:
        return {"Circle-Token": required(auth.get("api_key"), "api_key")}

    @staticmethod
    def _project(connector: IntegrationConnector, project_slug=None) -> str:
        return required(
            project_slug or connector.config.get("project_slug"), "project_slug"
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
            (None, response_error("CircleCI", response))
            if response.status_code >= 400
            else (response, None)
        )

    async def test_connection(self, connector, auth):
        response, failure = await self._request(connector, auth, "GET", "/me")
        return failure or IntegrationResult.success(
            detail=f"CircleCI credentials accepted ({response.status_code})."
        )

    async def list_pipelines(self, connector, auth, project_slug=None, branch=None):
        project = quote(self._project(connector, project_slug), safe="")
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            f"/project/{project}/pipeline",
            params={"branch": branch} if branch else None,
        )
        return failure or IntegrationResult.success(
            pipelines=response.json().get("items", [])
        )

    async def get_pipeline(self, connector, auth, pipeline_id):
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            f"/pipeline/{quote(required(pipeline_id, 'pipeline_id'), safe='')}",
        )
        return failure or IntegrationResult.success(pipeline=response.json())

    async def get_job(self, connector, auth, job_number, project_slug=None):
        project = quote(self._project(connector, project_slug), safe="")
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            f"/project/{project}/job/{int(job_number)}",
        )
        return failure or IntegrationResult.success(job=response.json())

    async def trigger_pipeline(
        self,
        connector,
        auth,
        project_slug=None,
        branch=None,
        tag=None,
        parameters=None,
    ):
        if branch and tag:
            raise ValueError("Choose branch or tag, not both")
        project = quote(self._project(connector, project_slug), safe="")
        payload: dict[str, Any] = {}
        if branch:
            payload["branch"] = branch
        if tag:
            payload["tag"] = tag
        if parameters:
            payload["parameters"] = parameters
        response, failure = await self._request(
            connector,
            auth,
            "POST",
            f"/project/{project}/pipeline",
            json=payload,
        )
        return failure or IntegrationResult.success(pipeline=response.json())


class AzurePipelinesAdapter(IntegrationAdapter):
    kind = "azure_pipelines"
    capabilities = (
        IntegrationCapability("test_connection", "Validate Azure Pipelines access."),
        IntegrationCapability("list_pipelines", "List pipelines."),
        IntegrationCapability("get_pipeline", "Read pipeline metadata."),
        IntegrationCapability("list_runs", "List pipeline runs."),
        IntegrationCapability("get_run", "Read pipeline run status."),
        IntegrationCapability(
            "run_pipeline",
            "Run an Azure Pipeline.",
            classification="caution",
            mutating=True,
        ),
    )

    def __init__(self, *, http_client_factory: HttpClientFactory | None = None):
        self._factory = http_client_factory or default_http_client

    @staticmethod
    def _base(connector: IntegrationConnector) -> str:
        if connector.base_url:
            return connector.base_url.rstrip("/")
        organization = required(connector.config.get("organization"), "organization")
        return f"https://dev.azure.com/{quote(organization, safe='')}"

    @staticmethod
    def _headers(
        connector: IntegrationConnector, auth: dict[str, Any]
    ) -> dict[str, str]:
        token = required(auth.get("access_token") or auth.get("token"), "token")
        if connector.auth_type == "oauth":
            return {"Authorization": f"Bearer {token}"}
        encoded = base64.b64encode(f":{token}".encode()).decode()
        return {"Authorization": f"Basic {encoded}"}

    @staticmethod
    def _project(connector: IntegrationConnector, project=None) -> str:
        return required(project or connector.config.get("project"), "project")

    async def _request(self, connector, auth, method, path, **kwargs):
        async with self._factory() as client:
            response = await client.request(
                method,
                f"{self._base(connector)}{path}",
                headers=self._headers(connector, auth),
                **kwargs,
            )
        return (
            (None, response_error("Azure Pipelines", response))
            if response.status_code >= 400
            else (response, None)
        )

    def _path(self, connector, project, suffix):
        project = quote(self._project(connector, project), safe="")
        return f"/{project}/_apis/pipelines{suffix}"

    async def test_connection(self, connector, auth):
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            self._path(connector, None, ""),
            params={"api-version": "7.1", "$top": 1},
        )
        return failure or IntegrationResult.success(
            detail=f"Azure Pipelines connection accepted ({response.status_code})."
        )

    async def list_pipelines(self, connector, auth, project=None, top=100):
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            self._path(connector, project, ""),
            params={"api-version": "7.1", "$top": min(max(int(top), 1), 100)},
        )
        return failure or IntegrationResult.success(
            pipelines=response.json().get("value", [])
        )

    async def get_pipeline(self, connector, auth, pipeline_id, project=None):
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            self._path(connector, project, f"/{int(pipeline_id)}"),
            params={"api-version": "7.1"},
        )
        return failure or IntegrationResult.success(pipeline=response.json())

    async def list_runs(self, connector, auth, pipeline_id, project=None, top=100):
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            self._path(connector, project, f"/{int(pipeline_id)}/runs"),
            params={"api-version": "7.1", "$top": min(max(int(top), 1), 100)},
        )
        return failure or IntegrationResult.success(
            runs=response.json().get("value", [])
        )

    async def get_run(self, connector, auth, pipeline_id, run_id, project=None):
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            self._path(
                connector,
                project,
                f"/{int(pipeline_id)}/runs/{int(run_id)}",
            ),
            params={"api-version": "7.1"},
        )
        return failure or IntegrationResult.success(run=response.json())

    async def run_pipeline(
        self,
        connector,
        auth,
        pipeline_id,
        project=None,
        branch=None,
        variables=None,
        template_parameters=None,
    ):
        payload: dict[str, Any] = {}
        if branch:
            payload["resources"] = {
                "repositories": {"self": {"refName": f"refs/heads/{branch}"}}
            }
        if variables:
            payload["variables"] = {
                str(key): {"value": str(value)} for key, value in variables.items()
            }
        if template_parameters:
            payload["templateParameters"] = template_parameters
        response, failure = await self._request(
            connector,
            auth,
            "POST",
            self._path(connector, project, f"/{int(pipeline_id)}/runs"),
            params={"api-version": "7.1"},
            json=payload,
        )
        return failure or IntegrationResult.success(run=response.json())


register_adapter(JenkinsAdapter())
register_adapter(CircleCIAdapter())
register_adapter(AzurePipelinesAdapter())
