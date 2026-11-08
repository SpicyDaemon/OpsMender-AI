"""Exercise redirects at the connector's real HTTP boundary."""

import httpx
import pytest

from backend.db.models import IntegrationConnector
from backend.integrations.registry import get_adapter, list_kinds

AUTH = {
    "token": "fixture-token",
    "api_token": "fixture-token",
    "access_token": "fixture-token",
    "api_key": "fixture-token",
    "username": "operator",
    "password": "fixture-password",
    "email": "operator@example.test",
}
KINDS = {kind.kind: kind for kind in list_kinds() if get_adapter(kind.kind)}
CONFIG = {
    "organization": "fixture-org",
    "project": "fixture-project",
    "workspace": "fixture-workspace",
    "job": "fixture-job",
    "page_id": "123",
}
DESTINATION = "https://other.example.test/new-api"


async def _call(
    kind, handler, action="test_connection", *, auth_type=None, auth=None, **arguments
):
    requests = []

    def record(request):
        requests.append(request)
        return handler(request)

    adapter = type(get_adapter(kind))(
        http_client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(record),
            follow_redirects=True,
        ),
    )
    connector = IntegrationConnector(
        kind=kind,
        name=kind,
        base_url="https://provider.example.test",
        auth_type=auth_type or KINDS[kind].auth_types[0],
        config=CONFIG,
    )
    result = await adapter.safe_invoke(action, connector, auth or AUTH, arguments)
    return result, requests


def _redirect(request):
    if request.url.host != "other.example.test":
        return httpx.Response(302, headers={"Location": DESTINATION})
    return httpx.Response(
        200, json={"id": 12, "path_with_namespace": "fixture-project"}
    )


@pytest.mark.parametrize("kind", KINDS)
async def test_connection_rejects_redirect(kind):
    result, requests = await _call(kind, _redirect)
    first_host = (
        "www.googleapis.com" if kind == "google_docs" else "provider.example.test"
    )
    assert [request.url.host for request in requests] == [first_host]
    assert not result.ok
    assert "HTTP 302" in result.error
    assert "https://other.example.test" in result.error
    assert "base URL" in result.error


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
async def test_redirected_jenkins_build_is_not_queued(status):
    def redirect(request):
        if request.url.path == "/canonical/job/build":
            return httpx.Response(200, text="landing page")
        return httpx.Response(status, headers={"Location": "/canonical/job/build"})

    result, requests = await _call("jenkins", redirect, "trigger_build")
    assert [request.method for request in requests] == ["POST"]
    assert not result.ok
    assert f"HTTP {status}" in result.error
    assert "https://provider.example.test" in result.error


async def test_redirect_diagnostic_omits_location_credentials():
    def redirect(_request):
        return httpx.Response(
            307,
            headers={
                "Location": (
                    "https://private-user:private-password@other.example.test:8443"
                    "/private-path?secret=private-query#private-fragment"
                )
            },
        )

    result, requests = await _call("gitlab", redirect)
    assert len(requests) == 1
    assert not result.ok
    assert "https://other.example.test:8443" in result.error
    assert "private-" not in result.error


@pytest.mark.parametrize(
    ("kind", "auth_type", "auth", "expected_path"),
    [
        (
            "github",
            "app",
            {"app_id": "7", "installation_id": "9", "private_key": "fixture-key"},
            "/api/v3/app/installations/9/access_tokens",
        ),
        (
            "google_docs",
            "service_account",
            {"client_email": "svc@example.test", "private_key": "fixture-key"},
            "/token",
        ),
    ],
)
async def test_token_exchange_rejects_redirect(
    monkeypatch, kind, auth_type, auth, expected_path
):
    monkeypatch.setattr("jwt.encode", lambda *args, **kwargs: "fixture-assertion")
    result, requests = await _call(kind, _redirect, auth_type=auth_type, auth=auth)
    assert [(request.method, request.url.path) for request in requests] == [
        ("POST", expected_path)
    ]
    assert not result.ok
    assert "https://other.example.test" in result.error


async def test_gitlab_does_not_forward_bearer_on_redirect():
    result, requests = await _call("gitlab", _redirect)
    assert len(requests) == 1
    assert requests[0].headers["Authorization"] == "Bearer fixture-token"
    assert "Private-Token" not in requests[0].headers
    assert not result.ok


@pytest.mark.parametrize(
    "status,location", [(300, "/landing"), (304, None), (302, None)]
)
async def test_custom_probe_fails_without_following_any_3xx(status, location):
    def redirect(_request):
        return httpx.Response(
            status, headers={"Location": location} if location else {}
        )

    result, requests = await _call("custom", redirect)
    assert len(requests) == 1
    assert not result.ok
    assert f"HTTP {status}" in result.error


@pytest.mark.parametrize(
    "status,location", [(200, "/queue/item/9/"), (201, "/login"), (201, None)]
)
async def test_jenkins_requires_queue_receipt(status, location):
    def response(_request):
        return httpx.Response(
            status, headers={"Location": location} if location else {}
        )

    result, requests = await _call("jenkins", response, "trigger_build")
    assert len(requests) == 1
    assert not result.ok
    assert "did not confirm a queued build" in result.error


@pytest.mark.parametrize(
    "status,body", [(200, {"iid": 9}), (201, {}), (201, {"iid": True})]
)
async def test_gitlab_requires_created_issue_receipt(status, body):
    result, requests = await _call(
        "gitlab",
        lambda _request: httpx.Response(status, json=body),
        "create_issue",
        title="Fixture",
    )
    assert len(requests) == 1
    assert not result.ok
    assert "did not confirm a created issue" in result.error
