"""Secure foundation tests for future native incident actions."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.bots.actions import (
    ExternalActorIdentity,
    IncidentActionError,
    VerifiedNativeAction,
    execute_incident_action,
    execute_verified_native_action,
    make_incident_action_token,
    verify_incident_action_token,
)
from backend.db.models import Base
from backend.db.repos import (
    BotActionAuditRepo,
    BotConnectorRepo,
    BotUserLinkRepo,
    IncidentAssignmentRepo,
    IncidentRepo,
    NativeActionInvocationRepo,
    SessionRepo,
    UserRepo,
)


TEST_ORG_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")
SECRET = "test-action-secret"


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _user(db, *, username: str, role: str = "operator", is_active: bool = True):
    user = await UserRepo.create(
        db,
        username=username,
        email=f"{username}@example.com",
        password_hash="x",
        role=role,
    )
    await UserRepo.add_to_organization(db, user.id, TEST_ORG_ID, role=role)
    if not is_active:
        await UserRepo.update_fields(db, user.id, is_active=False)
    return user


def _claims(incident_id: uuid.UUID, action: str = "acknowledge"):
    token = make_incident_action_token(
        secret=SECRET,
        org_id=TEST_ORG_ID,
        incident_id=incident_id,
        action=action,
        channel_id="C123",
        message_id="M123",
    )
    return verify_incident_action_token(token, secret=SECRET)


def test_incident_action_token_rejects_tampering_and_expiry():
    incident_id = uuid.uuid4()
    token = make_incident_action_token(
        secret=SECRET,
        org_id=TEST_ORG_ID,
        incident_id=incident_id,
        action="resolve",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    claims = verify_incident_action_token(token, secret=SECRET, expected_action="resolve")
    assert claims.incident_id == incident_id
    assert claims.action == "resolve"

    with pytest.raises(IncidentActionError, match="invalid_action_token"):
        verify_incident_action_token(token + "x", secret=SECRET)

    expired = make_incident_action_token(
        secret=SECRET,
        org_id=TEST_ORG_ID,
        incident_id=incident_id,
        action="resolve",
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    with pytest.raises(IncidentActionError, match="expired_action_token"):
        verify_incident_action_token(expired, secret=SECRET)


async def test_token_alone_cannot_mutate_incident(factory):
    async with factory() as db:
        incident = await IncidentRepo.create(
            db,
            TEST_ORG_ID,
            title="Cache outage",
            description="latency spike",
            severity="high",
        )
        claims = _claims(incident.id)
        with pytest.raises(IncidentActionError, match="actor_required"):
            await execute_incident_action(db, claims=claims)


async def test_operator_can_acknowledge_and_viewer_is_rejected(factory):
    async with factory() as db:
        operator = await _user(db, username="op", role="operator")
        viewer = await _user(db, username="view", role="viewer")
        incident = await IncidentRepo.create(
            db,
            TEST_ORG_ID,
            title="API outage",
            description="500s",
            severity="critical",
        )

        with pytest.raises(IncidentActionError, match="actor_not_authorized"):
            await execute_incident_action(
                db,
                claims=_claims(incident.id),
                actor_user_id=viewer.id,
            )

        result = await execute_incident_action(
            db,
            claims=_claims(incident.id),
            actor_user_id=operator.id,
        )
        assert result.status == "acknowledged"
        assignment = await IncidentAssignmentRepo.get_active(db, TEST_ORG_ID, incident.id)
        assert assignment is not None
        assert assignment.assigned_to == operator.id

        again = await execute_incident_action(
            db,
            claims=_claims(incident.id),
            actor_user_id=operator.id,
        )
        assert again.status == "already_acknowledged"


async def test_resolve_and_start_ai_session_are_idempotent(factory):
    async with factory() as db:
        operator = await _user(db, username="solver", role="operator")
        incident = await IncidentRepo.create(
            db,
            TEST_ORG_ID,
            title="Worker outage",
            description="queue stalled",
            severity="medium",
        )

        resolved = await execute_incident_action(
            db,
            claims=_claims(incident.id, action="resolve"),
            actor_user_id=operator.id,
        )
        assert resolved.status == "resolved"
        resolved_again = await execute_incident_action(
            db,
            claims=_claims(incident.id, action="resolve"),
            actor_user_id=operator.id,
        )
        assert resolved_again.status == "already_resolved"

        started = await execute_incident_action(
            db,
            claims=_claims(incident.id, action="start_ai_session"),
            actor_user_id=operator.id,
        )
        assert started.status == "session_started"
        active = await SessionRepo.list_by_incident(db, TEST_ORG_ID, incident.id)
        assert len(active) == 1
        assert active[0].tier == 2

        started_again = await execute_incident_action(
            db,
            claims=_claims(incident.id, action="start_ai_session"),
            actor_user_id=operator.id,
        )
        assert started_again.status == "already_active"
        assert started_again.session_id == started.session_id


async def test_external_actor_must_be_linked_and_active(factory):
    async with factory() as db:
        connector = await BotConnectorRepo.create(
            db,
            TEST_ORG_ID,
            name="slack-actions",
            platform="slack",
            config={"allowed_chat_ids": ["C123"]},
            credentials={},
            allowed_capabilities=["notifications"],
            status="configured",
            is_enabled=True,
        )
        inactive = await _user(db, username="inactive", role="operator", is_active=False)
        operator = await _user(db, username="linked", role="operator")
        incident = await IncidentRepo.create(
            db,
            TEST_ORG_ID,
            title="DB outage",
            description="pool exhausted",
            severity="high",
        )

        with pytest.raises(IncidentActionError, match="actor_not_linked"):
            await execute_incident_action(
                db,
                claims=_claims(incident.id),
                connector=connector,
                external_actor=ExternalActorIdentity(platform_user_id="U-missing"),
            )

        await BotUserLinkRepo.create(
            db,
            TEST_ORG_ID,
            connector_id=connector.id,
            platform_user_id="U-inactive",
            opsmender_user_id=inactive.id,
        )
        with pytest.raises(IncidentActionError, match="actor_not_active"):
            await execute_incident_action(
                db,
                claims=_claims(incident.id),
                connector=connector,
                external_actor=ExternalActorIdentity(platform_user_id="U-inactive"),
            )

        await BotUserLinkRepo.create(
            db,
            TEST_ORG_ID,
            connector_id=connector.id,
            platform_user_id="U-linked",
            opsmender_user_id=operator.id,
        )
        result = await execute_incident_action(
            db,
            claims=_claims(incident.id),
            connector=connector,
            external_actor=ExternalActorIdentity(platform_user_id="U-linked"),
        )
        assert result.status == "acknowledged"


async def test_verified_native_action_is_idempotent_and_audited(factory):
    async with factory() as db:
        connector = await BotConnectorRepo.create(
            db,
            TEST_ORG_ID,
            name="slack-native-actions",
            platform="slack",
            credentials={"signing_secret": "secret"},
            allowed_capabilities=["notifications"],
            status="healthy",
            is_enabled=True,
        )
        connector.native_actions_enabled = True
        connector.callback_status = "verified"
        operator = await _user(db, username="native-op", role="operator")
        await BotUserLinkRepo.create(
            db,
            TEST_ORG_ID,
            connector_id=connector.id,
            platform_user_id="U-native",
            opsmender_user_id=operator.id,
        )
        incident = await IncidentRepo.create(
            db,
            TEST_ORG_ID,
            title="Native callback outage",
            description="timeouts",
            severity="high",
        )
        request = VerifiedNativeAction(
            connector=connector,
            claims=_claims(incident.id),
            external_actor=ExternalActorIdentity(
                platform_user_id="U-native",
                username="native.user",
                display_name="Native User",
            ),
            idempotency_key="slack-action-123",
            callback_received_at=datetime.now(timezone.utc),
            chat_id="C123",
        )

        first = await execute_verified_native_action(db, request=request)
        await db.commit()
        second = await execute_verified_native_action(db, request=request)
        await db.commit()

        assert first.status == "acknowledged"
        assert second.status == "acknowledged"
        assert second.detail == "deduplicated"
        assignments = await IncidentAssignmentRepo.list_for_incident(
            db, TEST_ORG_ID, incident.id
        )
        assert len(assignments) == 1

        invocation = await NativeActionInvocationRepo.get_by_key(
            db, TEST_ORG_ID, connector.id, "slack-action-123"
        )
        assert invocation is not None
        assert invocation.status == "applied"
        assert invocation.actor_user_id == operator.id
        assert invocation.result_status == "acknowledged"

        link = await BotUserLinkRepo.get_by_platform_user(
            db,
            TEST_ORG_ID,
            connector_id=connector.id,
            platform_user_id="U-native",
        )
        assert link is not None
        assert link.last_seen_at is not None
        assert link.external_username == "native.user"
        assert link.external_display_name == "Native User"

        audits = await BotActionAuditRepo.list_by_connector(
            db, TEST_ORG_ID, connector.id
        )
        assert {row.status for row in audits} == {
            "callback_verified",
            "native_action_applied",
            "native_action_deduplicated",
        }
        assert all(row.incident_id == incident.id for row in audits)


async def test_native_action_requires_enabled_verified_callback(factory):
    async with factory() as db:
        connector = await BotConnectorRepo.create(
            db,
            TEST_ORG_ID,
            name="slack-disabled-actions",
            platform="slack",
            credentials={"signing_secret": "secret"},
            allowed_capabilities=["notifications"],
            status="healthy",
            is_enabled=True,
        )
        incident = await IncidentRepo.create(
            db,
            TEST_ORG_ID,
            title="No callback execution",
            description="must stay open",
            severity="medium",
        )
        request = VerifiedNativeAction(
            connector=connector,
            claims=_claims(incident.id),
            external_actor=ExternalActorIdentity(platform_user_id="U1"),
            idempotency_key="disabled-1",
            callback_received_at=datetime.now(timezone.utc),
        )

        with pytest.raises(IncidentActionError, match="native_actions_disabled"):
            await execute_verified_native_action(db, request=request)

        connector.native_actions_enabled = True
        with pytest.raises(IncidentActionError, match="callback_not_verified"):
            await execute_verified_native_action(db, request=request)

        invocation = await NativeActionInvocationRepo.get_by_key(
            db, TEST_ORG_ID, connector.id, "disabled-1"
        )
        assert invocation is None


async def test_unmapped_verified_callback_is_rejected_and_recorded(factory):
    async with factory() as db:
        connector = await BotConnectorRepo.create(
            db,
            TEST_ORG_ID,
            name="telegram-native-actions",
            platform="telegram",
            credentials={"webhook_secret": "secret"},
            allowed_capabilities=["notifications"],
            status="healthy",
            is_enabled=True,
        )
        connector.native_actions_enabled = True
        connector.callback_status = "verified"
        incident = await IncidentRepo.create(
            db,
            TEST_ORG_ID,
            title="Unmapped callback",
            description="must be rejected",
            severity="high",
        )
        request = VerifiedNativeAction(
            connector=connector,
            claims=_claims(incident.id, action="resolve"),
            external_actor=ExternalActorIdentity(platform_user_id="TG-missing"),
            idempotency_key="telegram-action-404",
            callback_received_at=datetime.now(timezone.utc),
            chat_id="-1001",
        )

        with pytest.raises(IncidentActionError, match="actor_not_linked"):
            await execute_verified_native_action(db, request=request)

        invocation = await NativeActionInvocationRepo.get_by_key(
            db, TEST_ORG_ID, connector.id, "telegram-action-404"
        )
        assert invocation is not None
        assert invocation.status == "rejected"
        assert invocation.error_code == "actor_not_linked"
        audits = await BotActionAuditRepo.list_by_connector(
            db, TEST_ORG_ID, connector.id
        )
        assert {row.status for row in audits} == {
            "callback_verified",
            "native_action_rejected",
        }
