"""API tokens act at their own role, capped by the creator's current role (EC-P05).

Permission checks after the role dependency use the request's role, never the
creator's account role: an operator-role token minted by an admin is an
operator everywhere, acting as its creator's identity. Demoting the creator
lowers the token. Operators cannot create global memories either (EC-P06).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from backend.auth.roles import lower_role, request_role
from backend.db.models import (
    Incident,
    IncidentComment,
    IncidentMemory,
    IncidentResponder,
)
from backend.db.repos import TeamRepo
from tests.test_ownership_lifecycle import (
    TEST_ORG_ID,
    World,
    _headers,
    _owner,
    _user,
    app as _app_fixture,
    client as _client_fixture,
    world as _world_fixture,
)
from tests.test_reassign_responders_part15 import _incident_on, _own, _team

app = pytest.fixture(_app_fixture.__wrapped__)
client = pytest.fixture(_client_fixture.__wrapped__)
world = pytest.fixture(_world_fixture.__wrapped__)


async def _token(
    w: World, role: str, headers: dict[str, str] | None = None
) -> dict[str, str]:
    resp = await w.client.post(
        "/api/v1/api-tokens",
        json={"name": f"scope-{role}-{uuid.uuid4().hex[:6]}", "role": role},
        headers=headers or w.admin,
    )
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


async def _incident(w: World, incident_id: uuid.UUID) -> Incident:
    async with w.app.state.session_factory() as db:
        return await db.get(Incident, incident_id)


async def _count(w: World, model, *where) -> int:
    async with w.app.state.session_factory() as db:
        return await db.scalar(select(func.count()).select_from(model).where(*where))


def test_lower_role_orders_roles_and_fails_closed():
    assert lower_role("admin", "operator") == "operator"
    assert lower_role("operator", "admin") == "operator"
    assert lower_role("viewer", "admin") == "viewer"
    # An unknown role ranks lowest, so no role dependency accepts it.
    assert lower_role("operator", "retired") == "retired"

    class Person:
        role = "admin"

    person = Person()
    assert request_role(person) == "admin"
    person.effective_role = "operator"
    assert request_role(person) == "operator"
    # A set but unusable request role never falls back to the account role.
    person.effective_role = ""
    assert request_role(person) == ""


async def test_operator_token_cannot_permanently_delete(world):
    _, service_a, _ = await _team(world, "Platform")
    incident_id = await _incident_on(world, service_a, None, priority="P2")
    operator = await _token(world, "operator")

    bulk = await world.client.post(
        "/incidents/bulk",
        json={"action": "delete", "incident_ids": [str(incident_id)]},
        headers=operator,
    )
    single = await world.client.delete(f"/incidents/{incident_id}", headers=operator)
    assert (bulk.status_code, single.status_code) == (403, 403)
    assert await _incident(world, incident_id) is not None

    admin = await _token(world, "admin")
    deleted = await world.client.post(
        "/incidents/bulk",
        json={"action": "delete", "incident_ids": [str(incident_id)]},
        headers=admin,
    )
    assert deleted.status_code == 200, deleted.text
    assert await _incident(world, incident_id) is None


async def test_operator_token_is_off_team_like_its_creator(world):
    a1 = await _user(world.app, "ts-a1")
    b1 = await _user(world.app, "ts-b1")
    _, service_a, _ = await _team(world, "Platform", members=[a1])
    team_b, service_b, _ = await _team(world, "Data", members=[b1])
    incident_id = await _incident_on(world, service_a, None, priority="P2")
    await _own(world, incident_id, a1)
    operator = await _token(world, "operator")

    reassign = await world.client.post(
        f"/incidents/{incident_id}/reassign",
        json={"team_id": str(team_b), "note": "Data owns it"},
        headers=operator,
    )
    responders = await world.client.post(
        f"/incidents/{incident_id}/responders",
        json={"user_ids": [str(b1)]},
        headers=operator,
    )
    force = await world.client.post(
        f"/incidents/{incident_id}/take",
        json={"force": True, "reason": "Owner is unreachable"},
        headers=operator,
    )
    moved = await world.client.patch(
        f"/incidents/{incident_id}",
        json={"service_id": str(service_b), "service_id_set": True},
        headers=operator,
    )
    assert [r.status_code for r in (reassign, responders, force, moved)] == [403] * 4

    row = await _incident(world, incident_id)
    assert row.team_id is None and row.service_id == service_a
    assert await _owner(world.app, incident_id) == a1
    assert (
        await _count(
            world, IncidentResponder, IncidentResponder.incident_id == incident_id
        )
        == 0
    )

    panel = (
        await world.client.get(f"/incidents/{incident_id}/paging", headers=operator)
    ).json()
    assert (
        panel["can_reassign"],
        panel["can_manage_responders"],
        panel["can_force_take"],
    ) == (False, False, False)


async def test_operator_token_acts_on_its_creators_team(world):
    lead = await _user(world.app, "ts-lead", role="admin")
    _, service_a, _ = await _team(world, "Platform", members=[lead])
    team_b, _, _ = await _team(world, "Data")
    incident_id = await _incident_on(world, service_a, None, priority="P2")
    operator = await _token(
        world, "operator", headers=await _headers(world.client, "ts-lead")
    )

    moved = await world.client.post(
        f"/incidents/{incident_id}/reassign",
        json={"team_id": str(team_b), "note": "Data owns the orders database"},
        headers=operator,
    )
    assert moved.status_code == 200, moved.text
    assert (await _incident(world, incident_id)).team_id == team_b


async def test_operator_token_cannot_delete_another_authors_comment(world):
    a1 = await _user(world.app, "ts-author")
    _, service_a, _ = await _team(world, "Platform", members=[a1])
    incident_id = await _incident_on(world, service_a, None, priority="P2")
    async with world.app.state.session_factory() as db:
        comment = IncidentComment(
            org_id=TEST_ORG_ID,
            incident_id=incident_id,
            author_user_id=a1,
            body="Owner note",
        )
        db.add(comment)
        await db.commit()
        comment_id = comment.id

    operator = await _token(world, "operator")
    refused = await world.client.delete(
        f"/incidents/{incident_id}/comments/{comment_id}", headers=operator
    )
    assert refused.status_code == 403
    assert await _count(world, IncidentComment, IncidentComment.id == comment_id) == 1

    admin = await _token(world, "admin")
    removed = await world.client.delete(
        f"/incidents/{incident_id}/comments/{comment_id}", headers=admin
    )
    assert removed.status_code == 204
    assert await _count(world, IncidentComment, IncidentComment.id == comment_id) == 0


async def test_operator_token_maintenance_waits_for_an_admin(world):
    now = datetime.now(timezone.utc)
    window = {
        "name": "Database upgrade",
        "scope_type": "global",
        "starts_at": (now - timedelta(minutes=1)).isoformat(),
        "ends_at": (now + timedelta(hours=1)).isoformat(),
    }
    operator = await _token(world, "operator")
    requested = await world.client.post(
        "/maintenance-windows", json=window, headers=operator
    )
    assert requested.status_code == 201, requested.text
    assert requested.json()["approved"] is False

    admin = await _token(world, "admin")
    approved = await world.client.post(
        "/maintenance-windows", json=window, headers=admin
    )
    assert approved.status_code == 201, approved.text
    assert approved.json()["approved"] is True


async def test_operator_token_cannot_resolve_across_services(world):
    _, service_a, _ = await _team(world, "Platform")
    _, service_b, _ = await _team(world, "Data")
    first = await _incident_on(world, service_a, None, priority="P2")
    second = await _incident_on(world, service_b, None, priority="P2")
    operator = await _token(world, "operator")

    resp = await world.client.post(
        "/incidents/bulk",
        json={"action": "resolve", "incident_ids": [str(first), str(second)]},
        headers=operator,
    )
    assert resp.status_code == 403
    assert [(await _incident(world, i)).status for i in (first, second)] == [
        "open",
        "open",
    ]


async def test_token_role_follows_its_creators_demotion(world):
    creator = await _user(world.app, "ts-creator", role="admin")
    _, service_a, _ = await _team(world, "Platform")
    incident_id = await _incident_on(world, service_a, None, priority="P2")
    token = await _token(
        world, "admin", headers=await _headers(world.client, "ts-creator")
    )
    assert (
        await world.client.get("/api/v1/api-tokens", headers=token)
    ).status_code == 200

    demoted = await world.client.patch(
        f"/auth/users/{creator}", json={"role": "operator"}, headers=world.admin
    )
    assert demoted.status_code == 200, demoted.text
    assert (
        await world.client.get("/api/v1/api-tokens", headers=token)
    ).status_code == 403
    assert (await world.client.get("/incidents", headers=token)).status_code == 200

    demoted = await world.client.patch(
        f"/auth/users/{creator}", json={"role": "viewer"}, headers=world.admin
    )
    assert demoted.status_code == 200, demoted.text
    comment = await world.client.post(
        f"/incidents/{incident_id}/comments",
        json={"body": "Read only now"},
        headers=token,
    )
    assert comment.status_code == 403
    assert (
        await _count(world, IncidentComment, IncidentComment.incident_id == incident_id)
        == 0
    )
    assert (
        await world.client.get(f"/incidents/{incident_id}", headers=token)
    ).status_code == 200


async def test_operators_cannot_create_global_memories(world):
    a1 = await _user(world.app, "ts-curator")
    _, service_a, _ = await _team(world, "Platform", members=[a1])
    operator_login = await _headers(world.client, "ts-curator")
    operator_token = await _token(world, "operator")

    def memory(service_id):
        return {
            "title": "Restart the checkout workers",
            "summary_md": "Clearing the stuck queue fixed it.",
            "service_id": None if service_id is None else str(service_id),
        }

    for headers in (operator_login, operator_token):
        refused = await world.client.post(
            "/memories", json=memory(None), headers=headers
        )
        assert refused.status_code == 403, refused.text
    assert await _count(world, IncidentMemory, IncidentMemory.service_id.is_(None)) == 0

    own_team = await world.client.post(
        "/memories", json=memory(service_a), headers=operator_login
    )
    assert own_team.status_code == 201, own_team.text
    global_by_admin = await world.client.post(
        "/memories", json=memory(None), headers=world.admin
    )
    assert global_by_admin.status_code == 201, global_by_admin.text
    assert await _count(world, IncidentMemory, IncidentMemory.service_id.is_(None)) == 1


async def test_team_membership_is_checked_for_the_token_creator(world):
    """A member operator's own login and an outsider's operator token differ."""

    a1 = await _user(world.app, "ts-member")
    team_a, service_a, _ = await _team(world, "Platform", members=[a1])
    team_b, _, _ = await _team(world, "Data")
    incident_id = await _incident_on(world, service_a, None, priority="P2")
    async with world.app.state.session_factory() as db:
        assert await TeamRepo.is_member(db, TEST_ORG_ID, team_a, a1)

    outsider = await _token(world, "operator")
    refused = await world.client.post(
        f"/incidents/{incident_id}/reassign",
        json={"team_id": str(team_b), "note": "Data owns it"},
        headers=outsider,
    )
    assert refused.status_code == 403
    member = await world.client.post(
        f"/incidents/{incident_id}/reassign",
        json={"team_id": str(team_b), "note": "Data owns it"},
        headers=await _headers(world.client, "ts-member"),
    )
    assert member.status_code == 200, member.text
