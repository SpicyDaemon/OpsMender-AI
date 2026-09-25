"""Who's on call (X1c): one shared context, shifts that switch at the handoff
time, a Services "on call now" that means now, D-4 quiet hours, and validated
notification preferences."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from backend.db.models import IncidentComment, IncidentPage
from backend.db.repos import (
    EscalationChainRepo,
    EscalationStepRepo,
    IncidentRepo,
    MaintenanceWindowRepo,
    RosterOverrideRepo,
    RosterRepo,
    UserNotificationPrefRepo,
    UserRepo,
)
from backend.paging import escalation as esc
from backend.paging.dispatch import DeliveryAttempt
from backend.paging.on_call import OnCallContext, OnCallMember, on_call_at
from tests.test_escalation import (
    TEST_ORG_ID,
    _make_team,
    _make_user,
    app as _base_app,
    auth_headers as _base_auth_headers,
    client as _base_client,
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


A, B, C = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
MONDAY = date(2026, 9, 21)


def _ctx(start, end, *, members=(A, B), tz="UTC", pattern="daily", anchor=MONDAY):
    return OnCallContext(
        members=[
            OnCallMember(user_id=u, position_index=i) for i, u in enumerate(members)
        ],
        time_zone=tz,
        pattern=pattern,
        pattern_length=7 if pattern == "weekly" else 1,
        coverage_start_time=start,
        coverage_end_time=end,
        handoff_time=start,
        anchor_date=anchor,
    )


def _at(day: int, hh: int, mm: int, tz="UTC", month=9, year=2026) -> datetime:
    return datetime(year, month, day, hh, mm, tzinfo=ZoneInfo(tz))


# ── R01–R04: the shift boundary is the handoff time ─────────────────────────


def test_r01_round_the_clock_roster_switches_at_the_handoff_not_midnight():
    ctx = _ctx("09:00", "09:00")
    assert on_call_at(ctx, _at(22, 8, 59)) == A  # Monday's shift runs until 09:00
    assert on_call_at(ctx, _at(22, 9, 1)) == B
    assert on_call_at(ctx, _at(22, 23, 59)) == B
    assert on_call_at(ctx, _at(23, 0, 1)) == B  # midnight is not a handoff
    assert on_call_at(ctx, _at(23, 9, 0)) == A


def test_r01_midnight_handoff_still_switches_at_midnight():
    ctx = _ctx("00:00", "00:00")
    assert on_call_at(ctx, _at(21, 23, 59)) == A
    assert on_call_at(ctx, _at(22, 0, 0)) == B


def test_r02_overnight_roster_belongs_to_the_evening_it_started():
    ctx = _ctx("18:00", "09:00")
    assert on_call_at(ctx, _at(21, 20, 0)) == A  # Monday evening
    assert on_call_at(ctx, _at(22, 3, 0)) == A  # still Monday's shift
    assert on_call_at(ctx, _at(22, 12, 0)) is None  # outside coverage
    assert on_call_at(ctx, _at(22, 18, 0)) == B


def test_r02_business_hours_roster_is_unchanged():
    ctx = _ctx("09:00", "17:00")
    assert on_call_at(ctx, _at(21, 12, 0)) == A
    assert on_call_at(ctx, _at(22, 12, 0)) == B
    assert on_call_at(ctx, _at(22, 8, 0)) is None


@pytest.mark.parametrize(
    "day, month",
    [(8, 3), (1, 11)],  # US spring-forward and fall-back days in 2026
)
def test_r03_dst_days_keep_the_local_handoff(day, month):
    ctx = _ctx("09:00", "09:00", tz="America/Chicago", anchor=date(2026, 3, 1))
    tz = "America/Chicago"
    before = on_call_at(ctx, _at(day, 8, 59, tz, month))
    after = on_call_at(ctx, _at(day, 9, 1, tz, month))
    assert before != after
    # The previous and next handoffs are exactly one shift away in local time.
    assert on_call_at(ctx, _at(day + 1, 8, 59, tz, month)) == after


def test_r03_weekly_roster_hands_over_on_the_start_dates_weekday():
    ctx = _ctx("09:00", "09:00", pattern="weekly", anchor=MONDAY)
    assert on_call_at(ctx, _at(28, 8, 59)) == A  # next Monday, before handoff
    assert on_call_at(ctx, _at(28, 9, 1)) == B


def test_r04_edges_are_deterministic():
    ctx = _ctx("09:00", "09:00")
    assert on_call_at(ctx, _at(1, 12, 0)) == A  # before the Start Date: position 0
    assert on_call_at(_ctx("09:00", "09:00", members=(C,)), _at(24, 3, 0)) == C
    # Removing a member recomputes the rotation over the remaining members.
    assert on_call_at(_ctx("09:00", "09:00", members=(A, C)), _at(22, 12, 0)) == C


# ── R04–R06 with the database: overrides and the shared loader ──────────────


@dataclass
class RosterFixture:
    roster_id: uuid.UUID
    team_id: uuid.UUID
    users: list[uuid.UUID]


async def _roster(app, *, start="18:00", end="09:00", tz="UTC") -> RosterFixture:
    team = await _make_team(app, name=f"oncall-{uuid.uuid4().hex[:6]}")
    users = [
        await _make_user(app, username=f"oc-{uuid.uuid4().hex[:6]}") for _ in range(2)
    ]
    async with app.state.session_factory() as db:
        roster = await RosterRepo.create(
            db,
            TEST_ORG_ID,
            team_id=team,
            name="night",
            time_zone=tz,
            pattern="daily",
            pattern_length=1,
            coverage_start_time=start,
            coverage_end_time=end,
            handoff_time=start,
            anchor_date=MONDAY,
        )
        for index, user in enumerate(users):
            await RosterRepo.add_member(
                db, TEST_ORG_ID, roster_id=roster.id, user_id=user, position_index=index
            )
        await db.commit()
        return RosterFixture(roster.id, team, users)


async def _roster_chain(app, fixture: RosterFixture, *, extra_user_level=None):
    async with app.state.session_factory() as db:
        chain = await EscalationChainRepo.create(
            db, TEST_ORG_ID, team_id=fixture.team_id, name=f"c-{uuid.uuid4().hex[:6]}"
        )
        await EscalationStepRepo.create(
            db,
            TEST_ORG_ID,
            chain_id=chain.id,
            step_index=0,
            target_type="roster",
            target_id=fixture.roster_id,
            timeout_seconds=300,
        )
        if extra_user_level is not None:
            await EscalationStepRepo.create(
                db,
                TEST_ORG_ID,
                chain_id=chain.id,
                step_index=1,
                target_type="user",
                target_id=extra_user_level,
                timeout_seconds=300,
            )
        await db.commit()
        return chain.id


async def test_r05_engine_api_range_and_calendar_agree(app, client, auth_headers):
    fixture = await _roster(app)
    chain_id = await _roster_chain(app, fixture)
    at = _at(22, 3, 0)  # 03:00, inside an 18:00-09:00 window
    iso = at.isoformat().replace("+00:00", "Z")

    async with app.state.session_factory() as db:
        engine = await esc._resolve_step_targets(
            db,
            TEST_ORG_ID,
            target_type="roster",
            target_id=fixture.roster_id,
            at=at,
        )
    single = await client.get(
        f"/rosters/{fixture.roster_id}/on-call",
        params={"at": iso},
        headers=auth_headers,
    )
    ranged = await client.get(
        f"/rosters/{fixture.roster_id}/on-call/range",
        params={
            "from": iso,
            "to": iso.replace("03:00:00", "04:00:00"),
            "step_hours": 1,
        },
        headers=auth_headers,
    )
    calendar = await client.get(
        f"/escalation-chains/{chain_id}/calendar",
        params={"range": "today", "start": "2026-09-22", "at": iso},
        headers=auth_headers,
    )
    assert single.status_code == ranged.status_code == calendar.status_code == 200
    expected = fixture.users[0]
    assert engine == [expected]  # before this change the engine paged nobody
    assert single.json()["user_id"] == str(expected)
    assert ranged.json()["items"][0]["user_id"] == str(expected)
    level = calendar.json()["days"][0]["levels"][0]
    assert level["resolved_user_id"] == str(expected)


async def test_r05_calendar_at_needs_a_single_day(app, client, auth_headers):
    fixture = await _roster(app)
    chain_id = await _roster_chain(app, fixture)
    resp = await client.get(
        f"/escalation-chains/{chain_id}/calendar",
        params={"range": "7d", "at": "2026-09-22T03:00:00Z"},
        headers=auth_headers,
    )
    assert resp.status_code == 422


async def test_r04_overrides_for_inactive_users_are_ignored_everywhere(
    app, client, auth_headers
):
    fixture = await _roster(app)
    deputy = await _make_user(app, username=f"deputy-{uuid.uuid4().hex[:6]}")
    async with app.state.session_factory() as db:
        await RosterOverrideRepo.create(
            db,
            TEST_ORG_ID,
            roster_id=fixture.roster_id,
            covering_user_id=deputy,
            starts_at=_at(21, 18, 0),
            ends_at=_at(22, 9, 0),
        )
        await db.commit()
    iso = "2026-09-22T03:00:00Z"
    covered = await client.get(
        f"/rosters/{fixture.roster_id}/on-call",
        params={"at": iso},
        headers=auth_headers,
    )
    assert covered.json()["user_id"] == str(deputy)
    async with app.state.session_factory() as db:
        (await UserRepo.get_by_id(db, deputy)).is_active = False
        await db.commit()
    fallback = await client.get(
        f"/rosters/{fixture.roster_id}/on-call",
        params={"at": iso},
        headers=auth_headers,
    )
    ranged = await client.get(
        f"/rosters/{fixture.roster_id}/on-call/range",
        params={
            "from": iso,
            "to": iso.replace("03:00:00", "04:00:00"),
            "step_hours": 1,
        },
        headers=auth_headers,
    )
    assert fallback.json()["user_id"] == str(fixture.users[0])
    item = ranged.json()["items"][0]
    assert item["user_id"] == str(fixture.users[0])
    assert item["is_override"] is False


async def test_r04_overlapping_overrides_earliest_start_wins(app, client, auth_headers):
    fixture = await _roster(app)
    first = await _make_user(app, username=f"first-{uuid.uuid4().hex[:6]}")
    second = await _make_user(app, username=f"second-{uuid.uuid4().hex[:6]}")
    async with app.state.session_factory() as db:
        for user, start in ((second, _at(22, 1, 0)), (first, _at(21, 20, 0))):
            await RosterOverrideRepo.create(
                db,
                TEST_ORG_ID,
                roster_id=fixture.roster_id,
                covering_user_id=user,
                starts_at=start,
                ends_at=_at(22, 12, 0),  # also spans the 09:00 handoff
            )
        await db.commit()
    for iso in ("2026-09-22T03:00:00Z", "2026-09-22T10:00:00Z"):
        resp = await client.get(
            f"/rosters/{fixture.roster_id}/on-call",
            params={"at": iso},
            headers=auth_headers,
        )
        assert resp.json()["user_id"] == str(first)


async def test_r06_a_fired_level_is_not_re_resolved(app):
    fixture = await _roster(app)
    chain_id = await _roster_chain(app, fixture)
    deputy = await _make_user(app, username=f"late-{uuid.uuid4().hex[:6]}")
    async with app.state.session_factory() as db:
        incident = await IncidentRepo.create(
            db, TEST_ORG_ID, title="r06", description="d"
        )
        await esc.start_chain(
            db,
            TEST_ORG_ID,
            incident_id=incident.id,
            chain_id=chain_id,
            at=_at(22, 3, 0),
        )
        await RosterOverrideRepo.create(
            db,
            TEST_ORG_ID,
            roster_id=fixture.roster_id,
            covering_user_id=deputy,
            starts_at=_at(22, 3, 1),
            ends_at=_at(22, 9, 0),
        )
        await esc.tick(db, TEST_ORG_ID, incident_id=incident.id, at=_at(22, 3, 2))
        await db.commit()
        pages = (
            (
                await db.execute(
                    select(IncidentPage.user_id).where(
                        IncidentPage.incident_id == incident.id,
                        IncidentPage.channel == "recorded",
                    )
                )
            )
            .scalars()
            .all()
        )
    assert list(pages) == [fixture.users[0]]


# ── R07: D-4 quiet hours ────────────────────────────────────────────────────


class _Email:
    key = "email"

    def __init__(self):
        self.sent: list[str] = []

    async def send(self, *, recipient, subject, body, blocks=None):
        self.sent.append(recipient)
        return DeliveryAttempt("email", "sent")


async def _quiet(app, user_id):
    async with app.state.session_factory() as db:
        await UserNotificationPrefRepo.upsert(
            db,
            TEST_ORG_ID,
            user_id,
            routing={"P1": ["email"]},
            quiet_hours={
                "weekday_start": "00:00",
                "weekday_end": "23:59",
                "time_zone": "UTC",
                "min_priority_to_break": "P0",
            },
            quiet_hours_provided=True,
        )
        await db.commit()


async def _fire(app, chain_id, at):
    channel = _Email()
    async with app.state.session_factory() as db:
        incident = await IncidentRepo.create(
            db, TEST_ORG_ID, title="d4", description="d"
        )
        incident.priority = "P1"
        incident.response_mode = "page"
        await db.flush()
        await esc.start_chain(
            db,
            TEST_ORG_ID,
            incident_id=incident.id,
            chain_id=chain_id,
            at=at,
            channel_factory=lambda key: channel if key == "email" else None,
        )
        await db.commit()
        rows = (
            (
                await db.execute(
                    select(IncidentPage).where(IncidentPage.incident_id == incident.id)
                )
            )
            .scalars()
            .all()
        )
        notes = (
            (
                await db.execute(
                    select(IncidentComment.body).where(
                        IncidentComment.incident_id == incident.id
                    )
                )
            )
            .scalars()
            .all()
        )
    return channel, rows, notes


async def test_r07_quiet_hours_never_block_the_roster_on_call_person(app):
    fixture = await _roster(app, start="00:00", end="00:00")
    await _quiet(app, fixture.users[0])
    chain_id = await _roster_chain(app, fixture)
    channel, rows, _ = await _fire(app, chain_id, _at(21, 12, 0))
    assert len(channel.sent) == 1
    assert [r.delivery_status for r in rows if r.channel == "email"] == ["sent"]


async def test_r07_quiet_hours_block_a_direct_user_page_and_say_so(app):
    team = await _make_team(app, name=f"direct-{uuid.uuid4().hex[:6]}")
    quiet_user = await _make_user(app, username=f"quiet-{uuid.uuid4().hex[:6]}")
    await _quiet(app, quiet_user)
    async with app.state.session_factory() as db:
        chain = await EscalationChainRepo.create(
            db, TEST_ORG_ID, team_id=team, name="direct"
        )
        await EscalationStepRepo.create(
            db,
            TEST_ORG_ID,
            chain_id=chain.id,
            step_index=0,
            target_type="user",
            target_id=quiet_user,
            timeout_seconds=300,
        )
        await db.commit()
    channel, rows, notes = await _fire(app, chain.id, _at(21, 12, 0))
    assert channel.sent == []
    skipped = [r for r in rows if r.channel == "suppressed"]
    assert [(r.delivery_status, r.delivery_error) for r in skipped] == [
        ("skipped", "quiet_hours")
    ]
    assert any("reached nobody" in n and "quiet hours" in n for n in notes)


async def test_r07_maintenance_window_suppression_is_recorded(app):
    fixture = await _roster(app, start="00:00", end="00:00")
    chain_id = await _roster_chain(app, fixture)
    async with app.state.session_factory() as db:
        await MaintenanceWindowRepo.create(
            db,
            TEST_ORG_ID,
            name="planned",
            starts_at=_at(21, 11, 0),
            ends_at=_at(21, 13, 0),
        )
        await db.commit()
    channel, rows, notes = await _fire(app, chain_id, _at(21, 12, 0))
    assert channel.sent == []
    assert [r.delivery_error for r in rows if r.channel == "suppressed"] == [
        "maintenance_window"
    ]
    assert any("maintenance window" in n for n in notes)


# ── R08: notification preferences ───────────────────────────────────────────

UI_QUIET = {
    "weekday_start": "22:00",
    "weekday_end": "07:00",
    "days": [0, 1, 2, 3, 4],
    "min_priority_to_break": "P0",
    "time_zone": "America/Chicago",
}
UI_ROUTING = {"P0": [{"channel_id": "email", "delay_seconds": 300}], "P1": []}


async def test_r08_the_ui_payload_is_accepted(client, auth_headers):
    resp = await client.put(
        "/users/me/notification-preferences",
        json={"channels": {}, "routing": UI_ROUTING, "quiet_hours": UI_QUIET},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    legacy = await client.put(
        "/users/me/notification-preferences",
        json={"routing": {"P1": ["email", "sms"]}, "quiet_hours": None},
        headers=auth_headers,
    )
    assert legacy.status_code == 200, legacy.text


@pytest.mark.parametrize(
    "payload",
    [
        {"quiet_hours": {**UI_QUIET, "weekday_start": "24:00"}},
        {"quiet_hours": {**UI_QUIET, "days": [7]}},
        {"quiet_hours": {**UI_QUIET, "time_zone": "Mars/Olympus"}},
        {"quiet_hours": {**UI_QUIET, "min_priority_to_break": "P9"}},
        {"quiet_hours": {"weekday_start": "22:00"}},
        {"routing": {"P7": []}},
        {"routing": {"P1": [{"channel_id": "", "delay_seconds": 60}]}},
        {"routing": {"P1": [{"channel_id": "x", "delay_seconds": -1}]}},
        {"routing": {"P1": ["a", "b", "c", "d"]}},
        {"routing": {"P1": [42]}},
    ],
)
async def test_r08_malformed_preferences_are_rejected(client, auth_headers, payload):
    resp = await client.put(
        "/users/me/notification-preferences", json=payload, headers=auth_headers
    )
    assert resp.status_code == 422, resp.text


async def test_r08_saving_paging_routing_keeps_inbox_mutes(client, auth_headers):
    muted = await client.put(
        "/notifications/preferences",
        json={"muted_categories": ["incident"]},
        headers=auth_headers,
    )
    assert muted.status_code in (200, 204), muted.text
    saved = await client.put(
        "/users/me/notification-preferences",
        json={"routing": UI_ROUTING},
        headers=auth_headers,
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["routing"]["in_app"]["muted_categories"] == ["incident"]
    prefs = await client.get("/notifications/preferences", headers=auth_headers)
    assert prefs.json()["muted_categories"] == ["incident"]


def test_r04_before_start_date_documented_as_position_zero():
    ctx = _ctx("18:00", "09:00", anchor=date(2026, 12, 1))
    assert on_call_at(ctx, datetime(2026, 9, 22, 3, 0, tzinfo=timezone.utc)) == A
    assert (
        on_call_at(
            ctx, datetime(2026, 9, 22, 3, 0, tzinfo=timezone.utc) + timedelta(days=1)
        )
        == A
    )
