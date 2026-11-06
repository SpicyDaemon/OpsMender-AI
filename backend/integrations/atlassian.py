"""Bitbucket, Jira, and Confluence native adapters."""

from __future__ import annotations

import base64
import re
from typing import Any
from urllib.parse import quote as url_quote
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
    numeric_id,
    path_segment,
    path_value,
    required,
    response_error,
)
from backend.integrations.registry import register_adapter


# A Jira issue is named by its key (OPS-12) or its numeric id; any other word
# in that place is a sibling route of the issue API (createmeta, picker,
# bulk), which a read tool must not reach (M1-147).
_ISSUE_KEY = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,254}-[0-9]+|[0-9]+")
# The REST API versions Jira serves.
_JIRA_API_VERSION = re.compile(r"[0-9]{1,2}|latest")


def _issue_key(value: Any) -> str:
    text = path_segment(value, "issue_key")
    if not _ISSUE_KEY.fullmatch(text):
        shown = str(value).strip()
        shown = shown if len(shown) <= 80 else shown[:80] + "..."
        raise ValueError(
            f"issue_key must be an issue key such as 'OPS-12' or a numeric "
            f"issue id, not {shown!r}."
        )
    return text


def _basic(username: str, password: str) -> str:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {token}"


_CLOUD_EMAIL_NEEDED = (
    "An Atlassian Cloud API token needs the email of the account it belongs "
    "to. Fill in the email field; leave it blank only for a Server or Data "
    "Center personal access token, with the edition set to match."
)


def _needs_account_email(connector: IntegrationConnector) -> bool:
    """A Jira or Confluence connector on an Atlassian Cloud site.

    Judged by the site's host, not by the edition setting: the form saves
    "cloud" as the edition unless it is changed, so the setting says little,
    and a Data Center site saved that way must keep its bearer token.
    Bitbucket shares these headers and its Cloud access tokens are bearer
    tokens with no account email.
    """
    if connector.kind not in {"jira", "confluence"}:
        return False
    host = (urlparse(str(connector.base_url or "")).hostname or "").lower()
    return host.endswith((".atlassian.net", ".jira.com"))


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
    if email:
        return {"Authorization": _basic(str(email), token)}
    if _needs_account_email(connector):
        # A Cloud API token only works as the password of its account. Sent
        # as a bearer token it draws a bare 403, which reads like a missing
        # permission (M1-140).
        raise ValueError(_CLOUD_EMAIL_NEEDED)
    return {"Authorization": f"Bearer {token}"}


class BitbucketAdapter(IntegrationAdapter):
    kind = "bitbucket"

    def argument_alternatives(self, connector, action):
        # Cloud addresses a repository by workspace, Data Center by project.
        cloud = self._edition(connector) == "cloud"
        return frozenset({"project" if cloud else "workspace"})

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
        self,
        connector,
        auth,
        workspace: str | None = None,
        project: str | None = None,
        repo: str | None = None,
    ):
        scope, repo = self._scope(connector, workspace, project, repo)
        path = (
            f"/repositories/{path_segment(scope, 'workspace')}/{path_segment(repo, 'repo')}"
            if self._edition(connector) == "cloud"
            else f"/projects/{path_segment(scope, 'project')}/repos/{path_segment(repo, 'repo')}"
        )
        response, failure = await self._request(connector, auth, "GET", path)
        return failure or IntegrationResult.success(repository=response.json())

    async def get_file(
        self,
        connector,
        auth,
        path: str,
        workspace: str | None = None,
        project: str | None = None,
        repo: str | None = None,
        ref: str = "main",
    ):
        scope, repo = self._scope(connector, workspace, project, repo)
        file_path = path_value(path, "path")
        endpoint = (
            f"/repositories/{path_segment(scope, 'workspace')}/{path_segment(repo, 'repo')}/src/{path_value(ref, 'ref')}/{file_path}"
            if self._edition(connector) == "cloud"
            else f"/projects/{path_segment(scope, 'project')}/repos/{path_segment(repo, 'repo')}/raw/{file_path}"
        )
        params = None if self._edition(connector) == "cloud" else {"at": ref}
        response, failure = await self._request(
            connector, auth, "GET", endpoint, params=params
        )
        return failure or IntegrationResult.success(
            file={"path": path, "ref": ref, "content": response.text}
        )

    async def list_issues(
        self, connector, auth, workspace: str | None = None, repo: str | None = None
    ):
        if self._edition(connector) != "cloud":
            return IntegrationResult.failure(
                "Bitbucket Data Center does not provide the Cloud issue tracker API"
            )
        workspace, repo = self._scope(connector, workspace, None, repo)
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            f"/repositories/{path_segment(workspace, 'workspace')}/{path_segment(repo, 'repo')}/issues",
        )
        return failure or IntegrationResult.success(
            issues=response.json().get("values", [])
        )

    async def create_issue(
        self,
        connector,
        auth,
        title: str,
        workspace: str | None = None,
        repo: str | None = None,
        content: str | None = None,
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
            f"/repositories/{path_segment(workspace, 'workspace')}/{path_segment(repo, 'repo')}/issues",
            json={
                "title": required(title, "title"),
                "content": {"raw": content or ""},
            },
        )
        return failure or IntegrationResult.success(issue=response.json())

    async def list_pull_requests(
        self,
        connector,
        auth,
        workspace: str | None = None,
        project: str | None = None,
        repo: str | None = None,
    ):
        scope, repo = self._scope(connector, workspace, project, repo)
        path = (
            f"/repositories/{path_segment(scope, 'workspace')}/{path_segment(repo, 'repo')}/pullrequests"
            if self._edition(connector) == "cloud"
            else f"/projects/{path_segment(scope, 'project')}/repos/{path_segment(repo, 'repo')}/pull-requests"
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
        title: str,
        source_branch: str,
        target_branch: str,
        workspace: str | None = None,
        project: str | None = None,
        repo: str | None = None,
        description: str | None = None,
    ):
        scope, repo = self._scope(connector, workspace, project, repo)
        cloud = self._edition(connector) == "cloud"
        path = (
            f"/repositories/{path_segment(scope, 'workspace')}/{path_segment(repo, 'repo')}/pullrequests"
            if cloud
            else f"/projects/{path_segment(scope, 'project')}/repos/{path_segment(repo, 'repo')}/pull-requests"
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
        pull_request_id: int,
        workspace: str | None = None,
        project: str | None = None,
        repo: str | None = None,
    ):
        scope, repo = self._scope(connector, workspace, project, repo)
        path = (
            f"/repositories/{path_segment(scope, 'workspace')}/{path_segment(repo, 'repo')}/pullrequests/"
            f"{int(pull_request_id)}/merge"
            if self._edition(connector) == "cloud"
            else f"/projects/{path_segment(scope, 'project')}/repos/{path_segment(repo, 'repo')}/pull-requests/"
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
    argument_fallbacks = {"issue_type": "Task"}
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
        if "/rest/api/" in root:
            return root
        edition = str(connector.config.get("edition") or "cloud")
        version = path_segment(
            connector.config.get("api_version") or ("3" if edition == "cloud" else "2"),
            "api_version",
        )
        if not _JIRA_API_VERSION.fullmatch(version):
            raise ValueError(
                f"api_version must be a Jira REST API version such as '3', "
                f"'2' or 'latest', not {version[:80]!r}."
            )
        return f"{root}/rest/api/{version}"

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

    async def get_issue(self, connector, auth, issue_key: str):
        response, failure = await self._request(
            connector, auth, "GET", f"/issue/{_issue_key(issue_key)}"
        )
        return failure or IntegrationResult.success(issue=response.json())

    async def create_issue(
        self,
        connector,
        auth,
        summary: str,
        project_key: str | None = None,
        issue_type: str | None = None,
        description: str | None = None,
        incident_id: str | None = None,
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
            url = f"{root}/browse/{url_quote(str(issue_key), safe='')}"
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

    async def comment_issue(self, connector, auth, issue_key: str, body: str):
        value = _adf(required(body, "body")) if self._cloud(connector) else body
        response, failure = await self._request(
            connector,
            auth,
            "POST",
            f"/issue/{_issue_key(issue_key)}/comment",
            json={"body": value},
        )
        return failure or IntegrationResult.success(comment=response.json())

    async def list_transitions(self, connector, auth, issue_key: str):
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            f"/issue/{_issue_key(issue_key)}/transitions",
        )
        return failure or IntegrationResult.success(
            transitions=response.json().get("transitions", [])
        )

    async def transition_issue(
        self, connector, auth, issue_key: str, transition_id: str
    ):
        response, failure = await self._request(
            connector,
            auth,
            "POST",
            f"/issue/{_issue_key(issue_key)}/transitions",
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
        cloud = self._cloud(connector)
        collection = "/spaces" if cloud else "/space"
        space = str(connector.config.get("space_id") or "").strip()
        space_path = (
            f"{collection}/{path_segment(space, 'space_id')}" if space else None
        )
        response, failure = await self._request(
            connector,
            auth,
            "GET",
            collection,
            params={"limit": 1},
        )
        if failure:
            return failure
        accepted = f"Confluence connection accepted ({response.status_code})."
        if space_path is None:
            return IntegrationResult.success(
                detail=f"{accepted} No default space is set, so none was checked."
            )
        # The account can sign in; the default space is what pages are
        # written to, so a wrong one must not read as healthy (M1-141). Only
        # the configured space is named, never another the account can see.
        found, missing = await self._request(
            connector,
            auth,
            "GET",
            space_path,
        )
        if missing:
            hint = (
                " On Cloud the default space is the numeric space id, not the key."
                if cloud and not space.isdigit()
                else ""
            )
            return IntegrationResult.failure(
                "Confluence accepted the credentials, but the default space "
                f"'{space}' was not found or this account cannot read it "
                f"({missing.error}).{hint}"
            )
        try:
            data = found.json()
        except ValueError:
            data = None
        if not isinstance(data, dict) or not (data.get("id") or data.get("key")):
            # A page that is not the space (a proxy's sign-in page, an empty
            # answer) is not proof that the space is readable.
            return IntegrationResult.failure(
                "Confluence accepted the credentials, but the answer for the "
                f"default space '{space}' was not a space. Check the base URL."
            )
        label = data.get("name") or data.get("key") or space
        return IntegrationResult.success(
            detail=f"{accepted} Default space '{label}' is readable."
        )

    async def read_doc(self, connector, auth, page_id: str):
        if self._cloud(connector):
            path = f"/pages/{numeric_id(page_id, 'page_id')}"
            params = {"body-format": "storage"}
        else:
            path = f"/content/{numeric_id(page_id, 'page_id')}"
            params = {"expand": "body.storage,version"}
        response, failure = await self._request(
            connector, auth, "GET", path, params=params
        )
        return failure or IntegrationResult.success(page=response.json())

    async def write_doc(
        self,
        connector,
        auth,
        title: str,
        content: str,
        space_id: str | None = None,
        page_id: str | None = None,
        parent_id: str | None = None,
        version: int | None = None,
    ):
        space = required(space_id or connector.config.get("space_id"), "space_id")
        page = numeric_id(page_id, "page_id") if page_id is not None else None
        if self._cloud(connector):
            payload = {
                "spaceId": space,
                "status": "current",
                "title": required(title, "title"),
                "body": {"representation": "storage", "value": content},
            }
            if parent_id is not None:
                payload["parentId"] = numeric_id(parent_id, "parent_id")
            method, path = (
                ("PUT", f"/pages/{page}") if page is not None else ("POST", "/pages")
            )
            if page is not None:
                payload["id"] = page
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
                ("PUT", f"/content/{page}")
                if page is not None
                else ("POST", "/content")
            )
            if page is not None:
                payload["id"] = page
                payload["version"] = {"number": int(version or 1) + 1}
        response, failure = await self._request(
            connector, auth, method, path, json=payload
        )
        return failure or IntegrationResult.success(page=response.json())


register_adapter(BitbucketAdapter())
register_adapter(JiraAdapter())
register_adapter(ConfluenceAdapter())
