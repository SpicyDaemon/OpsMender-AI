"""M1-39 (R-22, O-07): Viewers and viewer tokens never receive credential
values from SLA probe targets or model configurations (MCP servers since
M1-29), and only admins and operators export Activity; Viewers still read it."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from backend.api.auth import hash_password
from backend.auth.redaction import probe_config_for_viewer, url_without_secrets
from backend.db.repos import ModelConfigRepo, SLATargetRepo, UserRepo
from tests.test_api_tokens import (
    TEST_ORG_ID,
    _bearer,
    _create_token,
    admin_headers as _admin_headers_fixture,
    app as _app_fixture,
    client as _client_fixture,
)

app = pytest.fixture(_app_fixture.__wrapped__)
client = pytest.fixture(_client_fixture.__wrapped__)
admin_headers = pytest.fixture(_admin_headers_fixture.__wrapped__)

PASSWORD = "credential-check-1"
SECRETS = (
    "probe-user",
    "probe-pass",
    "SECRET-TOKEN-1",
    "SECRET-HEADER-2",
    "SECRET-BODY-3",
    "SECRET-KEY-4",
    "SECRET-QUERY-5",
)
PROBE_URL = (
    "https://probe-user:probe-pass@status.example.test:8443/health"
    "?token=SECRET-TOKEN-1#SECRET-TOKEN-1"
)


async def _seed(app) -> str:
    async with app.state.session_factory() as db:
        target = await SLATargetRepo.create(
            db,
            TEST_ORG_ID,
            name="Checkout health",
            kind="http",
            config={
                "url": PROBE_URL,
                "method": "POST",
                "headers": {"Authorization": "Bearer SECRET-HEADER-2"},
                "body": "SECRET-BODY-3",
                "expected_status": 200,
            },
        )
        await ModelConfigRepo.create(
            db,
            TEST_ORG_ID,
            name="Default model",
            provider="openai",
            model_id="gpt-test",
            api_key_env_var="OPENAI_API_KEY",
            base_url="https://key:SECRET-KEY-4@llm.example.test/v1?api-key=SECRET-QUERY-5",
            is_default=True,
        )
        await db.commit()
        return str(target.id)


async def _login(app, client: AsyncClient, name: str, role: str) -> dict:
    async with app.state.session_factory() as db:
        user = await UserRepo.create(
            db,
            username=name,
            email=f"{name}@test.com",
            password_hash=hash_password(PASSWORD),
            role=role,
            primary_org_id=TEST_ORG_ID,
        )
        await UserRepo.add_to_organization(
            db, user_id=user.id, org_id=TEST_ORG_ID, role=role
        )
        await db.commit()
    resp = await client.post(
        "/auth/login", json={"username": name, "password": PASSWORD}
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _routes(target_id: str) -> list[str]:
    return [
        "/sla-targets",
        f"/sla-targets/{target_id}",
        "/models/configs",
        "/models/bootstrap",
        "/mcp-servers",
    ]


@pytest.mark.parametrize("via", ["viewer", "viewer-token"])
async def test_viewers_never_receive_credential_values(
    app, client: AsyncClient, admin_headers, via
):
    target_id = await _seed(app)
    if via == "viewer":
        headers = await _login(app, client, "cred-viewer", "viewer")
    else:
        token = await _create_token(client, admin_headers, name="read", role="viewer")
        headers = _bearer(token["token"])

    for route in _routes(target_id):
        resp = await client.get(route, headers=headers)
        assert resp.status_code == 200, (route, resp.text)
        leaked = [secret for secret in SECRETS if secret in resp.text]
        assert leaked == [], (route, leaked)

    target = (await client.get(f"/sla-targets/{target_id}", headers=headers)).json()
    clean = "https://status.example.test:8443/health"
    assert target["url"] == target["config"]["url"] == clean
    assert target["config"] == {"url": clean, "method": "POST", "expected_status": 200}
    [model] = (await client.get("/models/configs", headers=headers)).json()["items"]
    assert model["base_url"] == "https://llm.example.test/v1"
    assert model["api_key_env_var"] == "OPENAI_API_KEY"  # a name, not a value


async def test_operators_and_admins_still_see_the_configuration(
    app, client: AsyncClient, admin_headers
):
    target_id = await _seed(app)
    operator = await _login(app, client, "cred-operator", "operator")

    for headers in (admin_headers, operator):
        target = (await client.get(f"/sla-targets/{target_id}", headers=headers)).json()
        assert target["config"]["url"] == PROBE_URL
        assert target["config"]["headers"] == {
            "Authorization": "Bearer SECRET-HEADER-2"
        }
        [model] = (await client.get("/models/configs", headers=headers)).json()["items"]
        assert "SECRET-QUERY-5" in model["base_url"]


async def test_only_admins_and_operators_export_activity(
    app, client: AsyncClient, admin_headers
):
    viewer = await _login(app, client, "export-viewer", "viewer")
    operator = await _login(app, client, "export-operator", "operator")
    token = await _create_token(client, admin_headers, name="read", role="viewer")

    assert (await client.get("/audit/export.csv", headers=viewer)).status_code == 403
    assert (
        await client.get("/audit/export.csv", headers=_bearer(token["token"]))
    ).status_code == 403
    assert (await client.get("/audit/export.csv", headers=operator)).status_code == 200
    assert (
        await client.get("/audit/export.csv", headers=admin_headers)
    ).status_code == 200
    # Viewers still read the Activity page.
    assert (await client.get("/audit", headers=viewer)).status_code == 200


def test_urls_keep_only_scheme_host_port_and_path():
    assert url_without_secrets(PROBE_URL) == "https://status.example.test:8443/health"
    assert url_without_secrets("http://[::1]:9000/x?y=1") == "http://[::1]:9000/x"
    assert url_without_secrets(None) is None
    assert url_without_secrets("not a url") is None
    assert probe_config_for_viewer(
        "tcp", {"host": "db.internal", "port": 5432, "password": "x"}
    ) == {"host": "db.internal", "port": 5432}
