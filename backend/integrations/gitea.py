"""Gitea repository, issue, and pull-request integration adapter."""

from __future__ import annotations

import base64
from typing import Any
from urllib.parse import urlparse

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
    path_value,
    required,
    response_error,
)
from backend.integrations.registry import register_adapter


class GiteaAdapter(IntegrationAdapter):
    kind = "gitea"
    capabilities = (
        IntegrationCapability("test_connection", "Validate Gitea access."),
        IntegrationCapability("get_repository", "Read repository metadata."),
        IntegrationCapability("get_file", "Read a repository file."),
        IntegrationCapability("list_issues", "List repository issues."),
        IntegrationCapability(
            "create_issue", "Create a repository issue.", "caution", True
        ),
        IntegrationCapability(
            "comment_issue", "Comment on an issue or pull request.", "caution", True
        ),
        IntegrationCapability("list_pull_requests", "List pull requests."),
        IntegrationCapability(
            "create_pull_request", "Create a pull request.", "caution", True
        ),
        IntegrationCapability(
            "merge_pull_request",
            "Merge a pull request.",
            "destructive",
            True,
            True,
        ),
        IntegrationCapability(
            "link_commit_to_incident",
            "Link a commit to an incident.",
            "caution",
            True,
        ),
        IntegrationCapability(
            "link_pull_request_to_incident",
            "Link a pull request to an incident.",
            "caution",
            True,
        ),
    )

    def __init__(self, *, http_client_factory: HttpClientFactory | None = None):
        self._factory = http_client_factory or default_http_client

    @staticmethod
    def _base(connector: IntegrationConnector) -> str:
        raw = required(connector.base_url, "base_url").rstrip("/")
        parsed = urlparse(raw)
        return raw + "/api/v1" if parsed.path in {"", "/"} else raw

    @staticmethod
    def _headers(auth: dict[str, Any]) -> dict[str, str]:
        token = required(auth.get("token") or auth.get("api_token"), "token")
        return {"Authorization": f"token {token}", "Accept": "application/json"}

    @staticmethod
    def _repo(connector, owner=None, repo=None) -> tuple[str, str]:
        return (
            required(owner or connector.config.get("owner"), "owner"),
            required(repo or connector.config.get("repo"), "repo"),
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
            (None, response_error("Gitea", response))
            if response.status_code >= 400
            else (response, None)
        )

    async def test_connection(self, connector, auth):
        response, failure = await self._request(connector, auth, "GET", "/user")
        return failure or IntegrationResult.success(
            detail=f"Gitea credentials accepted ({response.status_code})."
        )

    async def get_repository(
        self, connector, auth, owner: str | None = None, repo: str | None = None
    ):
        owner, repo = self._repo(connector, owner, repo)
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            f"/repos/{path_segment(owner, 'owner')}/{path_segment(repo, 'repo')}",
        )
        return failure or IntegrationResult.success(repository=response.json())

    async def get_file(
        self,
        connector,
        auth,
        path: str,
        owner: str | None = None,
        repo: str | None = None,
        ref: str | None = None,
    ):
        owner, repo = self._repo(connector, owner, repo)
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            f"/repos/{path_segment(owner, 'owner')}/{path_segment(repo, 'repo')}/contents/"
            f"{path_value(path, 'path')}",
            params={"ref": ref} if ref else None,
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
                "path": data.get("path"),
                "sha": data.get("sha"),
                "size": data.get("size"),
                "html_url": data.get("html_url"),
                "content": decoded,
            }
        )

    async def list_issues(
        self,
        connector,
        auth,
        owner: str | None = None,
        repo: str | None = None,
        state: str = "open",
    ):
        owner, repo = self._repo(connector, owner, repo)
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            f"/repos/{path_segment(owner, 'owner')}/{path_segment(repo, 'repo')}/issues",
            params={"state": state, "limit": 100},
        )
        return failure or IntegrationResult.success(issues=response.json())

    async def create_issue(
        self,
        connector,
        auth,
        title: str,
        owner: str | None = None,
        repo: str | None = None,
        body: str | None = None,
        labels: list[int] | None = None,
    ):
        owner, repo = self._repo(connector, owner, repo)
        payload: dict[str, Any] = {"title": required(title, "title")}
        if body is not None:
            payload["body"] = body
        if labels:
            payload["labels"] = labels
        response, failure = await self._request(
            connector,
            auth,
            "POST",
            f"/repos/{path_segment(owner, 'owner')}/{path_segment(repo, 'repo')}/issues",
            json=payload,
        )
        return failure or IntegrationResult.success(issue=response.json())

    async def comment_issue(
        self,
        connector,
        auth,
        issue_number: int,
        body: str,
        owner: str | None = None,
        repo: str | None = None,
    ):
        owner, repo = self._repo(connector, owner, repo)
        response, failure = await self._request(
            connector,
            auth,
            "POST",
            f"/repos/{path_segment(owner, 'owner')}/{path_segment(repo, 'repo')}/issues/{int(issue_number)}/comments",
            json={"body": required(body, "body")},
        )
        return failure or IntegrationResult.success(comment=response.json())

    async def list_pull_requests(
        self,
        connector,
        auth,
        owner: str | None = None,
        repo: str | None = None,
        state: str = "open",
    ):
        owner, repo = self._repo(connector, owner, repo)
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            f"/repos/{path_segment(owner, 'owner')}/{path_segment(repo, 'repo')}/pulls",
            params={"state": state, "limit": 100},
        )
        return failure or IntegrationResult.success(pull_requests=response.json())

    async def create_pull_request(
        self,
        connector,
        auth,
        title: str,
        head: str,
        base: str,
        owner: str | None = None,
        repo: str | None = None,
        body: str | None = None,
    ):
        owner, repo = self._repo(connector, owner, repo)
        response, failure = await self._request(
            connector,
            auth,
            "POST",
            f"/repos/{path_segment(owner, 'owner')}/{path_segment(repo, 'repo')}/pulls",
            json={
                "title": required(title, "title"),
                "head": required(head, "head"),
                "base": required(base, "base"),
                "body": body,
            },
        )
        return failure or IntegrationResult.success(pull_request=response.json())

    async def merge_pull_request(
        self,
        connector,
        auth,
        pull_number: int,
        owner: str | None = None,
        repo: str | None = None,
        merge_style: str = "merge",
        title: str | None = None,
        message: str | None = None,
    ):
        owner, repo = self._repo(connector, owner, repo)
        payload: dict[str, Any] = {"Do": merge_style}
        if title:
            payload["MergeTitleField"] = title
        if message:
            payload["MergeMessageField"] = message
        response, failure = await self._request(
            connector,
            auth,
            "POST",
            f"/repos/{path_segment(owner, 'owner')}/{path_segment(repo, 'repo')}/pulls/{int(pull_number)}/merge",
            json=payload,
        )
        return failure or IntegrationResult.success(
            merged=True, status_code=response.status_code
        )

    async def link_commit_to_incident(
        self,
        connector,
        auth,
        incident_id: str,
        sha: str,
        owner: str | None = None,
        repo: str | None = None,
    ):
        owner, repo = self._repo(connector, owner, repo)
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            f"/repos/{path_segment(owner, 'owner')}/{path_segment(repo, 'repo')}/git/commits/"
            f"{path_segment(sha, 'sha')}",
        )
        if failure:
            return failure
        data = response.json()
        message = str(data.get("message") or sha).splitlines()[0]
        return IntegrationResult.success(
            integration_link={
                "incident_id": required(incident_id, "incident_id"),
                "reference_type": "commit",
                "external_id": data.get("sha") or sha,
                "url": required(data.get("html_url"), "commit html_url"),
                "title": message,
                "reference_meta": {"owner": owner, "repo": repo},
            }
        )

    async def link_pull_request_to_incident(
        self,
        connector,
        auth,
        incident_id: str,
        pull_number: int,
        owner: str | None = None,
        repo: str | None = None,
    ):
        owner, repo = self._repo(connector, owner, repo)
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            f"/repos/{path_segment(owner, 'owner')}/{path_segment(repo, 'repo')}/pulls/{int(pull_number)}",
        )
        if failure:
            return failure
        data = response.json()
        return IntegrationResult.success(
            integration_link={
                "incident_id": required(incident_id, "incident_id"),
                "reference_type": "pull_request",
                "external_id": str(data.get("number") or pull_number),
                "url": required(data.get("html_url"), "pull request html_url"),
                "title": data.get("title"),
                "reference_meta": {"owner": owner, "repo": repo},
            }
        )


register_adapter(GiteaAdapter())
