"""M1-60: only an admin may send connection settings to model discovery.

With a caller's base URL and key variable, GET /models would read that
variable from the server's environment and send its value to that URL. Every
other role, and a Viewer's API token, now gets 403 before discovery runs;
plain discovery is unchanged for every role; a key setting must name a
variable, never hold a key; an unknown provider is a 400, not a server error.
"""

from __future__ import annotations

import uuid

import pytest

from tests.test_role_access import _headers, client as _client_fixture

client = pytest.fixture(_client_fixture.__wrapped__)

LEAK = {
    "provider": "openai_compatible",
    "base_url": "http://127.0.0.1:9/v1",
    "api_key_env_var": "OPSMENDER_JWT_SECRET",
}
SETTINGS = (
    "api_key_env_var",
    "base_url",
    "api_version",
    "region",
    "profile",
    "project",
    "location",
)
PASTED_KEY = "sk-live-0123456789abcdef"
ADMIN_ONLY = {"detail": "Only an admin can send connection settings to model discovery"}


@pytest.fixture
def discovered(monkeypatch):
    """Record every discovery call instead of building a provider."""
    calls = []

    def record(self, **kwargs):
        calls.append(kwargs)
        return []

    monkeypatch.setattr(
        "backend.api.routes.models.ProviderRegistry.discover_models", record
    )
    return calls


async def _viewer_token(client, h) -> dict[str, str]:
    minted = await client.post(
        "/api/v1/api-tokens",
        headers=h["admin"],
        json={"name": "viewer-script", "role": "viewer"},
    )
    assert minted.status_code in (200, 201), minted.text
    return {"Authorization": f"Bearer {minted.json()['token']}"}


async def test_only_an_admin_sends_connection_settings(client, discovered):
    h = await _headers(client)
    h["viewer_token"] = await _viewer_token(client, h)

    for who in ("viewer", "operator", "viewer_token"):
        refused = await client.get("/models", headers=h[who], params=LEAK)
        assert refused.status_code == 403, (who, refused.text)
        assert refused.json() == ADMIN_ONLY
    # Discovery never ran, so no variable was read and nothing was sent.
    assert discovered == []

    allowed = await client.get("/models", headers=h["admin"], params=LEAK)
    assert allowed.status_code == 200, allowed.text
    assert discovered == [
        {
            "provider": "openai_compatible",
            "model_id": None,
            "api_key_env_var": "OPSMENDER_JWT_SECRET",
            "base_url": "http://127.0.0.1:9/v1",
            "api_version": None,
            "provider_meta": None,
        }
    ]


@pytest.mark.parametrize("setting", SETTINGS)
async def test_each_connection_setting_needs_an_admin(client, discovered, setting):
    h = await _headers(client)
    refused = await client.get(
        "/models", headers=h["operator"], params={"provider": "ollama", setting: "x"}
    )
    assert refused.status_code == 403, refused.text
    assert refused.json() == ADMIN_ONLY
    assert discovered == []


async def test_plain_discovery_is_unchanged_for_every_role(client, discovered):
    h = await _headers(client)
    for who in ("viewer", "operator", "admin"):
        listed = await client.get(
            "/models",
            headers=h[who],
            params={"provider": "ollama", "model_id": "llama3"},
        )
        assert listed.status_code == 200, (who, listed.text)
    assert len(discovered) == 3
    assert all(
        call["base_url"] is None and call["api_key_env_var"] is None
        for call in discovered
    )


async def test_an_unknown_provider_is_a_bad_request(client):
    h = await _headers(client)
    resp = await client.get("/models", headers=h["admin"], params={"provider": "nope"})
    assert resp.status_code == 400, resp.text


async def test_a_key_setting_must_name_a_variable(client):
    """A pasted key is refused wherever a key setting is taken, before any
    variable is read or anything is stored, and the refusal never repeats it."""
    from backend.api.deps import get_current_session_factory
    from backend.db.repos import ModelConfigRepo

    h = await _headers(client)
    me = (await client.get("/auth/me", headers=h["admin"])).json()
    org_id = uuid.UUID(me["primary_org_id"])
    async with get_current_session_factory()() as db:
        saved = await ModelConfigRepo.create(
            db,
            org_id,
            name="named-key",
            provider="openai",
            model_id="gpt-test",
            api_key_env_var="OPENAI_API_KEY",
        )
        await db.commit()
        saved_id = saved.id

    body = {
        "name": "pasted",
        "provider": "openai_compatible",
        "model_id": "m",
        "base_url": "http://127.0.0.1:9/v1",
        "api_key_env_var": PASTED_KEY,
    }
    answers = [
        await client.get(
            "/models",
            headers=h["admin"],
            params={
                "provider": "openai_compatible",
                "base_url": "http://127.0.0.1:9/v1",
                "api_key_env_var": PASTED_KEY,
            },
        ),
        await client.post("/models/configs", headers=h["admin"], json=body),
        await client.put(f"/models/configs/{saved_id}", headers=h["admin"], json=body),
        await client.put("/config/model", headers=h["admin"], json=body),
    ]
    for resp in answers:
        assert resp.status_code == 400, resp.text
        assert PASTED_KEY not in resp.text
        assert resp.json()["detail"].startswith(
            "api_key_env_var must name an environment variable"
        )

    configs = await client.get("/models/configs", headers=h["admin"])
    assert configs.status_code == 200, configs.text
    assert PASTED_KEY not in configs.text
    [kept] = [item for item in configs.json()["items"] if item["id"] == str(saved_id)]
    assert kept["api_key_env_var"] == "OPENAI_API_KEY"
