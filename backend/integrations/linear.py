"""Linear GraphQL issue adapter."""

from __future__ import annotations

from typing import Any

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
)
from backend.integrations.registry import register_adapter


class LinearAdapter(IntegrationAdapter):
    kind = "linear"
    capabilities = (
        IntegrationCapability("test_connection", "Validate Linear access."),
        IntegrationCapability("get_issue", "Read an issue."),
        IntegrationCapability("list_issues", "List issues."),
        IntegrationCapability(
            "create_issue",
            "Create an issue.",
            classification="caution",
            mutating=True,
        ),
        IntegrationCapability(
            "update_issue",
            "Update an issue.",
            classification="caution",
            mutating=True,
        ),
    )

    def __init__(self, *, http_client_factory: HttpClientFactory | None = None):
        self._factory = http_client_factory or default_http_client

    @staticmethod
    def _endpoint(connector: IntegrationConnector) -> str:
        return (connector.base_url or "https://api.linear.app/graphql").rstrip("/")

    @staticmethod
    def _headers(
        connector: IntegrationConnector,
        auth: dict[str, Any],
    ) -> dict[str, str]:
        token = required(auth.get("access_token") or auth.get("api_key"), "api_key")
        value = f"Bearer {token}" if connector.auth_type == "oauth" else token
        return {"Authorization": value, "Content-Type": "application/json"}

    async def _graphql(
        self,
        connector: IntegrationConnector,
        auth: dict[str, Any],
        query: str,
        variables: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, IntegrationResult | None]:
        async with self._factory() as client:
            response = await client.post(
                self._endpoint(connector),
                headers=self._headers(connector, auth),
                json={"query": query, "variables": variables or {}},
            )
        if response.status_code >= 400:
            return None, response_error("Linear", response)
        body = response.json()
        if body.get("errors"):
            messages = "; ".join(
                str(item.get("message") or item) for item in body["errors"]
            )
            return None, IntegrationResult.failure(f"Linear GraphQL: {messages}")
        return body.get("data", {}), None

    async def test_connection(self, connector, auth):
        data, failure = await self._graphql(
            connector,
            auth,
            "query Viewer { viewer { id name email } }",
        )
        return failure or IntegrationResult.success(
            detail=f"Linear credentials accepted ({data['viewer'].get('name', 'user')})."
        )

    async def get_issue(self, connector, auth, issue_id):
        data, failure = await self._graphql(
            connector,
            auth,
            """
            query Issue($id: String!) {
              issue(id: $id) { id identifier title description priority url state { id name } }
            }
            """,
            {"id": required(issue_id, "issue_id")},
        )
        return failure or IntegrationResult.success(issue=data["issue"])

    async def list_issues(
        self, connector, auth, first=50, team_id=None, assignee_id=None
    ):
        filters: dict[str, Any] = {}
        if team_id or connector.config.get("team_id"):
            filters["team"] = {"id": {"eq": team_id or connector.config.get("team_id")}}
        if assignee_id:
            filters["assignee"] = {"id": {"eq": assignee_id}}
        data, failure = await self._graphql(
            connector,
            auth,
            """
            query Issues($first: Int!, $filter: IssueFilter) {
              issues(first: $first, filter: $filter) {
                nodes { id identifier title priority url state { id name } }
              }
            }
            """,
            {"first": min(max(int(first), 1), 100), "filter": filters or None},
        )
        return failure or IntegrationResult.success(issues=data["issues"]["nodes"])

    async def create_issue(
        self,
        connector,
        auth,
        title,
        team_id=None,
        description=None,
        priority=None,
        assignee_id=None,
    ):
        issue_input: dict[str, Any] = {
            "teamId": required(team_id or connector.config.get("team_id"), "team_id"),
            "title": required(title, "title"),
        }
        if description is not None:
            issue_input["description"] = description
        if priority is not None:
            issue_input["priority"] = int(priority)
        if assignee_id:
            issue_input["assigneeId"] = assignee_id
        data, failure = await self._graphql(
            connector,
            auth,
            """
            mutation CreateIssue($input: IssueCreateInput!) {
              issueCreate(input: $input) {
                success
                issue { id identifier title url }
              }
            }
            """,
            {"input": issue_input},
        )
        payload = data["issueCreate"] if data else {}
        if not failure and not payload.get("success"):
            return IntegrationResult.failure("Linear did not create the issue")
        return failure or IntegrationResult.success(issue=payload["issue"])

    async def update_issue(self, connector, auth, issue_id, fields):
        if not isinstance(fields, dict) or not fields:
            raise ValueError("fields is required")
        data, failure = await self._graphql(
            connector,
            auth,
            """
            mutation UpdateIssue($id: String!, $input: IssueUpdateInput!) {
              issueUpdate(id: $id, input: $input) {
                success
                issue { id identifier title url }
              }
            }
            """,
            {"id": required(issue_id, "issue_id"), "input": fields},
        )
        payload = data["issueUpdate"] if data else {}
        if not failure and not payload.get("success"):
            return IntegrationResult.failure("Linear did not update the issue")
        return failure or IntegrationResult.success(issue=payload["issue"])


register_adapter(LinearAdapter())
