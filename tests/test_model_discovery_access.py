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
    # The admin's settings reached discovery as sent.
    assert len(discovered) == 1
    assert discovered[0]["api_key_env_var"] == "OPSMENDER_JWT_SECRET"
    assert discovered[0]["base_url"] == "http://127.0.0.1:9/v1"


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
            provider="openai_compatible",
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


LISTED = "Model keys and hosts stay with their provider (O-22, M1-63)."


async def test_a_key_setting_must_be_a_listed_variable(client, monkeypatch):
    """Even an admin cannot name any variable the server holds as a model key:
    only the provider's own variable or one the operator lists in
    OPSMENDER_MODEL_KEY_VARS, wherever a key setting is taken."""
    monkeypatch.delenv("OPSMENDER_MODEL_KEY_VARS", raising=False)
    h = await _headers(client)
    body = {
        "name": "leak",
        "provider": "openai_compatible",
        "model_id": "m",
        "base_url": "http://127.0.0.1:9/v1",
        "api_key_env_var": "OPSMENDER_JWT_SECRET",
    }
    answers = [
        await client.get("/models", headers=h["admin"], params=LEAK),
        await client.post("/models/configs", headers=h["admin"], json=body),
        await client.put("/config/model", headers=h["admin"], json=body),
    ]
    for resp in answers:
        assert resp.status_code == 400, resp.text
        assert "OPSMENDER_JWT_SECRET" not in resp.text
        assert resp.json()["detail"].startswith(
            "api_key_env_var must be the provider's own key variable"
        )

    # A variable the operator lists is accepted; discovery then runs against
    # the closed port and reports its error instead of refusing.
    monkeypatch.setenv("OPSMENDER_MODEL_KEY_VARS", "GROQ_API_KEY, OTHER_KEY")
    listed = await client.get(
        "/models",
        headers=h["admin"],
        params={**LEAK, "api_key_env_var": "GROQ_API_KEY"},
    )
    assert listed.status_code == 200, listed.text
    saved = await client.post(
        "/models/configs",
        headers=h["admin"],
        json={**body, "api_key_env_var": "GROQ_API_KEY"},
    )
    assert saved.status_code in (200, 201), saved.text
    assert saved.json()["config"]["api_key_env_var"] == "GROQ_API_KEY"

    # The provider's own variable needs no listing.
    own = await client.get(
        "/models",
        headers=h["admin"],
        params={"provider": "openai", "api_key_env_var": "OPENAI_API_KEY"},
    )
    assert own.status_code == 200, own.text


async def test_a_fixed_host_provider_refuses_another_base_url(client, monkeypatch):
    """A provider with a fixed host only connects there, so its key cannot be
    sent to a URL of the caller's choosing; the free-URL providers take any
    URL but only listed keys."""
    monkeypatch.setenv("OPSMENDER_MODEL_KEY_VARS", "GROQ_API_KEY")
    h = await _headers(client)
    foreign = "http://127.0.0.1:9/v1"
    body = {
        "name": "foreign",
        "provider": "openai",
        "model_id": "m",
        "base_url": foreign,
    }
    answers = [
        await client.get(
            "/models",
            headers=h["admin"],
            params={"provider": "openai", "base_url": foreign},
        ),
        await client.post("/models/configs", headers=h["admin"], json=body),
        await client.put("/config/model", headers=h["admin"], json=body),
    ]
    for resp in answers:
        assert resp.status_code == 400, resp.text
        assert resp.json()["detail"] == (
            "Provider 'openai' only connects to api.openai.com; base_url must be a URL there"
        )

    at_home = await client.get(
        "/models",
        headers=h["admin"],
        params={"provider": "openai", "base_url": "https://api.openai.com/v1"},
    )
    assert at_home.status_code == 200, at_home.text
    free = await client.get(
        "/models",
        headers=h["admin"],
        params={
            "provider": "openai_compatible",
            "base_url": foreign,
            "api_key_env_var": "GROQ_API_KEY",
        },
    )
    assert free.status_code == 200, free.text
