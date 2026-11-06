"""M1-141 (I-05): the Confluence connection test checks the default space.

In the 2026-11-04 live round the connector's default space was replaced by
an id the provider answers 404 for, and Test still stored the connector as
healthy: the probe listed one space and never looked at the configured one.
"""

from __future__ import annotations

import httpx
import pytest

from backend.db.models import IntegrationConnector
from backend.integrations.atlassian import ConfluenceAdapter
from backend.integrations.http import response_error
from backend.integrations.registry import config_fields

AUTH = {"email": "ops@example.test", "api_token": "t"}


def _adapter(spaces: dict[str, dict], *, account_status: int = 200):
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        path = request.url.path
        if path.endswith(("/spaces", "/space")):
            if account_status != 200:
                return httpx.Response(account_status, json={"message": "Unauthorized"})
            return httpx.Response(200, json={"results": [{"id": "1", "name": "Other"}]})
        key = path.rsplit("/", 1)[-1]
        if key in spaces:
            return httpx.Response(200, json=spaces[key])
        if key == "blank":
            return httpx.Response(200, content=b"")
        # The v2 API's error shape.
        return httpx.Response(
            404,
            json={
                "errors": [{"status": 404, "code": "NOT_FOUND", "title": "Not Found"}]
            },
        )

    adapter = ConfluenceAdapter(
        http_client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        )
    )
    return adapter, requests


def _connector(**config) -> IntegrationConnector:
    return IntegrationConnector(
        kind="confluence",
        name="Confluence",
        base_url="https://opsmender.atlassian.net/wiki",
        auth_type="pat",
        config=config,
    )


async def test_a_default_space_the_provider_does_not_have_is_not_healthy():
    adapter, requests = _adapter({"98765": {"id": "98765", "name": "INTG1"}})

    # The live round's case: a numeric id the provider answers 404 for.
    wrong = await adapter.safe_invoke(
        "test_connection", _connector(edition="cloud", space_id="11111"), AUTH
    )
    assert wrong.ok is False
    assert wrong.error == (
        "Confluence accepted the credentials, but the default space '11111' "
        "was not found or this account cannot read it "
        "(Confluence HTTP 404: Not Found)."
    )
    # Only the configured space is named, never one the account can list.
    assert "Other" not in wrong.error
    assert [r.url.path for r in requests] == [
        "/wiki/api/v2/spaces",
        "/wiki/api/v2/spaces/11111",
    ]


async def test_the_configured_space_is_verified():
    adapter, _ = _adapter({"98765": {"id": "98765", "name": "INTG1"}})
    right = await adapter.safe_invoke(
        "test_connection", _connector(edition="cloud", space_id="98765"), AUTH
    )
    assert right.ok is True
    assert right.data["detail"] == (
        "Confluence connection accepted (200). Default space 'INTG1' is readable."
    )


async def test_a_space_key_on_cloud_gets_the_numeric_id_hint():
    adapter, _ = _adapter({})
    keyed = await adapter.safe_invoke(
        "test_connection", _connector(edition="cloud", space_id="OPS"), AUTH
    )
    assert keyed.ok is False
    assert keyed.error.endswith(
        " On Cloud the default space is the numeric space id, not the key."
    )


async def test_an_answer_that_is_not_the_space_is_not_healthy():
    adapter, _ = _adapter({})
    result = await adapter.safe_invoke(
        "test_connection", _connector(edition="cloud", space_id="blank"), AUTH
    )
    assert result.ok is False
    assert result.error == (
        "Confluence accepted the credentials, but the answer for the default "
        "space 'blank' was not a space. Check the base URL."
    )


def test_the_form_says_which_kind_of_space_id_each_edition_takes():
    field = next(f for f in config_fields("confluence") if f.name == "space_id")
    assert field.placeholder == "98765"
    assert field.helper == (
        "Cloud: the numeric space id, for example 98765. On-premises: the "
        "space key, for example OPS."
    )


def test_a_providers_error_text_is_kept_short():
    long = httpx.Response(400, json={"message": "x" * 20000})
    assert len(response_error("Confluence", long).error) < 340
    listed = httpx.Response(
        409, json={"errors": [{"title": "Version conflict"}, {"detail": "Stale"}]}
    )
    assert response_error("Confluence", listed).error == (
        "Confluence HTTP 409: Version conflict; Stale"
    )


async def test_no_default_space_says_none_was_checked():
    adapter, requests = _adapter({})
    blank = await adapter.safe_invoke(
        "test_connection", _connector(edition="cloud"), AUTH
    )
    assert blank.ok is True
    assert blank.data["detail"] == (
        "Confluence connection accepted (200). No default space is set, so "
        "none was checked."
    )
    assert len(requests) == 1


async def test_an_on_premises_space_is_checked_by_its_key():
    adapter, requests = _adapter({"OPS": {"key": "OPS", "name": "Operations"}})
    connector = IntegrationConnector(
        kind="confluence",
        name="Confluence",
        base_url="https://wiki.internal.example",
        auth_type="basic",
        config={"edition": "on_prem", "space_id": "OPS"},
    )
    result = await adapter.safe_invoke(
        "test_connection", connector, {"username": "u", "password": "p"}
    )
    assert result.ok is True
    assert result.data["detail"].endswith("Default space 'Operations' is readable.")
    assert [r.url.path for r in requests] == ["/rest/api/space", "/rest/api/space/OPS"]


@pytest.mark.parametrize("status", [401, 403])
async def test_wrong_credentials_fail_before_the_space_is_looked_at(status):
    adapter, requests = _adapter({"98765": {"name": "INTG1"}}, account_status=status)
    result = await adapter.safe_invoke(
        "test_connection", _connector(edition="cloud", space_id="98765"), AUTH
    )
    assert result.ok is False
    assert result.error == f"Confluence HTTP {status}: Unauthorized"
    assert len(requests) == 1
