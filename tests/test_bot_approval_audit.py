"""M1-123 (L-03): the bot audit of a chat decision follows the decision's
outcome, not the words of the reply.

The live round's repeated ``/approve`` answered "already approved", ran
nothing twice and was still audited ``ok``, because the dispatcher looked
for the word "approved" in the reply. The audit now says ``ok`` only for a
decision this command made, ``noop`` for a repeat, an expired or an
unresolvable request, ``not_found`` for an unknown id and ``role_denied``
when the person may not decide it.
"""

# The app, client and auth fixtures come from the API tests.
# ruff: noqa: F811

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from httpx import AsyncClient

from backend.db.models import BotActionAudit
from backend.db.repos import ApprovalRequestRepo, SessionRepo, UserRepo
from tests.test_api import TEST_ORG_ID, app, auth_headers, client  # noqa: F401


async def _connector(client: AsyncClient, auth_headers) -> str:
    resp = await client.post(
        "/bot-connectors",
        json={
            "name": f"telegram-{uuid.uuid4()}",
            "platform": "telegram",
            "config": {},
            "credentials": {
                "bot_token": "secret-token",
                "webhook_secret": "telegram-secret",
            },
            "allowed_capabilities": ["approvals"],
            "is_enabled": True,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201
    return resp.json()["id"]


async def _link_admin(
    client: AsyncClient, app, auth_headers, connector_id: str
) -> None:
    async with app.state.session_factory() as db:
        admin = await UserRepo.get_by_username(db, "testadmin")
    resp = await client.post(
        f"/bot-connectors/{connector_id}/user-links",
        json={"platform_user_id": "111", "opsmender_user_id": str(admin.id)},
        headers=auth_headers,
    )
    assert resp.status_code == 201


async def _pending_request(app, *, minutes: int = 15) -> str:
    async with app.state.session_factory() as db:
        session = await SessionRepo.create(db, TEST_ORG_ID, tier=1)
        await SessionRepo.set_status(
            db, TEST_ORG_ID, session.id, status="awaiting_approval"
        )
        request = await ApprovalRequestRepo.create(
            db,
            TEST_ORG_ID,
            session_id=session.id,
            action={
                "tool_name": "restart_service",
                "parameters": {"service": "ratings"},
            },
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=minutes),
        )
        await db.commit()
        return str(request.id)


async def _send(client: AsyncClient, connector_id: str, text: str) -> str:
    resp = await client.post(
        f"/bot-connectors/{connector_id}/telegram/webhook",
        json={
            "message": {"from": {"id": 111}, "chat": {"id": "-100123"}, "text": text}
        },
        headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
    )
    assert resp.status_code == 200
    return resp.json()["text"]


async def _audits(app, connector_id: str) -> list[tuple[str, str, str | None]]:
    from sqlalchemy import select

    async with app.state.session_factory() as db:
        rows = (
            (
                await db.execute(
                    select(BotActionAudit)
                    .where(BotActionAudit.connector_id == uuid.UUID(connector_id))
                    .where(BotActionAudit.command.in_(["/approve", "/reject"]))
                    .order_by(BotActionAudit.created_at)
                )
            )
            .scalars()
            .all()
        )
    return [(row.command, row.status, row.detail) for row in rows]


async def _status(app, request_id: str) -> tuple[str, int]:
    async with app.state.session_factory() as db:
        request = await ApprovalRequestRepo.get_by_id(
            db, TEST_ORG_ID, uuid.UUID(request_id)
        )
        return request.status, request.resolved_at is not None


async def test_a_repeated_decision_is_audited_as_noop_and_decides_nothing_twice(
    client: AsyncClient, app, auth_headers
):
    from backend.bots.rate_limit import rate_limiter

    rate_limiter.reset()
    connector_id = await _connector(client, auth_headers)
    await _link_admin(client, app, auth_headers, connector_id)
    request_id = await _pending_request(app)

    first = await _send(client, connector_id, f"/approve {request_id}")
    assert "approved" in first
    status, resolved = await _status(app, request_id)
    assert (status, resolved) == ("approved", True)

    second = await _send(client, connector_id, f"/approve {request_id}")
    assert "already approved" in second
    third = await _send(client, connector_id, f"/reject {request_id}")
    assert "already approved" in third
    assert await _status(app, request_id) == ("approved", True)

    assert await _audits(app, connector_id) == [
        ("/approve", "ok", "approved"),
        ("/approve", "noop", "approved: already approved"),
        ("/reject", "noop", "rejected: already approved"),
    ]


async def test_an_unknown_request_is_audited_as_not_found(
    client: AsyncClient, app, auth_headers
):
    from backend.bots.rate_limit import rate_limiter

    rate_limiter.reset()
    connector_id = await _connector(client, auth_headers)
    await _link_admin(client, app, auth_headers, connector_id)

    reply = await _send(client, connector_id, f"/approve {uuid.uuid4()}")

    assert reply == "Approval request not found."
    assert await _audits(app, connector_id) == [("/approve", "not_found", "approved")]


async def test_an_expired_request_is_audited_as_noop(
    client: AsyncClient, app, auth_headers
):
    from backend.bots.rate_limit import rate_limiter

    rate_limiter.reset()
    connector_id = await _connector(client, auth_headers)
    await _link_admin(client, app, auth_headers, connector_id)
    request_id = await _pending_request(app, minutes=-1)

    reply = await _send(client, connector_id, f"/reject {request_id}")

    assert "expired" in reply
    assert await _status(app, request_id) == ("expired", True)
    assert await _audits(app, connector_id) == [
        ("/reject", "noop", "rejected: expired")
    ]
