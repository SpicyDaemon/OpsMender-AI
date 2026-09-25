"""Part 4 escalation progression and logical-page regressions."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from backend.api.schemas import RosterCreate, RosterUpdate
from backend.db.models import InAppNotification, IncidentComment, IncidentPage
from backend.db.repos import (
    EscalationChainRepo,
    EscalationStepRepo,
    IncidentChainStateRepo,
    IncidentPageRepo,
    IncidentRepo,
    RosterRepo,
    ServiceEscalationChainRepo,
    ServiceRepo,
    TeamRepo,
    UserRepo,
)
from backend.paging import escalation as esc
from backend.paging.dispatch import DeliveryAttempt
from tests.test_escalation import (
    TEST_ORG_ID,
    _make_team,
    _make_user,
    app as _base_app,
    client as _base_client,
    auth_headers as _base_auth_headers,
)


@pytest.fixture
async def app(tmp_path):
    async for application in _base_app.__wrapped__(tmp_path):
        yield application


@pytest.fixture
async def client(app):
    async for browser_client in _base_client.__wrapped__(app):
        yield browser_client


@pytest.fixture
async def auth_headers(client):
    return await _base_auth_headers.__wrapped__(client)


T0 = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


async def _chain(db, team_id, targets, *, timeout=300, indexes=None):
    chain = await EscalationChainRepo.create(
        db, TEST_ORG_ID, team_id=team_id, name=f"chain-{uuid.uuid4().hex[:8]}"
    )
    steps = []
    for position, target in enumerate(targets):
        steps.append(
            await EscalationStepRepo.create(
                db,
                TEST_ORG_ID,
                chain_id=chain.id,
                step_index=indexes[position] if indexes else position,
                target_type="user",
                target_id=target,
                timeout_seconds=timeout,
            )
        )
    return chain, steps


async def _incident(db, chain_id):
    incident = await IncidentRepo.create(
        db,
        TEST_ORG_ID,
        title="Part 4 progression",
        description="test",
    )
    await esc.start_chain(
        db,
        TEST_ORG_ID,
        incident_id=incident.id,
        chain_id=chain_id,
        at=T0,
    )
    return incident


async def _markers(db, incident_id):
    return (
        (
            await db.execute(
                select(IncidentPage)
                .where(
                    IncidentPage.incident_id == incident_id,
                    IncidentPage.channel == "recorded",
                )
                .order_by(IncidentPage.step_index)
            )
        )
        .scalars()
        .all()
    )


@pytest.mark.parametrize("timeout", [300, 900])
async def test_all_levels_reached_without_start_time_cap(app, timeout):
    team = await _make_team(app, name=f"progress-{timeout}")
    users = [
        await _make_user(app, username=f"progress-{timeout}-{i}") for i in range(4)
    ]
    async with app.state.session_factory() as db:
        chain, _ = await _chain(db, team, users, timeout=timeout)
        incident = await _incident(db, chain.id)
        await db.commit()
        for level in range(1, 4):
            await esc.tick(
                db,
                TEST_ORG_ID,
                incident_id=incident.id,
                at=T0 + timedelta(seconds=timeout * level),
            )
            await db.commit()
            assert [p.step_index for p in await _markers(db, incident.id)] == list(
                range(level + 1)
            )
        state = await IncidentChainStateRepo.get_for_incident(
            db, TEST_ORG_ID, incident.id
        )
        assert state.status == "running"
        assert esc._aware(state.next_step_due_at) == T0 + timedelta(seconds=timeout * 4)
        await esc.tick(
            db,
            TEST_ORG_ID,
            incident_id=incident.id,
            at=T0 + timedelta(seconds=timeout * 4),
        )
        await db.commit()
        assert state.status == "exhausted"


async def test_sparse_and_deleted_middle_step_preserve_cursor(app):
    team = await _make_team(app, name="delete-middle")
    users = [await _make_user(app, username=f"delete-{i}") for i in range(3)]
    async with app.state.session_factory() as db:
        chain, steps = await _chain(db, team, users, timeout=20, indexes=[0, 4, 9])
        incident = await _incident(db, chain.id)
        await db.commit()
        await EscalationStepRepo.delete(db, TEST_ORG_ID, steps[1].id)
        await db.commit()
        assert [
            s.step_index
            for s in await EscalationStepRepo.list_for_chain(db, TEST_ORG_ID, chain.id)
        ] == [0, 1]
        state = await IncidentChainStateRepo.get_for_incident(
            db, TEST_ORG_ID, incident.id
        )
        assert state.current_step_index == 0
        assert state.round == 1
        await esc.tick(
            db, TEST_ORG_ID, incident_id=incident.id, at=T0 + timedelta(seconds=20)
        )
        await db.commit()
        assert [(p.user_id, p.round) for p in await _markers(db, incident.id)] == [
            (users[0], 0),
            (users[2], 1),
        ]
        await esc.tick(
            db, TEST_ORG_ID, incident_id=incident.id, at=T0 + timedelta(seconds=40)
        )
        await db.commit()
        assert state.status == "exhausted"


async def test_deleted_current_step_advances_to_remaining_level(app):
    team = await _make_team(app, name="delete-current")
    users = [await _make_user(app, username=f"current-{i}") for i in range(3)]
    async with app.state.session_factory() as db:
        chain, steps = await _chain(db, team, users, timeout=20)
        incident = await _incident(db, chain.id)
        await db.commit()
        await esc.tick(
            db, TEST_ORG_ID, incident_id=incident.id, at=T0 + timedelta(seconds=20)
        )
        await db.commit()
        await EscalationStepRepo.delete(db, TEST_ORG_ID, steps[1].id)
        await db.commit()
        state = await IncidentChainStateRepo.get_for_incident(
            db, TEST_ORG_ID, incident.id
        )
        assert state.current_step_index == 0
        await esc.tick(
            db, TEST_ORG_ID, incident_id=incident.id, at=T0 + timedelta(seconds=40)
        )
        await db.commit()
        assert [p.user_id for p in await _markers(db, incident.id)] == users


@pytest.mark.parametrize("removed", [0, 2])
async def test_delete_first_or_final_during_active_run(app, removed):
    team = await _make_team(app, name=f"delete-edge-{removed}")
    users = [await _make_user(app, username=f"edge-{removed}-{i}") for i in range(3)]
    async with app.state.session_factory() as db:
        chain, steps = await _chain(db, team, users, timeout=20)
        incident = await _incident(db, chain.id)
        await db.commit()
        await EscalationStepRepo.delete(db, TEST_ORG_ID, steps[removed].id)
        await db.commit()
        await esc.tick(
            db, TEST_ORG_ID, incident_id=incident.id, at=T0 + timedelta(seconds=20)
        )
        await db.commit()
        assert {p.user_id for p in await _markers(db, incident.id)} == set(users[:2])
        await esc.tick(
            db, TEST_ORG_ID, incident_id=incident.id, at=T0 + timedelta(seconds=40)
        )
        await db.commit()
        if removed == 0:
            assert {p.user_id for p in await _markers(db, incident.id)} == set(users)
        else:
            state = await IncidentChainStateRepo.get_for_incident(
                db, TEST_ORG_ID, incident.id
            )
            assert state.status == "exhausted"


async def test_selection_priority_default_and_no_match(app):
    team = await _make_team(app, name="selection")
    async with app.state.session_factory() as db:
        svc = await ServiceRepo.create(
            db, TEST_ORG_ID, team_id=team, name="svc", slug="svc-selection"
        )
        a, _ = await _chain(db, team, [])
        b, _ = await _chain(db, team, [])
        default, _ = await _chain(db, team, [])
        link_b = await ServiceEscalationChainRepo.link(
            db,
            TEST_ORG_ID,
            service_id=svc.id,
            chain_id=b.id,
            applies_when={"priorities": ["P1"]},
        )
        link_a = await ServiceEscalationChainRepo.link(
            db,
            TEST_ORG_ID,
            service_id=svc.id,
            chain_id=a.id,
            applies_when={"priorities": ["P1"]},
        )
        await db.commit()
        expected = min((link_a, link_b), key=lambda link: link.id).chain_id
        for _ in range(3):
            link = await esc.select_chain_for_incident(
                db, TEST_ORG_ID, service_id=svc.id, priority="P1"
            )
            assert link.chain_id == expected
        assert (
            await esc.select_chain_for_incident(
                db, TEST_ORG_ID, service_id=svc.id, priority="P0"
            )
            is None
        )
        await ServiceEscalationChainRepo.link(
            db, TEST_ORG_ID, service_id=svc.id, chain_id=default.id
        )
        await db.commit()
        link = await esc.select_chain_for_incident(
            db, TEST_ORG_ID, service_id=svc.id, priority="P0"
        )
        assert link.chain_id == default.id
        assert (
            await esc.select_chain_for_incident(
                db, TEST_ORG_ID, service_id=None, priority="P1"
            )
            is None
        )


async def test_empty_levels_skip_and_single_exhaustion_notice(app):
    team = await _make_team(app, name="empty-levels")
    user_id = await _make_user(app, username="empty-inactive")
    async with app.state.session_factory() as db:
        user = await UserRepo.get_by_id(db, user_id)
        user.is_active = False
        chain, _ = await _chain(db, team, [user_id, uuid.uuid4()], timeout=20)
        incident = await _incident(db, chain.id)
        await db.commit()
        state = await IncidentChainStateRepo.get_for_incident(
            db, TEST_ORG_ID, incident.id
        )
        assert state.status == "exhausted"
        assert await _markers(db, incident.id) == []
        notified = state.exhaustion_notified_at
        for _ in range(2):
            await esc.tick(
                db, TEST_ORG_ID, incident_id=incident.id, at=T0 + timedelta(minutes=30)
            )
        await db.commit()
        assert state.exhaustion_notified_at == notified


async def test_team_and_roster_targets_drop_inactive_users(app):
    team = await _make_team(app, name="inactive-targets")
    user_id = await _make_user(app, username="inactive-target")
    async with app.state.session_factory() as db:
        await TeamRepo.add_member(db, TEST_ORG_ID, team, user_id=user_id)
        roster = await RosterRepo.create(
            db,
            TEST_ORG_ID,
            team_id=team,
            name="empty roster",
            anchor_date=T0.date(),
        )
        await RosterRepo.add_member(
            db,
            TEST_ORG_ID,
            roster_id=roster.id,
            user_id=user_id,
            position_index=0,
        )
        user = await UserRepo.get_by_id(db, user_id)
        user.is_active = False
        await db.commit()
        assert (
            await esc._resolve_step_targets(
                db, TEST_ORG_ID, target_type="team", target_id=team, at=T0
            )
            == []
        )
        assert (
            await esc._resolve_step_targets(
                db, TEST_ORG_ID, target_type="roster", target_id=roster.id, at=T0
            )
            == []
        )


async def test_final_timeout_notice_once_and_ack_lock_lapse(app):
    team = await _make_team(app, name="final-notice")
    user = await _make_user(app, username="notice-user")
    async with app.state.session_factory() as db:
        await TeamRepo.add_member(db, TEST_ORG_ID, team, user_id=user)
        chain, _ = await _chain(db, team, [user], timeout=20)
        incident = await _incident(db, chain.id)
        await db.commit()
        await esc.tick(
            db, TEST_ORG_ID, incident_id=incident.id, at=T0 + timedelta(seconds=20)
        )
        await esc.tick(
            db, TEST_ORG_ID, incident_id=incident.id, at=T0 + timedelta(seconds=40)
        )
        await db.commit()
        notices = (
            (
                await db.execute(
                    select(InAppNotification).where(
                        InAppNotification.incident_id == incident.id,
                        InAppNotification.event_type == "incident.escalation_exhausted",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(notices) == 1

        second = await _incident(db, chain.id)
        await esc.acknowledge(
            db,
            TEST_ORG_ID,
            incident_id=second.id,
            assignee_id=user,
            at=T0 + timedelta(seconds=19),
        )
        await db.commit()
        await esc.tick(
            db, TEST_ORG_ID, incident_id=second.id, at=T0 + timedelta(seconds=20)
        )
        state = await IncidentChainStateRepo.get_for_incident(
            db, TEST_ORG_ID, second.id
        )
        assert state.status == "acked"
        await esc.tick(
            db,
            TEST_ORG_ID,
            incident_id=second.id,
            at=T0 + timedelta(seconds=19 + esc.ACK_LOCK_INACTIVITY_SECONDS),
        )
        await db.commit()
        assert state.status == "exhausted"
        notices = (
            (
                await db.execute(
                    select(InAppNotification).where(
                        InAppNotification.incident_id == second.id,
                        InAppNotification.event_type == "incident.escalation_exhausted",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(notices) == 1


async def test_logical_marker_ignores_physical_rows_and_handoff_gets_new_round(app):
    team = await _make_team(app, name="round")
    user = await _make_user(app, username="round-user")
    async with app.state.session_factory() as db:
        chain, _ = await _chain(db, team, [user, user], timeout=20)
        incident = await _incident(db, chain.id)
        for channel in ("sms", "voice"):
            await IncidentPageRepo.create(
                db,
                TEST_ORG_ID,
                incident_id=incident.id,
                user_id=user,
                chain_id=chain.id,
                step_index=0,
                channel=channel,
                delivery_status="sent",
            )
        await db.commit()
        assert await IncidentPageRepo.already_paged(
            db,
            TEST_ORG_ID,
            incident_id=incident.id,
            user_id=user,
            step_index=0,
            round=0,
        )
        await esc.tick(
            db, TEST_ORG_ID, incident_id=incident.id, at=T0 + timedelta(seconds=20)
        )
        await db.commit()
        assert len(await _markers(db, incident.id)) == 2
        await esc.restart_chain_for_handoff(
            db,
            TEST_ORG_ID,
            incident_id=incident.id,
            chain_id=chain.id,
            at=T0 + timedelta(seconds=21),
        )
        await db.commit()
        markers = await _markers(db, incident.id)
        assert len(markers) == 3
        assert {(p.step_index, p.round) for p in markers} == {(0, 0), (1, 0), (0, 1)}


async def test_failed_due_chain_does_not_rollback_healthy_chain(app, monkeypatch):
    team = await _make_team(app, name="savepoint")
    users = [await _make_user(app, username=f"savepoint-{i}") for i in range(2)]
    async with app.state.session_factory() as db:
        chain, _ = await _chain(db, team, users, timeout=20)
        broken = await _incident(db, chain.id)
        healthy = await _incident(db, chain.id)
        await db.commit()
        original = esc._tick_state

        async def fail_one(*args, **kwargs):
            if kwargs["incident_id"] == broken.id:
                raise ValueError("malformed chain")
            return await original(*args, **kwargs)

        monkeypatch.setattr(esc, "_tick_state", fail_one)
        changed = await esc.tick_all_due(db, at=T0 + timedelta(seconds=20))
        await db.commit()
        assert changed == 1
        assert [p.step_index for p in await _markers(db, broken.id)] == [0]
        assert [p.step_index for p in await _markers(db, healthy.id)] == [0, 1]


async def test_unique_marker_conflict_stays_inside_savepoint(app, monkeypatch):
    team = await _make_team(app, name="marker-conflict")
    user = await _make_user(app, username="marker-conflict-user")
    async with app.state.session_factory() as db:
        chain, steps = await _chain(db, team, [user], timeout=20)
        incident = await _incident(db, chain.id)
        await db.commit()

        async def missed_existing(*args, **kwargs):
            return False

        monkeypatch.setattr(IncidentPageRepo, "already_paged", missed_existing)
        result = await esc._fire_step(
            db,
            TEST_ORG_ID,
            incident_id=incident.id,
            chain_id=chain.id,
            round=0,
            step=steps[0],
            at=T0 + timedelta(seconds=1),
        )
        assert result.users_paged == []
        other = await IncidentRepo.create(
            db, TEST_ORG_ID, title="transaction survived", description="test"
        )
        await db.commit()
        assert await IncidentRepo.get_by_id(db, TEST_ORG_ID, other.id) is not None
        assert len(await _markers(db, incident.id)) == 1


async def test_later_level_and_handoff_bypass_earlier_delivery_window(app):
    team = await _make_team(app, name="delivery-round")
    user = await _make_user(app, username="delivery-round-user")
    sent = []

    class FakeEmail:
        key = "email"

        async def send(self, *, recipient, subject, body, blocks=None):
            sent.append((recipient, subject))
            return DeliveryAttempt("email", "sent")

    channel = FakeEmail()

    def factory(key):
        return channel if key == "email" else None

    async with app.state.session_factory() as db:
        chain, steps = await _chain(db, team, [user, user], timeout=20)
        incident = await IncidentRepo.create(
            db, TEST_ORG_ID, title="delivery", description="test"
        )
        await esc.start_chain(
            db,
            TEST_ORG_ID,
            incident_id=incident.id,
            chain_id=chain.id,
            at=T0,
            channel_factory=factory,
        )
        await db.commit()
        await esc.tick(
            db,
            TEST_ORG_ID,
            incident_id=incident.id,
            at=T0 + timedelta(seconds=20),
            channel_factory=factory,
        )
        await db.commit()
        await esc._fire_step(
            db,
            TEST_ORG_ID,
            incident_id=incident.id,
            chain_id=chain.id,
            round=0,
            step=steps[1],
            at=T0 + timedelta(seconds=21),
            channel_factory=factory,
        )
        await db.commit()
        await esc.restart_chain_for_handoff(
            db,
            TEST_ORG_ID,
            incident_id=incident.id,
            chain_id=chain.id,
            at=T0 + timedelta(seconds=22),
            channel_factory=factory,
        )
        await db.commit()
        assert len(sent) == 3
        assert (
            len(
                [
                    p
                    for p in await IncidentPageRepo.list_for_incident(
                        db, TEST_ORG_ID, incident.id
                    )
                    if p.channel == "email" and p.delivery_status == "sent"
                ]
            )
            == 3
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("time_zone", "Mars/Olympus"),
        ("coverage_start_time", "24:00"),
        ("coverage_end_time", "12:60"),
        ("handoff_time", "bad"),
    ],
)
def test_roster_rejects_invalid_zone_or_time(field, value):
    with pytest.raises(ValidationError):
        RosterUpdate(**{field: value})


def test_roster_accepts_boundaries_and_overnight():
    roster = RosterCreate(
        team_id=uuid.uuid4(),
        name="overnight",
        anchor_date=T0.date(),
        time_zone="America/Chicago",
        coverage_start_time="23:59",
        coverage_end_time="00:00",
        handoff_time="00:00",
    )
    assert roster.coverage_start_time == "23:59"


async def test_api_records_no_matching_chain_warning(app, client, auth_headers):
    team = await _make_team(app, name="api-no-match")
    async with app.state.session_factory() as db:
        service = await ServiceRepo.create(
            db,
            TEST_ORG_ID,
            team_id=team,
            name="no-match",
            slug="no-match",
            priority="P1",
        )
        chain, _ = await _chain(db, team, [])
        await ServiceEscalationChainRepo.link(
            db,
            TEST_ORG_ID,
            service_id=service.id,
            chain_id=chain.id,
            applies_when={"priorities": ["P0"]},
        )
        await db.commit()
    response = await client.post(
        "/incidents",
        json={
            "title": "No match",
            "description": "warning proof",
            "severity": "high",
            "service_id": str(service.id),
        },
        headers=auth_headers,
    )
    assert response.status_code == 201, response.text
    incident_id = uuid.UUID(response.json()["id"])
    async with app.state.session_factory() as db:
        assert (
            await IncidentChainStateRepo.get_for_incident(db, TEST_ORG_ID, incident_id)
            is None
        )
        comments = (
            (
                await db.execute(
                    select(IncidentComment).where(
                        IncidentComment.incident_id == incident_id,
                    )
                )
            )
            .scalars()
            .all()
        )
        assert any("No escalation chain matches" in item.body for item in comments)


async def test_roster_api_rejects_invalid_time_and_accepts_overnight(
    app, client, auth_headers
):
    team = await _make_team(app, name="roster-api")
    base = {
        "team_id": str(team),
        "name": "overnight",
        "anchor_date": "2026-09-25",
        "time_zone": "America/Chicago",
        "coverage_start_time": "23:59",
        "coverage_end_time": "00:00",
        "handoff_time": "00:00",
    }
    invalid = await client.post(
        "/rosters", json={**base, "coverage_end_time": "24:00"}, headers=auth_headers
    )
    assert invalid.status_code == 422
    created = await client.post("/rosters", json=base, headers=auth_headers)
    assert created.status_code == 201, created.text
    # handoff_time mirrors the coverage start until X1c defines handoffs.
    assert created.json()["handoff_time"] == "23:59"
    rejected = await client.put(
        f"/rosters/{created.json()['id']}",
        json={"time_zone": "Mars/Olympus"},
        headers=auth_headers,
    )
    assert rejected.status_code == 422
    unchanged = await client.put(
        f"/rosters/{created.json()['id']}",
        json={"time_zone": None, "coverage_end_time": None, "handoff_time": None},
        headers=auth_headers,
    )
    assert unchanged.status_code == 200, unchanged.text
    assert unchanged.json()["time_zone"] == "America/Chicago"
    assert unchanged.json()["coverage_end_time"] == "00:00"


async def test_exhaustion_inbox_notice_respects_muted_category(app):
    from backend.db.repos import UserNotificationPrefRepo

    team = await _make_team(app, name="exhaust-mute")
    muted = await _make_user(app, username="exhaust-muted")
    listening = await _make_user(app, username="exhaust-listening")
    async with app.state.session_factory() as db:
        await UserNotificationPrefRepo.upsert(
            db,
            TEST_ORG_ID,
            muted,
            routing={"in_app": {"muted_categories": ["incident"]}},
        )
        chain, _ = await _chain(db, team, [muted, listening], timeout=20)
        incident = await _incident(db, chain.id)
        for seconds in (20, 40):
            await esc.tick(
                db,
                TEST_ORG_ID,
                incident_id=incident.id,
                at=T0 + timedelta(seconds=seconds),
            )
        await db.commit()
        notices = (
            (
                await db.execute(
                    select(InAppNotification).where(
                        InAppNotification.incident_id == incident.id,
                        InAppNotification.event_type == "incident.escalation_exhausted",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert [notice.user_id for notice in notices] == [listening]
