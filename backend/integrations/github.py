"""GitHub REST adapter for repository, issue, and pull-request workflows."""

from __future__ import annotations

import base64
import time
from typing import Any, Callable
from urllib.parse import quote, urlparse

import httpx
import jwt

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


class GitHubAdapter(IntegrationAdapter):
    kind = "github"
    capabilities = (
        IntegrationCapability(
            "test_connection",
            "Validate GitHub credentials and API reachability.",
        ),
        IntegrationCapability(
            "get_repository",
            "Read repository metadata. Parameters: owner, repo.",
        ),
        IntegrationCapability(
            "get_file",
            "Read a repository file. Parameters: owner, repo, path, optional ref.",
        ),
        IntegrationCapability(
            "list_issues",
            "List repository issues. Parameters: owner, repo, optional state.",
        ),
        IntegrationCapability(
            "create_issue",
            "Create an issue. Parameters: owner, repo, title, optional body and labels.",
            classification="caution",
            mutating=True,
        ),
        IntegrationCapability(
            "comment_issue",
            "Comment on an issue or pull request. Parameters: owner, repo, issue_number, body.",
            classification="caution",
            mutating=True,
        ),
        IntegrationCapability(
            "create_pull_request",
            "Create a pull request. Parameters: owner, repo, title, head, base, optional body and draft.",
            classification="caution",
            mutating=True,
        ),
        IntegrationCapability(
            "merge_pull_request",
            "Merge a pull request. Parameters: owner, repo, pull_number, optional sha and merge_method.",
            classification="destructive",
            mutating=True,
            always_requires_approval=True,
        ),
        IntegrationCapability(
            "link_commit_to_incident",
            "Link a commit to an OpsMender incident. Parameters: incident_id, owner, repo, sha.",
            classification="caution",
            mutating=True,
        ),
        IntegrationCapability(
            "link_pull_request_to_incident",
            "Link a pull request to an OpsMender incident. Parameters: incident_id, owner, repo, pull_number.",
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
        raw = (connector.base_url or "https://api.github.com").rstrip("/")
        parsed = urlparse(raw)
        if parsed.path in {"", "/"} and parsed.netloc != "api.github.com":
            return raw + "/api/v3"
        return raw

    @staticmethod
    def _repo(
        connector: IntegrationConnector,
        owner: str | None,
        repo: str | None,
    ) -> tuple[str, str]:
        return (
            _required(owner or connector.config.get("owner"), "owner"),
            _required(repo or connector.config.get("repo"), "repo"),
        )

    async def _token(
        self,
        client: httpx.AsyncClient,
        connector: IntegrationConnector,
        auth: dict[str, Any],
    ) -> str:
        if connector.auth_type == "pat":
            return _required(auth.get("token"), "token")
        existing = auth.get("installation_token")
        if existing:
            return str(existing)
        app_id = _required(auth.get("app_id"), "app_id")
        installation_id = _required(auth.get("installation_id"), "installation_id")
        private_key = _required(auth.get("private_key"), "private_key")
        now = int(time.time())
        app_jwt = jwt.encode(
            {"iat": now - 60, "exp": now + 540, "iss": app_id},
            private_key,
            algorithm="RS256",
        )
        response = await client.post(
            f"{self._base_url(connector)}/app/installations/"
            f"{quote(installation_id, safe='')}/access_tokens",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {app_jwt}",
                "X-GitHub-Api-Version": str(
                    connector.config.get("api_version") or "2022-11-28"
                ),
            },
        )
        if response.status_code >= 400:
            raise RuntimeError(self._error(response))
        return _required(response.json().get("token"), "installation token")

    @staticmethod
    def _error(response: httpx.Response) -> str:
        try:
            body = response.json()
            detail = body.get("message") if isinstance(body, dict) else None
        except ValueError:
            detail = None
        return f"GitHub HTTP {response.status_code}" + (f": {detail}" if detail else "")

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
            token = await self._token(client, connector, auth)
            response = await client.request(
                method,
                f"{self._base_url(connector)}{path}",
                params=params,
                json=json_body,
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {token}",
                    "X-GitHub-Api-Version": str(
                        connector.config.get("api_version") or "2022-11-28"
                    ),
                },
            )
        if response.status_code >= 400:
            return None, IntegrationResult.failure(self._error(response))
        return response, None

    async def test_connection(self, connector, auth):
        path = "/installation/repositories" if connector.auth_type == "app" else "/user"
        response, failure = await self._request(connector, auth, "GET", path)
        if failure:
            return failure
        data = response.json()
        identity = data.get("login") or data.get("total_count") or "installation"
        return IntegrationResult.success(
            detail=f"GitHub credentials accepted ({identity})."
        )

    async def get_repository(self, connector, auth, owner=None, repo=None):
        owner, repo = self._repo(connector, owner, repo)
        response, failure = await self._request(
            connector, auth, "GET", f"/repos/{quote(owner)}/{quote(repo)}"
        )
        if failure:
            return failure
        data = response.json()
        return IntegrationResult.success(
            repository={
                "full_name": data.get("full_name"),
                "default_branch": data.get("default_branch"),
                "private": data.get("private"),
                "html_url": data.get("html_url"),
            }
        )

    async def get_file(self, connector, auth, path, owner=None, repo=None, ref=None):
        owner, repo = self._repo(connector, owner, repo)
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            f"/repos/{quote(owner)}/{quote(repo)}/contents/"
            f"{quote(_required(path, 'path'), safe='/')}",
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

    async def list_issues(self, connector, auth, owner=None, repo=None, state="open"):
        owner, repo = self._repo(connector, owner, repo)
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            f"/repos/{quote(owner)}/{quote(repo)}/issues",
            params={"state": state, "per_page": 100},
        )
        if failure:
            return failure
        issues = [
            {
                "number": item.get("number"),
                "title": item.get("title"),
                "state": item.get("state"),
                "html_url": item.get("html_url"),
            }
            for item in response.json()
            if "pull_request" not in item
        ]
        return IntegrationResult.success(issues=issues)

    async def create_issue(
        self,
        connector,
        auth,
        title,
        owner=None,
        repo=None,
        body=None,
        labels=None,
    ):
        owner, repo = self._repo(connector, owner, repo)
        payload = {"title": _required(title, "title")}
        if body is not None:
            payload["body"] = body
        if labels:
            payload["labels"] = labels
        response, failure = await self._request(
            connector,
            auth,
            "POST",
            f"/repos/{quote(owner)}/{quote(repo)}/issues",
            json_body=payload,
        )
        if failure:
            return failure
        data = response.json()
        return IntegrationResult.success(
            issue={
                "number": data.get("number"),
                "title": data.get("title"),
                "html_url": data.get("html_url"),
            }
        )

    async def comment_issue(
        self,
        connector,
        auth,
        issue_number,
        body,
        owner=None,
        repo=None,
    ):
        owner, repo = self._repo(connector, owner, repo)
        response, failure = await self._request(
            connector,
            auth,
            "POST",
            f"/repos/{quote(owner)}/{quote(repo)}/issues/"
            f"{int(issue_number)}/comments",
            json_body={"body": _required(body, "body")},
        )
        if failure:
            return failure
        data = response.json()
        return IntegrationResult.success(
            comment={"id": data.get("id"), "html_url": data.get("html_url")}
        )

    async def create_pull_request(
        self,
        connector,
        auth,
        title,
        head,
        base,
        owner=None,
        repo=None,
        body=None,
        draft=False,
    ):
        owner, repo = self._repo(connector, owner, repo)
        response, failure = await self._request(
            connector,
            auth,
            "POST",
            f"/repos/{quote(owner)}/{quote(repo)}/pulls",
            json_body={
                "title": _required(title, "title"),
                "head": _required(head, "head"),
                "base": _required(base, "base"),
                "body": body,
                "draft": bool(draft),
            },
        )
        if failure:
            return failure
        data = response.json()
        return IntegrationResult.success(
            pull_request={
                "number": data.get("number"),
                "title": data.get("title"),
                "html_url": data.get("html_url"),
            }
        )

    async def merge_pull_request(
        self,
        connector,
        auth,
        pull_number,
        owner=None,
        repo=None,
        sha=None,
        merge_method="merge",
    ):
        owner, repo = self._repo(connector, owner, repo)
        payload = {"merge_method": merge_method}
        if sha:
            payload["sha"] = sha
        response, failure = await self._request(
            connector,
            auth,
            "PUT",
            f"/repos/{quote(owner)}/{quote(repo)}/pulls/" f"{int(pull_number)}/merge",
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
        owner=None,
        repo=None,
    ):
        owner, repo = self._repo(connector, owner, repo)
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            f"/repos/{quote(owner)}/{quote(repo)}/commits/"
            f"{quote(_required(sha, 'sha'), safe='')}",
        )
        if failure:
            return failure
        data = response.json()
        message = ((data.get("commit") or {}).get("message") or "").splitlines()
        return IntegrationResult.success(
            integration_link={
                "incident_id": _required(incident_id, "incident_id"),
                "reference_type": "commit",
                "external_id": data.get("sha") or sha,
                "url": _required(data.get("html_url"), "commit html_url"),
                "title": message[0] if message else str(sha),
                "reference_meta": {"owner": owner, "repo": repo},
            }
        )

    async def link_pull_request_to_incident(
        self,
        connector,
        auth,
        incident_id,
        pull_number,
        owner=None,
        repo=None,
    ):
        owner, repo = self._repo(connector, owner, repo)
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            f"/repos/{quote(owner)}/{quote(repo)}/pulls/{int(pull_number)}",
        )
        if failure:
            return failure
        data = response.json()
        return IntegrationResult.success(
            integration_link={
                "incident_id": _required(incident_id, "incident_id"),
                "reference_type": "pull_request",
                "external_id": str(data.get("number") or pull_number),
                "url": _required(data.get("html_url"), "pull request html_url"),
                "title": data.get("title"),
                "reference_meta": {"owner": owner, "repo": repo},
            }
        )


register_adapter(GitHubAdapter())
