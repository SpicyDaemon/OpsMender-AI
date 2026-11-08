"""M1-146: the GitLab connection test checks the configured project.

In the 2026-11-05 follow-up a fine-grained token limited to one private
project read that project, its README and its issues through the adapter,
and created an issue and merged a merge request, while Test answered
"GitLab HTTP 403": the probe read ``GET /user``, which a token with no user
or global access may not call.
"""

from __future__ import annotations

import httpx

from backend.db.models import IntegrationConnector
from backend.integrations.gitlab import GitLabAdapter

SCOPED = "project-only-token"
ACCOUNT = "account-token"


def _adapter():
    """gitlab.com as a project-only token and an account token see it."""
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        path = request.url.raw_path.decode().split("?", 1)[0]
        if token not in {SCOPED, ACCOUNT}:
            return httpx.Response(401, json={"message": "401 Unauthorized"})
        if path == "/api/v4/user":
            if token == SCOPED:
                return httpx.Response(403, json={"message": "403 Forbidden"})
            return httpx.Response(200, json={"username": "ops"})
        if path == "/api/v4/projects/acme%2Fopsmender-intg1-lab":
            return httpx.Response(
                200,
                json={"id": 88, "path_with_namespace": "acme/opsmender-intg1-lab"},
            )
        # GitLab answers 404 for a project the token cannot see.
        return httpx.Response(404, json={"message": "404 Project Not Found"})

    adapter = GitLabAdapter(
        http_client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        )
    )
    return adapter, requests


def _connector(**config) -> IntegrationConnector:
    return IntegrationConnector(
        kind="gitlab", name="GitLab", base_url=None, auth_type="pat", config=config
    )


async def test_a_project_only_token_passes_on_its_project():
    adapter, requests = _adapter()
    result = await adapter.safe_invoke(
        "test_connection",
        _connector(project="acme/opsmender-intg1-lab"),
        {"token": SCOPED},
    )
    # On main this was "GitLab HTTP 403: 403 Forbidden" from GET /user.
    assert result.ok is True
    assert result.data["detail"] == (
        "GitLab credentials accepted for project acme/opsmender-intg1-lab."
    )
    assert [r.url.raw_path.decode() for r in requests] == [
        "/api/v4/projects/acme%2Fopsmender-intg1-lab"
    ]


async def test_wrong_credentials_fail_on_the_project():
    adapter, _ = _adapter()
    result = await adapter.safe_invoke(
        "test_connection",
        _connector(project="acme/opsmender-intg1-lab"),
        {"token": "wrong"},
    )
    assert result.ok is False
    assert result.error == "GitLab HTTP 401: 401 Unauthorized"


async def test_a_token_for_another_project_fails_and_names_the_project():
    adapter, _ = _adapter()
    result = await adapter.safe_invoke(
        "test_connection", _connector(project="acme/payments"), {"token": SCOPED}
    )
    assert result.ok is False
    assert result.error == (
        "GitLab does not show the project 'acme/payments' to this token "
        "(GitLab HTTP 404: 404 Project Not Found). Check the project path and "
        "that the token covers it."
    )


async def test_an_answer_that_is_not_a_project_is_not_healthy():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"id": 1}])

    adapter = GitLabAdapter(
        http_client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        )
    )
    result = await adapter.safe_invoke(
        "test_connection", _connector(project="acme/payments"), {"token": SCOPED}
    )
    assert result.ok is False
    assert result.error == (
        "The base URL answered, but not with a GitLab project. Check the base URL."
    )


async def test_a_refusal_is_read_by_its_status_not_by_words_in_its_text():
    """A provider's message that happens to contain "HTTP 404" is not a
    missing project."""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "upstream said HTTP 404 earlier"})

    adapter = GitLabAdapter(
        http_client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        )
    )
    result = await adapter.safe_invoke(
        "test_connection", _connector(project="acme/payments"), {"token": SCOPED}
    )
    assert result.error == "GitLab HTTP 403: upstream said HTTP 404 earlier"


async def test_without_a_default_project_the_account_check_is_kept():
    adapter, requests = _adapter()
    account = await adapter.safe_invoke(
        "test_connection", _connector(), {"token": ACCOUNT}
    )
    assert account.ok is True
    assert account.data["detail"] == "GitLab credentials accepted (ops)."
    assert [r.url.path for r in requests] == ["/api/v4/user"]

    # A project-only token with no default project cannot be checked: the
    # refusal now says what to set.
    scoped = await adapter.safe_invoke(
        "test_connection", _connector(), {"token": SCOPED}
    )
    assert scoped.ok is False
    assert scoped.error == (
        "GitLab HTTP 403: 403 Forbidden. If this token is limited to one "
        "project, set the connector's default project and test again."
    )
