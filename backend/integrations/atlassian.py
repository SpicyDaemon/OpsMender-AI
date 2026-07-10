"""Bitbucket, Jira, and Confluence native adapters."""

from __future__ import annotations

import base64
from typing import Any
from urllib.parse import quote, urlparse


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


def _basic(username: str, password: str) -> str:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {token}"


def _atlassian_headers(
    connector: IntegrationConnector,
    auth: dict[str, Any],
) -> dict[str, str]:
    if connector.auth_type == "oauth":
        return {
            "Authorization": f"Bearer {required(auth.get('access_token'), 'access_token')}"
        }
    if connector.auth_type == "basic":
        return {
            "Authorization": _basic(
                required(auth.get("username") or auth.get("email"), "username"),
                required(auth.get("password") or auth.get("api_token"), "password"),
            )
        }
    token = required(auth.get("token") or auth.get("api_token"), "token")
    if connector.config.get("edition") in {"server", "data_center", "on_prem"}:
        return {"Authorization": f"Bearer {token}"}
    email = auth.get("email")
    return (
        {"Authorization": _basic(str(email), token)}
        if email
        else {"Authorization": f"Bearer {token}"}
    )


class BitbucketAdapter(IntegrationAdapter):
    kind = "bitbucket"
    capabilities = (
        IntegrationCapability("test_connection", "Validate Bitbucket access."),
        IntegrationCapability("get_repository", "Read repository metadata."),
        IntegrationCapability("get_file", "Read a repository file."),
        IntegrationCapability("list_issues", "List repository issues."),
        IntegrationCapability(
            "create_issue", "Create a repository issue.", "caution", True
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
    )

    def __init__(self, *, http_client_factory: HttpClientFactory | None = None):
        self._factory = http_client_factory or default_http_client

    @staticmethod
    def _edition(connector: IntegrationConnector) -> str:
        return str(connector.config.get("edition") or "cloud")

    @classmethod
    def _base(cls, connector: IntegrationConnector) -> str:
        raw = (connector.base_url or "https://api.bitbucket.org/2.0").rstrip("/")
        if cls._edition(connector) == "cloud":
            return raw
        parsed = urlparse(raw)
        return raw + "/rest/api/1.0" if parsed.path in {"", "/"} else raw

    @staticmethod
    def _scope(connector, workspace=None, project=None, repo=None):
        repo = required(repo or connector.config.get("repo"), "repo")
        if BitbucketAdapter._edition(connector) == "cloud":
            return (
                required(workspace or connector.config.get("workspace"), "workspace"),
                repo,
            )
        return required(project or connector.config.get("project"), "project"), repo

    async def _request(self, connector, auth, method, path, **kwargs):
        async with self._factory() as client:
            response = await client.request(
                method,
                f"{self._base(connector)}{path}",
                headers=_atlassian_headers(connector, auth),
                **kwargs,
            )
        return (
            (None, response_error("Bitbucket", response))
            if response.status_code >= 400
            else (response, None)
        )

    async def test_connection(self, connector, auth):
        if self._edition(connector) == "cloud":
            path = "/user"
        else:
            path = "/application-properties"
        response, failure = await self._request(connector, auth, "GET", path)
        return failure or IntegrationResult.success(
            detail=f"Bitbucket connection accepted ({response.status_code})."
        )

    async def get_repository(
        self, connector, auth, workspace=None, project=None, repo=None
    ):
        scope, repo = self._scope(connector, workspace, project, repo)
        path = (
            f"/repositories/{quote(scope)}/{quote(repo)}"
            if self._edition(connector) == "cloud"
            else f"/projects/{quote(scope)}/repos/{quote(repo)}"
        )
        response, failure = await self._request(connector, auth, "GET", path)
        return failure or IntegrationResult.success(repository=response.json())

    async def get_file(
        self,
        connector,
        auth,
        path,
        workspace=None,
        project=None,
        repo=None,
        ref="main",
    ):
        scope, repo = self._scope(connector, workspace, project, repo)
        file_path = quote(required(path, "path"), safe="/")
        endpoint = (
            f"/repositories/{quote(scope)}/{quote(repo)}/src/{quote(ref)}/{file_path}"
            if self._edition(connector) == "cloud"
            else f"/projects/{quote(scope)}/repos/{quote(repo)}/raw/{file_path}"
        )
        params = None if self._edition(connector) == "cloud" else {"at": ref}
        response, failure = await self._request(
            connector, auth, "GET", endpoint, params=params
        )
        return failure or IntegrationResult.success(
            file={"path": path, "ref": ref, "content": response.text}
        )

    async def list_issues(self, connector, auth, workspace=None, repo=None):
        if self._edition(connector) != "cloud":
            return IntegrationResult.failure(
                "Bitbucket Data Center does not provide the Cloud issue tracker API"
            )
        workspace, repo = self._scope(connector, workspace, None, repo)
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            f"/repositories/{quote(workspace)}/{quote(repo)}/issues",
        )
        return failure or IntegrationResult.success(
            issues=response.json().get("values", [])
        )

    async def create_issue(
        self, connector, auth, title, workspace=None, repo=None, content=None
    ):
        if self._edition(connector) != "cloud":
            return IntegrationResult.failure(
                "Bitbucket Data Center does not provide the Cloud issue tracker API"
            )
        workspace, repo = self._scope(connector, workspace, None, repo)
        response, failure = await self._request(
            connector,
            auth,
            "POST",
            f"/repositories/{quote(workspace)}/{quote(repo)}/issues",
            json={
                "title": required(title, "title"),
                "content": {"raw": content or ""},
            },
        )
        return failure or IntegrationResult.success(issue=response.json())

    async def list_pull_requests(
        self, connector, auth, workspace=None, project=None, repo=None
    ):
        scope, repo = self._scope(connector, workspace, project, repo)
        path = (
            f"/repositories/{quote(scope)}/{quote(repo)}/pullrequests"
            if self._edition(connector) == "cloud"
            else f"/projects/{quote(scope)}/repos/{quote(repo)}/pull-requests"
        )
        response, failure = await self._request(connector, auth, "GET", path)
        data = response.json() if response else {}
        return failure or IntegrationResult.success(
            pull_requests=data.get("values", data)
        )

    async def create_pull_request(
        self,
        connector,
        auth,
        title,
        source_branch,
        target_branch,
        workspace=None,
        project=None,
        repo=None,
        description=None,
    ):
        scope, repo = self._scope(connector, workspace, project, repo)
        cloud = self._edition(connector) == "cloud"
        path = (
            f"/repositories/{quote(scope)}/{quote(repo)}/pullrequests"
            if cloud
            else f"/projects/{quote(scope)}/repos/{quote(repo)}/pull-requests"
        )
        payload = (
            {
                "title": required(title, "title"),
                "description": description or "",
                "source": {
                    "branch": {"name": required(source_branch, "source_branch")}
                },
                "destination": {
                    "branch": {"name": required(target_branch, "target_branch")}
                },
            }
            if cloud
            else {
                "title": required(title, "title"),
                "description": description or "",
                "fromRef": {
                    "id": f"refs/heads/{required(source_branch, 'source_branch')}"
                },
                "toRef": {
                    "id": f"refs/heads/{required(target_branch, 'target_branch')}"
                },
            }
        )
        response, failure = await self._request(
            connector, auth, "POST", path, json=payload
        )
        return failure or IntegrationResult.success(pull_request=response.json())

    async def merge_pull_request(
        self,
        connector,
        auth,
        pull_request_id,
        workspace=None,
        project=None,
        repo=None,
    ):
        scope, repo = self._scope(connector, workspace, project, repo)
        path = (
            f"/repositories/{quote(scope)}/{quote(repo)}/pullrequests/"
            f"{int(pull_request_id)}/merge"
            if self._edition(connector) == "cloud"
            else f"/projects/{quote(scope)}/repos/{quote(repo)}/pull-requests/"
            f"{int(pull_request_id)}/merge"
        )
        response, failure = await self._request(connector, auth, "POST", path)
        return failure or IntegrationResult.success(merge=response.json())


def _adf(text: str) -> dict[str, Any]:
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": text}],
            }
        ],
    }


class JiraAdapter(IntegrationAdapter):
    kind = "jira"
    capabilities = (
        IntegrationCapability("test_connection", "Validate Jira access."),
        IntegrationCapability("get_issue", "Read an issue."),
        IntegrationCapability("create_issue", "Create an issue.", "caution", True),
        IntegrationCapability("comment_issue", "Comment on an issue.", "caution", True),
        IntegrationCapability("list_transitions", "List issue transitions."),
        IntegrationCapability(
            "transition_issue", "Transition an issue.", "caution", True
        ),
    )

    def __init__(self, *, http_client_factory: HttpClientFactory | None = None):
        self._factory = http_client_factory or default_http_client

    @staticmethod
    def _base(connector):
        root = required(connector.base_url, "base_url").rstrip("/")
        edition = str(connector.config.get("edition") or "cloud")
        version = str(
            connector.config.get("api_version") or ("3" if edition == "cloud" else "2")
        )
        return root if "/rest/api/" in root else f"{root}/rest/api/{version}"

    async def _request(self, connector, auth, method, path, **kwargs):
        async with self._factory() as client:
            response = await client.request(
                method,
                f"{self._base(connector)}{path}",
                headers=_atlassian_headers(connector, auth),
                **kwargs,
            )
        return (
            (None, response_error("Jira", response))
            if response.status_code >= 400
            else (response, None)
        )

    def _cloud(self, connector):
        return str(connector.config.get("edition") or "cloud") == "cloud"

    async def test_connection(self, connector, auth):
        response, failure = await self._request(connector, auth, "GET", "/myself")
        return failure or IntegrationResult.success(
            detail=f"Jira credentials accepted ({response.json().get('displayName', 'user')})."
        )

    async def get_issue(self, connector, auth, issue_key):
        response, failure = await self._request(
            connector, auth, "GET", f"/issue/{quote(required(issue_key, 'issue_key'))}"
        )
        return failure or IntegrationResult.success(issue=response.json())

    async def create_issue(
        self,
        connector,
        auth,
        summary,
        project_key=None,
        issue_type=None,
        description=None,
        incident_id=None,
    ):
        fields = {
            "project": {
                "key": required(
                    project_key or connector.config.get("project_key"),
                    "project_key",
                )
            },
            "summary": required(summary, "summary"),
            "issuetype": {
                "name": issue_type or connector.config.get("issue_type") or "Task"
            },
        }
        if description:
            fields["description"] = (
                _adf(description) if self._cloud(connector) else description
            )
        response, failure = await self._request(
            connector, auth, "POST", "/issue", json={"fields": fields}
        )
        if failure:
            return failure
        issue = response.json()
        data: dict[str, Any] = {"issue": issue}
        issue_key = issue.get("key") or issue.get("id")
        if incident_id and issue_key:
            root = required(connector.base_url, "base_url").rstrip("/")
            root = root.split("/rest/api/", 1)[0]
            url = f"{root}/browse/{quote(str(issue_key))}"
            data["integration_link"] = {
                "incident_id": incident_id,
                "reference_type": "ticket",
                "external_id": str(issue_key),
                "url": url,
                "title": summary,
            }
            data["ticket_sync"] = {
                "incident_id": incident_id,
                "external_ticket_id": str(issue_key),
                "external_ticket_url": url,
            }
        return IntegrationResult.success(**data)

    async def comment_issue(self, connector, auth, issue_key, body):
        value = _adf(required(body, "body")) if self._cloud(connector) else body
        response, failure = await self._request(
            connector,
            auth,
            "POST",
            f"/issue/{quote(required(issue_key, 'issue_key'))}/comment",
            json={"body": value},
        )
        return failure or IntegrationResult.success(comment=response.json())

    async def list_transitions(self, connector, auth, issue_key):
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            f"/issue/{quote(required(issue_key, 'issue_key'))}/transitions",
        )
        return failure or IntegrationResult.success(
            transitions=response.json().get("transitions", [])
        )

    async def transition_issue(self, connector, auth, issue_key, transition_id):
        response, failure = await self._request(
            connector,
            auth,
            "POST",
            f"/issue/{quote(required(issue_key, 'issue_key'))}/transitions",
            json={"transition": {"id": str(transition_id)}},
        )
        return failure or IntegrationResult.success(
            transitioned=True, status_code=response.status_code
        )

    async def sync_status_out(
        self,
        connector,
        auth,
        ticket_id,
        new_status,
    ):
        transitions = await self.list_transitions(
            connector,
            auth,
            issue_key=ticket_id,
        )
        if not transitions.ok:
            return transitions
        target = str(new_status).strip().casefold()
        match = next(
            (
                item
                for item in transitions.data.get("transitions", [])
                if str(item.get("id", "")).casefold() == target
                or str(item.get("name", "")).casefold() == target
                or str((item.get("to") or {}).get("name", "")).casefold() == target
            ),
            None,
        )
        if match is None:
            return IntegrationResult.failure(
                f"No Jira transition matches '{new_status}'"
            )
        return await self.transition_issue(
            connector,
            auth,
            issue_key=ticket_id,
            transition_id=match["id"],
        )


class ConfluenceAdapter(IntegrationAdapter):
    kind = "confluence"
    capabilities = (
        IntegrationCapability("test_connection", "Validate Confluence access."),
        IntegrationCapability("read_doc", "Read a runbook or page."),
        IntegrationCapability(
            "write_doc", "Create or update a postmortem page.", "caution", True
        ),
    )

    def __init__(self, *, http_client_factory: HttpClientFactory | None = None):
        self._factory = http_client_factory or default_http_client

    @staticmethod
    def _cloud(connector):
        return str(connector.config.get("edition") or "cloud") == "cloud"

    @classmethod
    def _base(cls, connector):
        root = required(connector.base_url, "base_url").rstrip("/")
        if cls._cloud(connector):
            if root.endswith("/wiki/api/v2"):
                return root
            return root + ("/api/v2" if root.endswith("/wiki") else "/wiki/api/v2")
        return root if root.endswith("/rest/api") else root + "/rest/api"

    async def _request(self, connector, auth, method, path, **kwargs):
        async with self._factory() as client:
            response = await client.request(
                method,
                f"{self._base(connector)}{path}",
                headers=_atlassian_headers(connector, auth),
                **kwargs,
            )
        return (
            (None, response_error("Confluence", response))
            if response.status_code >= 400
            else (response, None)
        )

    async def test_connection(self, connector, auth):
        path = "/spaces" if self._cloud(connector) else "/space"
        response, failure = await self._request(
            connector, auth, "GET", path, params={"limit": 1}
        )
        return failure or IntegrationResult.success(
            detail=f"Confluence connection accepted ({response.status_code})."
        )

    async def read_doc(self, connector, auth, page_id):
        if self._cloud(connector):
            path = f"/pages/{quote(required(page_id, 'page_id'))}"
            params = {"body-format": "storage"}
        else:
            path = f"/content/{quote(required(page_id, 'page_id'))}"
            params = {"expand": "body.storage,version"}
        response, failure = await self._request(
            connector, auth, "GET", path, params=params
        )
        return failure or IntegrationResult.success(page=response.json())

    async def write_doc(
        self,
        connector,
        auth,
        title,
        content,
        space_id=None,
        page_id=None,
        parent_id=None,
        version=None,
    ):
        space = required(space_id or connector.config.get("space_id"), "space_id")
        if self._cloud(connector):
            payload = {
                "spaceId": space,
                "status": "current",
                "title": required(title, "title"),
                "body": {"representation": "storage", "value": content},
            }
            if parent_id:
                payload["parentId"] = parent_id
            method, path = (
                ("PUT", f"/pages/{quote(str(page_id))}")
                if page_id
                else ("POST", "/pages")
            )
            if page_id:
                payload["id"] = str(page_id)
                payload["version"] = {"number": int(version or 1) + 1}
        else:
            payload = {
                "type": "page",
                "title": required(title, "title"),
                "space": {"key": space},
                "body": {
                    "storage": {
                        "value": content,
                        "representation": "storage",
                    }
                },
            }
            method, path = (
                ("PUT", f"/content/{quote(str(page_id))}")
                if page_id
                else ("POST", "/content")
            )
            if page_id:
                payload["id"] = str(page_id)
                payload["version"] = {"number": int(version or 1) + 1}
        response, failure = await self._request(
            connector, auth, method, path, json=payload
        )
        return failure or IntegrationResult.success(page=response.json())


register_adapter(BitbucketAdapter())
register_adapter(JiraAdapter())
register_adapter(ConfluenceAdapter())
