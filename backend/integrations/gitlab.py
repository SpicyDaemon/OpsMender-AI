"""GitLab REST adapter for repository, issue, and merge-request workflows."""

from __future__ import annotations

import base64
from typing import Any, Callable
from urllib.parse import quote, urlparse

import httpx

from backend.db.models import IntegrationConnector
from backend.integrations.base import (
    IntegrationAdapter,
    IntegrationCapability,
    IntegrationResult,
)
from backend.integrations.registry import register_adapter


def _required(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} is required")
    return text


class GitLabAdapter(IntegrationAdapter):
    kind = "gitlab"
    capabilities = (
        IntegrationCapability(
            "test_connection",
            "Validate GitLab credentials and API reachability.",
        ),
        IntegrationCapability(
            "get_project",
            "Read project metadata. Parameter: project.",
        ),
        IntegrationCapability(
            "get_file",
            "Read a repository file. Parameters: project, path, optional ref.",
        ),
        IntegrationCapability(
            "list_issues",
            "List project issues. Parameters: project, optional state.",
        ),
        IntegrationCapability(
            "create_issue",
            "Create an issue. Parameters: project, title, optional description and labels.",
            classification="caution",
            mutating=True,
        ),
        IntegrationCapability(
            "comment_issue",
            "Comment on an issue. Parameters: project, issue_iid, body.",
            classification="caution",
            mutating=True,
        ),
        IntegrationCapability(
            "create_merge_request",
            "Create a merge request. Parameters: project, title, source_branch, target_branch, optional description.",
            classification="caution",
            mutating=True,
        ),
        IntegrationCapability(
            "merge_merge_request",
            "Merge a merge request. Parameters: project, merge_request_iid, optional sha.",
            classification="destructive",
            mutating=True,
            always_requires_approval=True,
        ),
        IntegrationCapability(
            "link_commit_to_incident",
            "Link a commit to an OpsMender incident. Parameters: incident_id, project, sha.",
            classification="caution",
            mutating=True,
        ),
        IntegrationCapability(
            "link_merge_request_to_incident",
            "Link a merge request to an OpsMender incident. Parameters: incident_id, project, merge_request_iid.",
            classification="caution",
            mutating=True,
        ),
    )

    def __init__(
        self,
        *,
        http_client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self._http_client_factory = http_client_factory or (
            lambda: httpx.AsyncClient(timeout=20, follow_redirects=True)
        )

    @staticmethod
    def _base_url(connector: IntegrationConnector) -> str:
        raw = (connector.base_url or "https://gitlab.com/api/v4").rstrip("/")
        parsed = urlparse(raw)
        if parsed.path in {"", "/"}:
            return raw + "/api/v4"
        return raw

    @staticmethod
    def _project(connector: IntegrationConnector, project: str | None) -> str:
        return _required(project or connector.config.get("project"), "project")

    @staticmethod
    def _headers(
        connector: IntegrationConnector, auth: dict[str, Any]
    ) -> dict[str, str]:
        token = _required(auth.get("token") or auth.get("access_token"), "token")
        if connector.auth_type == "oauth":
            return {"Authorization": f"Bearer {token}"}
        return {"PRIVATE-TOKEN": token}

    @staticmethod
    def _error(response: httpx.Response) -> str:
        try:
            body = response.json()
            detail = (
                body.get("message") or body.get("error_description")
                if isinstance(body, dict)
                else None
            )
        except ValueError:
            detail = None
        return f"GitLab HTTP {response.status_code}" + (f": {detail}" if detail else "")

    async def _request(
        self,
        connector: IntegrationConnector,
        auth: dict[str, Any],
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> tuple[httpx.Response | None, IntegrationResult | None]:
        async with self._http_client_factory() as client:
            response = await client.request(
                method,
                f"{self._base_url(connector)}{path}",
                params=params,
                json=json_body,
                headers=self._headers(connector, auth),
            )
        if response.status_code >= 400:
            return None, IntegrationResult.failure(self._error(response))
        return response, None

    async def test_connection(self, connector, auth):
        response, failure = await self._request(connector, auth, "GET", "/user")
        if failure:
            return failure
        data = response.json()
        return IntegrationResult.success(
            detail=(
                "GitLab credentials accepted "
                f"({data.get('username') or data.get('name') or 'user'})."
            )
        )

    async def get_project(self, connector, auth, project=None):
        project = self._project(connector, project)
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            f"/projects/{quote(project, safe='')}",
        )
        if failure:
            return failure
        data = response.json()
        return IntegrationResult.success(
            project={
                "id": data.get("id"),
                "path_with_namespace": data.get("path_with_namespace"),
                "default_branch": data.get("default_branch"),
                "web_url": data.get("web_url"),
            }
        )

    async def get_file(self, connector, auth, path, project=None, ref="HEAD"):
        project = self._project(connector, project)
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            f"/projects/{quote(project, safe='')}/repository/files/"
            f"{quote(_required(path, 'path'), safe='')}",
            params={"ref": ref or "HEAD"},
        )
        if failure:
            return failure
        data = response.json()
        content = data.get("content")
        decoded = None
        if content and data.get("encoding") == "base64":
            decoded = base64.b64decode(content).decode("utf-8", errors="replace")
        return IntegrationResult.success(
            file={
                "path": data.get("file_path"),
                "blob_id": data.get("blob_id"),
                "commit_id": data.get("commit_id"),
                "size": data.get("size"),
                "content": decoded,
            }
        )

    async def list_issues(self, connector, auth, project=None, state="opened"):
        project = self._project(connector, project)
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            f"/projects/{quote(project, safe='')}/issues",
            params={"state": state, "per_page": 100},
        )
        if failure:
            return failure
        return IntegrationResult.success(
            issues=[
                {
                    "iid": item.get("iid"),
                    "title": item.get("title"),
                    "state": item.get("state"),
                    "web_url": item.get("web_url"),
                }
                for item in response.json()
            ]
        )

    async def create_issue(
        self,
        connector,
        auth,
        title,
        project=None,
        description=None,
        labels=None,
    ):
        project = self._project(connector, project)
        payload = {"title": _required(title, "title")}
        if description is not None:
            payload["description"] = description
        if labels:
            payload["labels"] = ",".join(labels) if isinstance(labels, list) else labels
        response, failure = await self._request(
            connector,
            auth,
            "POST",
            f"/projects/{quote(project, safe='')}/issues",
            json_body=payload,
        )
        if failure:
            return failure
        data = response.json()
        return IntegrationResult.success(
            issue={
                "iid": data.get("iid"),
                "title": data.get("title"),
                "web_url": data.get("web_url"),
            }
        )

    async def comment_issue(
        self,
        connector,
        auth,
        issue_iid,
        body,
        project=None,
    ):
        project = self._project(connector, project)
        response, failure = await self._request(
            connector,
            auth,
            "POST",
            f"/projects/{quote(project, safe='')}/issues/" f"{int(issue_iid)}/notes",
            json_body={"body": _required(body, "body")},
        )
        if failure:
            return failure
        data = response.json()
        return IntegrationResult.success(
            comment={"id": data.get("id"), "body": data.get("body")}
        )

    async def create_merge_request(
        self,
        connector,
        auth,
        title,
        source_branch,
        target_branch,
        project=None,
        description=None,
        remove_source_branch=False,
    ):
        project = self._project(connector, project)
        response, failure = await self._request(
            connector,
            auth,
            "POST",
            f"/projects/{quote(project, safe='')}/merge_requests",
            json_body={
                "title": _required(title, "title"),
                "source_branch": _required(source_branch, "source_branch"),
                "target_branch": _required(target_branch, "target_branch"),
                "description": description,
                "remove_source_branch": bool(remove_source_branch),
            },
        )
        if failure:
            return failure
        data = response.json()
        return IntegrationResult.success(
            merge_request={
                "iid": data.get("iid"),
                "title": data.get("title"),
                "web_url": data.get("web_url"),
            }
        )

    async def merge_merge_request(
        self,
        connector,
        auth,
        merge_request_iid,
        project=None,
        sha=None,
    ):
        project = self._project(connector, project)
        payload = {}
        if sha:
            payload["sha"] = sha
        response, failure = await self._request(
            connector,
            auth,
            "PUT",
            f"/projects/{quote(project, safe='')}/merge_requests/"
            f"{int(merge_request_iid)}/merge",
            json_body=payload,
        )
        if failure:
            return failure
        return IntegrationResult.success(merge=response.json())

    async def link_commit_to_incident(
        self,
        connector,
        auth,
        incident_id,
        sha,
        project=None,
    ):
        project = self._project(connector, project)
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            f"/projects/{quote(project, safe='')}/repository/commits/"
            f"{quote(_required(sha, 'sha'), safe='')}",
        )
        if failure:
            return failure
        data = response.json()
        return IntegrationResult.success(
            integration_link={
                "incident_id": _required(incident_id, "incident_id"),
                "reference_type": "commit",
                "external_id": data.get("id") or sha,
                "url": _required(data.get("web_url"), "commit web_url"),
                "title": data.get("title") or data.get("short_id"),
                "reference_meta": {"project": project},
            }
        )

    async def link_merge_request_to_incident(
        self,
        connector,
        auth,
        incident_id,
        merge_request_iid,
        project=None,
    ):
        project = self._project(connector, project)
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            f"/projects/{quote(project, safe='')}/merge_requests/"
            f"{int(merge_request_iid)}",
        )
        if failure:
            return failure
        data = response.json()
        return IntegrationResult.success(
            integration_link={
                "incident_id": _required(incident_id, "incident_id"),
                "reference_type": "merge_request",
                "external_id": str(data.get("iid") or merge_request_iid),
                "url": _required(data.get("web_url"), "merge request web_url"),
                "title": data.get("title"),
                "reference_meta": {"project": project},
            }
        )


register_adapter(GitLabAdapter())
