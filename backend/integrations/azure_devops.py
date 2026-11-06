"""Azure DevOps Repos and Boards adapter."""

from __future__ import annotations

import base64
from typing import Any


from backend.db.models import IntegrationConnector
from backend.integrations.base import (
    IntegrationAdapter,
    IntegrationCapability,
    IntegrationResult,
)
from backend.integrations.http import (
    HttpClientFactory,
    default_http_client,
    path_segment,
    required,
    response_error,
)
from backend.integrations.registry import register_adapter


class AzureDevOpsAdapter(IntegrationAdapter):
    kind = "azure_devops"
    capabilities = (
        IntegrationCapability("test_connection", "Validate Azure DevOps access."),
        IntegrationCapability("get_repository", "Read repository metadata."),
        IntegrationCapability("get_file", "Read a repository file."),
        IntegrationCapability("list_pull_requests", "List pull requests."),
        IntegrationCapability(
            "create_pull_request",
            "Create a pull request.",
            classification="caution",
            mutating=True,
        ),
        IntegrationCapability(
            "merge_pull_request",
            "Complete a pull request.",
            classification="destructive",
            mutating=True,
            always_requires_approval=True,
        ),
        IntegrationCapability("get_work_item", "Read a Boards work item."),
        IntegrationCapability(
            "create_work_item",
            "Create a Boards work item.",
            classification="caution",
            mutating=True,
        ),
        IntegrationCapability(
            "update_work_item",
            "Update a Boards work item.",
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
        return f"https://dev.azure.com/{path_segment(organization, 'organization')}"

    @staticmethod
    def _headers(
        connector: IntegrationConnector,
        auth: dict[str, Any],
    ) -> dict[str, str]:
        token = required(auth.get("access_token") or auth.get("token"), "token")
        if connector.auth_type == "oauth":
            return {"Authorization": f"Bearer {token}"}
        encoded = base64.b64encode(f":{token}".encode()).decode()
        return {"Authorization": f"Basic {encoded}"}

    @staticmethod
    def _project(connector: IntegrationConnector, project: str | None) -> str:
        return required(project or connector.config.get("project"), "project")

    @staticmethod
    def _repo(connector: IntegrationConnector, repository: str | None) -> str:
        return required(repository or connector.config.get("repository"), "repository")

    async def _request(self, connector, auth, method, path, **kwargs):
        headers = self._headers(connector, auth)
        headers.update(kwargs.pop("headers", {}))
        async with self._factory() as client:
            response = await client.request(
                method,
                f"{self._base(connector)}{path}",
                headers=headers,
                **kwargs,
            )
        return (
            (None, response_error("Azure DevOps", response))
            if response.status_code >= 400
            else (response, None)
        )

    async def test_connection(self, connector, auth):
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            "/_apis/projects",
            params={"api-version": "7.1", "$top": 1},
        )
        return failure or IntegrationResult.success(
            detail=f"Azure DevOps connection accepted ({response.status_code})."
        )

    async def get_repository(
        self, connector, auth, project: str | None = None, repository: str | None = None
    ):
        project = self._project(connector, project)
        repository = self._repo(connector, repository)
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            f"/{path_segment(project, 'project')}/_apis/git/repositories/{path_segment(repository, 'repository')}",
            params={"api-version": "7.1"},
        )
        return failure or IntegrationResult.success(repository=response.json())

    async def get_file(
        self,
        connector,
        auth,
        path: str,
        project: str | None = None,
        repository: str | None = None,
        ref: str = "main",
    ):
        project = self._project(connector, project)
        repository = self._repo(connector, repository)
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            f"/{path_segment(project, 'project')}/_apis/git/repositories/{path_segment(repository, 'repository')}/items",
            params={
                "api-version": "7.1",
                "path": required(path, "path"),
                "includeContent": "true",
                "versionDescriptor.version": ref,
                "versionDescriptor.versionType": "branch",
            },
        )
        if failure:
            return failure
        content_type = response.headers.get("content-type", "")
        content = (
            response.json().get("content") if "json" in content_type else response.text
        )
        return IntegrationResult.success(
            file={"path": path, "ref": ref, "content": content}
        )

    async def list_pull_requests(
        self,
        connector,
        auth,
        project: str | None = None,
        repository: str | None = None,
        status: str = "active",
    ):
        project = self._project(connector, project)
        repository = self._repo(connector, repository)
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            f"/{path_segment(project, 'project')}/_apis/git/repositories/"
            f"{path_segment(repository, 'repository')}/pullrequests",
            params={
                "api-version": "7.1",
                "searchCriteria.status": status,
            },
        )
        return failure or IntegrationResult.success(
            pull_requests=response.json().get("value", [])
        )

    async def create_pull_request(
        self,
        connector,
        auth,
        title: str,
        source_branch: str,
        target_branch: str,
        project: str | None = None,
        repository: str | None = None,
        description: str | None = None,
    ):
        project = self._project(connector, project)
        repository = self._repo(connector, repository)
        response, failure = await self._request(
            connector,
            auth,
            "POST",
            f"/{path_segment(project, 'project')}/_apis/git/repositories/"
            f"{path_segment(repository, 'repository')}/pullrequests",
            params={"api-version": "7.1"},
            json={
                "title": required(title, "title"),
                "description": description or "",
                "sourceRefName": f"refs/heads/{required(source_branch, 'source_branch')}",
                "targetRefName": f"refs/heads/{required(target_branch, 'target_branch')}",
            },
        )
        return failure or IntegrationResult.success(pull_request=response.json())

    async def merge_pull_request(
        self,
        connector,
        auth,
        pull_request_id: int,
        project: str | None = None,
        repository: str | None = None,
        last_merge_source_commit: str | None = None,
        delete_source_branch: bool = False,
        squash: bool = False,
    ):
        project = self._project(connector, project)
        repository = self._repo(connector, repository)
        payload: dict[str, Any] = {
            "status": "completed",
            "completionOptions": {
                "deleteSourceBranch": bool(delete_source_branch),
                "mergeStrategy": "squash" if squash else "noFastForward",
            },
        }
        if last_merge_source_commit:
            payload["lastMergeSourceCommit"] = {"commitId": last_merge_source_commit}
        response, failure = await self._request(
            connector,
            auth,
            "PATCH",
            f"/{path_segment(project, 'project')}/_apis/git/repositories/"
            f"{path_segment(repository, 'repository')}/pullrequests/{int(pull_request_id)}",
            params={"api-version": "7.1"},
            json=payload,
        )
        return failure or IntegrationResult.success(merge=response.json())

    async def get_work_item(
        self, connector, auth, work_item_id: int, project: str | None = None
    ):
        project = self._project(connector, project)
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            f"/{path_segment(project, 'project')}/_apis/wit/workitems/{int(work_item_id)}",
            params={"api-version": "7.1", "$expand": "relations"},
        )
        return failure or IntegrationResult.success(work_item=response.json())

    @staticmethod
    def _patch(fields: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                "op": "add",
                "path": f"/fields/{name}",
                "value": value,
            }
            for name, value in fields.items()
        ]

    async def create_work_item(
        self,
        connector,
        auth,
        title: str,
        project: str | None = None,
        work_item_type: str = "Task",
        description: str | None = None,
        fields: dict[str, Any] | None = None,
    ):
        project = self._project(connector, project)
        values = dict(fields or {})
        values["System.Title"] = required(title, "title")
        if description is not None:
            values["System.Description"] = description
        response, failure = await self._request(
            connector,
            auth,
            "POST",
            f"/{path_segment(project, 'project')}/_apis/wit/workitems/"
            f"${path_segment(work_item_type, 'work_item_type')}",
            params={"api-version": "7.1"},
            headers={"Content-Type": "application/json-patch+json"},
            json=self._patch(values),
        )
        return failure or IntegrationResult.success(work_item=response.json())

    async def update_work_item(
        self,
        connector,
        auth,
        work_item_id: int,
        fields: dict[str, Any],
        project: str | None = None,
    ):
        project = self._project(connector, project)
        if not isinstance(fields, dict) or not fields:
            raise ValueError("fields is required")
        response, failure = await self._request(
            connector,
            auth,
            "PATCH",
            f"/{path_segment(project, 'project')}/_apis/wit/workitems/{int(work_item_id)}",
            params={"api-version": "7.1"},
            headers={"Content-Type": "application/json-patch+json"},
            json=self._patch(fields),
        )
        return failure or IntegrationResult.success(work_item=response.json())


register_adapter(AzureDevOpsAdapter())
