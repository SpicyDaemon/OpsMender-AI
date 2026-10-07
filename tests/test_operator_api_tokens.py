"""M1-03: operators create, list and revoke their own Operator API tokens.

Admins mint Admin, Operator or Viewer tokens and see and revoke all of them.
An operator, signed in, mints Operator tokens only; lists and revokes only
their own (others' look absent). Their tokens stop working when the operator
is deactivated and drop to Viewer when they are demoted.
"""

from __future__ import annotations

import uuid

import pytest

from backend.db.models import ApiToken
from tests.test_ownership_lifecycle import (
    World,
    _headers,
    app as _app_fixture,
    client as _client_fixture,
    world as _world_fixture,
)

app = pytest.fixture(_app_fixture.__wrapped__)
client = pytest.fixture(_client_fixture.__wrapped__)
world = pytest.fixture(_world_fixture.__wrapped__)


async def _create(w: World, headers, *, role="operator", name=None):
    return await w.client.post(
        "/api/v1/api-tokens",
        json={"name": name or f"op-{uuid.uuid4().hex[:6]}", "role": role},
        headers=headers,
    )


def _bearer(secret: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}"}


async def test_an_operator_creates_only_operator_tokens(world):
    operator = await _headers(world.client, "lc-l3")
    created = await _create(world, operator)
    assert created.status_code == 201, created.text
    assert created.json()["role"] == "operator"
    for role in ("admin", "viewer"):
        refused = await _create(world, operator, role=role)
        assert refused.status_code == 422, (role, refused.text)
        assert refused.json()["detail"] == "Operators can create only Operator tokens."


async def test_an_operator_must_be_signed_in_to_create_one(world):
    operator = await _headers(world.client, "lc-l3")
    secret = (await _create(world, operator)).json()["token"]
    refused = await _create(world, _bearer(secret))
    assert refused.status_code == 401, refused.text


async def test_operators_see_and_revoke_only_their_own(world):
    mine = await _headers(world.client, "lc-l3")
    theirs = await _headers(world.client, "lc-l2")
    own = (await _create(world, mine, name="mine")).json()
    other = (await _create(world, theirs, name="theirs")).json()
    admin_made = (await _create(world, world.admin, name="admin-made")).json()

    listed = await world.client.get("/api/v1/api-tokens", headers=mine)
    assert listed.status_code == 200, listed.text
    assert [row["name"] for row in listed.json()["items"]] == ["mine"]
    everyone = await world.client.get("/api/v1/api-tokens", headers=world.admin)
    assert {row["name"] for row in everyone.json()["items"]} >= {
        "mine",
        "theirs",
        "admin-made",
    }

    for token in (other, admin_made):
        refused = await world.client.delete(
            f"/api/v1/api-tokens/{token['id']}", headers=mine
        )
        assert refused.status_code == 404, refused.text
        async with world.app.state.session_factory() as db:
            assert (await db.get(ApiToken, uuid.UUID(token["id"]))).revoked_at is None
    revoked = await world.client.delete(f"/api/v1/api-tokens/{own['id']}", headers=mine)
    assert revoked.status_code == 204, revoked.text
    assert (
        await world.client.get("/incidents", headers=_bearer(own["token"]))
    ).status_code == 401


async def test_an_admin_made_operator_token_acts_as_an_operator(world):
    operator = await _headers(world.client, "lc-l3")
    await _create(world, operator, name="theirs")
    full = (await _create(world, world.admin, role="admin", name="full")).json()
    capped = (await _create(world, world.admin, name="capped")).json()
    assert capped["role"] == "operator"
    token = _bearer(capped["token"])
    # It sees only its creator's Operator and Viewer tokens, never their Admin
    # token or anyone else's.
    listed = await world.client.get("/api/v1/api-tokens", headers=token)
    assert listed.status_code == 200, listed.text
    assert [row["name"] for row in listed.json()["items"]] == ["capped"]
    hidden = await world.client.delete(
        f"/api/v1/api-tokens/{full['id']}", headers=token
    )
    assert hidden.status_code == 404, hidden.text
    async with world.app.state.session_factory() as db:
        assert (await db.get(ApiToken, uuid.UUID(full["id"]))).revoked_at is None
    assert (
        await world.client.get("/api/v1/api-tokens", headers=_bearer(full["token"]))
    ).status_code == 200
    refused = await _create(world, token, role="admin")
    assert refused.status_code == 401, refused.text


async def test_a_name_another_token_holds_gets_a_clear_conflict(world):
    await _create(world, world.admin, name="ci")
    operator = await _headers(world.client, "lc-l3")
    clash = await _create(world, operator, name="ci")
    assert clash.status_code == 409, clash.text
    assert clash.json()["detail"] == (
        'A token named "ci" already exists in this workspace, possibly someone '
        "else's or a revoked one. Choose another name."
    )


async def test_deactivating_an_operator_stops_their_token(world):
    operator = await _headers(world.client, "lc-l2")
    token = _bearer((await _create(world, operator)).json()["token"])
    assert (await world.client.get("/incidents", headers=token)).status_code == 200
    deactivated = await world.client.patch(
        f"/auth/users/{world.level2}", json={"is_active": False}, headers=world.admin
    )
    assert deactivated.status_code == 200, deactivated.text
    assert (await world.client.get("/incidents", headers=token)).status_code == 401


async def test_an_admin_revokes_an_operators_token(world):
    mine = await _headers(world.client, "lc-l3")
    own = (await _create(world, mine)).json()
    revoked = await world.client.delete(
        f"/api/v1/api-tokens/{own['id']}", headers=world.admin
    )
    assert revoked.status_code == 204, revoked.text


async def test_an_operators_token_follows_their_account(world):
    operator = await _headers(world.client, "lc-l3")
    secret = (await _create(world, operator)).json()["token"]
    token = _bearer(secret)
    assert (await world.client.get("/incidents", headers=token)).status_code == 200
    # An operator endpoint works while they are an operator.
    assert (
        await world.client.get("/api/v1/api-tokens", headers=token)
    ).status_code == 200

    demoted = await world.client.patch(
        f"/auth/users/{world.level3}", json={"role": "viewer"}, headers=world.admin
    )
    assert demoted.status_code == 200, demoted.text
    # A Viewer can read but not use operator endpoints.
    assert (await world.client.get("/incidents", headers=token)).status_code == 200
    assert (
        await world.client.get("/api/v1/api-tokens", headers=token)
    ).status_code == 403

    deactivated = await world.client.patch(
        f"/auth/users/{world.level3}", json={"is_active": False}, headers=world.admin
    )
    assert deactivated.status_code == 200, deactivated.text
    assert (await world.client.get("/incidents", headers=token)).status_code == 401
